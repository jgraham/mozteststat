import logging
import os
import re
from collections import deque

from .cache import path_cache

reftest_re = re.compile("(?:^| )(url-prefix|include|load|==|!=|print) ([^ ]*)(?: ([^ ]*))?")


def parse_reftest_file(prefix, name):
    if ":" in name:
        return None
    name = name.split("#", 1)[0]
    name = name.split("?", 1)[0]
    if prefix is not None:
        name = prefix + name
    return name


@path_cache
def read_reftest_list(path, obj):
    logging.debug("Reading %s for %s" % (path, obj.id))

    file_data = obj.read_raw().decode("utf8")

    includes = []
    tests = []
    files = set()

    url_prefix = None

    for line in file_data.split("\n"):
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        match = reftest_re.match(line)

        if match is None:
            continue

        line_type, url, url_ref = match.groups()
        if line_type == "include":
            includes.append(url)
        elif line_type == "==" or line_type == "!=" or line_type == "print":
            for name in [url, url_ref]:
                filename = parse_reftest_file(url_prefix, name)
                if filename:
                    files.add(filename)
                tests.append(name)
        elif line_type == "load":
            filename = parse_reftest_file(url_prefix, url)
            if filename:
                files.add(filename)
            tests.append(url)
        elif line_type == "url-prefix":
            url_prefix = url

    return {"includes": includes,
            "tests": tests,
            "files": files}


class ReftestData:
    def __init__(self, commit, manifest_paths):
        self.manifest_paths = manifest_paths
        self._included_paths = set()
        self._test_count = 0
        self._test_paths = set()

    def _update(self, commit, path_cache):
        queue = deque(self.manifest_paths)

        test_count = 0
        paths = set()
        included_paths = set()

        while queue:
            path = queue.popleft()
            path_prefix = path.rsplit("/", 1)[0] + "/"

            obj = path_cache.get(path, commit.tree)

            file_data = read_reftest_list(path, obj)
            for item in file_data["includes"]:
                include_path = os.path.normpath(path_prefix + item)
                queue.append(include_path)
                included_paths.add(include_path)

            test_count += len(file_data["tests"])
            for rel_path in file_data["files"]:
                paths.add(path_prefix + rel_path)

        self._included_paths = included_paths
        self._test_count = test_count
        self._test_paths = paths

    def update(self, new_commit, path_changes, path_cache):
        has_updates = False
        if any(path in self.manifest_paths or path in self._included_paths
               for path in path_changes.keys()):
            has_updates = True
            self._update(new_commit, path_cache)

        return has_updates

    def get_data(self):
        return self._test_count, self._test_paths


class ReftestMatcher:
    def __init__(self, paths, manifest_paths):
        parts = []
        self.manifest_paths = manifest_paths
        for path in sorted(paths):
            dir_name = path.rsplit("/", 1)[0]
            if not (parts and dir_name.startswith(parts[-1])):
                parts.append(dir_name)
        self.regex = re.compile("|".join(parts))

    def __call__(self, changed_paths):
        return any(path not in self.manifest_paths and self.regex.match(path)
                   for path in changed_paths)
