import logging
import re

from .cache import path_cache

mochitest_line = re.compile("( *)([^ ]*)")

mochitest_ini_cache = {}


@path_cache
def read_mochitest_ini(path, obj):
    logging.debug("Reading %s for %s" % (path, obj.id))

    file_data = obj.read_raw().decode("utf8")

    key = None
    value = None

    section = None
    key_indent = None
    data = {}

    for line in file_data.split("\n"):
        line = line.strip()

        if not line:
            key = value = None
            continue

        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        if len(line) > 2 and line[0] == "[" and line[-1] == "]":
            section = line[1:-1]
            if section in data:
                logging.warn("Error parsing mochitest ini: Duplicate section %s" % section)
                raise ValueError
            data[section] = {}
            continue

        if section is None:
            logging.warn("Error parsing mochitest ini: No section heading")
            raise ValueError

        indent, line_data = mochitest_line.match(line).groups()
        line_indent = len(indent)
        if key and line_indent > key_indent:
            data[section][key] += ("\n" + line_data)

        for separator in (":", "="):
            if separator in line_data:
                key, value = line_data.split(separator, 1)
                key = key.strip()
                value = value.strip()
                key_indent = line_indent

                if key in data[section]:
                    logging.warn("Error parsing mochitest ini: Duplicate key %s" % key)
                    raise ValueError
                data[section][key] = value
                break

    return data


def mochitest_paths(commit, mochitest_manifests):
    test_count = 0
    paths = set()
    for path in mochitest_manifests:
        path_prefix = path.rsplit("/", 1)[0] + "/"

        obj = commit.tree[path]
        ini_data = read_mochitest_ini(path, obj)

        for section, values in ini_data.items():
            if section == "DEFAULT":
                support_files = values.get("support-files", "").split("\n")
                for item in support_files:
                    paths.add(path_prefix + item)
            else:
                test_count += 1
                paths.add(path_prefix + section)
    return test_count, paths


class MochitestData:
    def __init__(self, commit, manifest_paths):
        self.manifest_paths = manifest_paths
        self._test_count = 0
        self._paths = set()

    def _update_data(self, commit, path_cache):
        test_count = 0
        paths = set()
        for path in self.manifest_paths:
            path_prefix = path.rsplit("/", 1)[0] + "/"

            obj = path_cache.get(path, commit.tree)
            try:
                ini_data = read_mochitest_ini(path, obj)
            except ValueError:
                # TODO: not sure how to handle this
                continue

            for section, values in ini_data.items():
                if section == "DEFAULT":
                    support_files = values.get("support-files", "").split("\n")
                    for item in support_files:
                        paths.add(path_prefix + item)
                else:
                    test_count += 1
                    paths.add(path_prefix + section)
        self._test_count = test_count
        self._paths = paths

    def update(self, new_commit, path_changes, path_cache):
        has_updates = False
        if not self.manifest_paths.isdisjoint(path_changes):
            has_updates = True
            self._update_data(new_commit, path_cache)

        return has_updates

    def get_data(self):
        return self._test_count, self._paths


class MochitestMatcher:
    def __init__(self, paths):
        self.paths = paths

    def __call__(self, changed_paths):
        return any(item in self.paths for item in changed_paths)
