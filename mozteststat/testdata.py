import logging

from .gitutils import iter_tree, paths_changed
from .mochitest import MochitestMatcher
from .mozbuild import MozBuildData
from .reftest import ReftestMatcher
from .wpt import has_wpt_changes, has_wpt_meta_changes


class PathCache:
    def __init__(self, names):
        self._data = {}
        self.names = names

    def match(self, name):
        return name in self.names

    def set(self, path, obj):
        self._data[path] = obj

    def remove(self, path):
        del self._data[path]

    def get(self, path, tree):
        rv = self._data.get(path)
        if rv is None:
            return tree[path]
        return rv


class TestData:
    def __init__(self, commit):
        self.commit = None
        self._data = {}
        self._tests_by_type = {}

        self.cache_names = {"mochitest.ini", "reftest.list", "crashtest.list"}
        self.mozbuild_names = {"moz.build"}

        self._path_cache = PathCache(self.cache_names)

        self._count_by_suite = {"reftest": 0,
                                "mochitest": 0,
                                "crashtest": 0}

        self._paths_by_suite = {"reftest": set(),
                                "mochitest": set(),
                                "crashtest": set()}

        self.matcher_by_suite = {
            "web-platform-tests": has_wpt_changes,
            "web-platform-tests-meta": has_wpt_meta_changes,
            "mochitest": None,
            "reftest": None,
            "crashtest": None,
        }

        self.update(commit)

    def update_mozbuild(self, new_commit, path, status, obj):
        if status == "D":
            old = self._data[path]
            del self._data[path]
            return old.suites

        if status == "A":
            self._data[path] = MozBuildData.for_file(new_commit, path, obj)
            return self._data[path].suites

        if status == "M":
            return self._data[path].update(new_commit, obj)

    def update(self, new_commit):
        prev_commit = self.commit
        if prev_commit is None:
            path_changes = {path: ("A", obj) for path, obj in iter_tree(new_commit.tree)}
        else:
            path_changes = paths_changed(new_commit, prev_commit)

        suites_with_updates = set()

        for path, (status, obj) in path_changes.items():
            name = path.rsplit("/", 1)[-1]
            if name == "moz.build":
                suites_with_updates |= self.update_mozbuild(new_commit, path, status, obj)
            if name in self.cache_names:
                if status == "D":
                    self._path_cache.remove(path)
                else:
                    self._path_cache.set(path, obj)

        if suites_with_updates:
            logging.debug("mozbuild changes updated %s" % suites_with_updates)

        for mozbuild_data in self._data.values():
            suites_with_updates |= mozbuild_data.update_suites(new_commit,
                                                               path_changes,
                                                               self._path_cache)

        for suite in suites_with_updates:
            count = 0
            paths = set()
            manifest_paths = set()
            for mozbuild_data in self._data.values():
                mozbuild_count, mozbuild_paths = mozbuild_data.get_data(suite)
                count += mozbuild_count
                paths |= mozbuild_paths
                if suite != "mochitest":
                    manifest_paths |= mozbuild_data.get_manifest_paths(suite)
            self._count_by_suite[suite] = count
            self._paths_by_suite[suite] = paths

            if suite == "mochitest":
                self.matcher_by_suite[suite] = MochitestMatcher(paths)
            else:
                self.matcher_by_suite[suite] = ReftestMatcher(paths, manifest_paths)

        self.commit = new_commit

    def changes(self, diff_paths, exclude=None):
        changes = {"A": set(),
                   "M": set()}

        for status, paths in diff_paths.items():
            if not paths or status == "D":
                continue
            for suite, matcher in self.matcher_by_suite.items():
                if exclude is not None and status in exclude and suite in exclude[status]:
                    continue
                if matcher is not None and matcher(paths):
                    changes[status].add(suite)

        return changes


def read_test_data(self, out_path, is_retry=False, include_wpt=None):
    import json
    import os
    import time
    import subprocess
    t0 = time.time()
    out_file = None
    if include_wpt is None:
        include_wpt = self.files_changed.includes_prefix("testing/web-platform/")
    if self.test_hash:
        out_file = os.path.join(out_path, "%s.json" % self.test_hash)

    if out_file is None or not os.path.exists(out_file):
        print("Reading tests for %s" % self.sha1)
        self.repo.git("checkout", self.sha1)
        try:
            out_data = subprocess.check_output(["./mach", "python", "-c",
                                                """
import json
import os
import hashlib

from moztest.resolve import TestResolver

include_wpt = %r

r = TestResolver.from_environment()
if include_wpt:
  r.add_wpt_manifest_data()

data = {}

for item in r.tests:
    flavor = item["flavor"]
    if flavor not in ["web-platform-tests", "mochitest", "reftest", "crashtest"]:
        continue
    if flavor not in data:
        data[flavor] = {}

    dir = item["dir_relpath"]
    path = os.path.relpath(item["file_relpath"], dir)
    support_files = [support for support in item.get("support-files", "").split("\\n") if support]
    if flavor == "reftest" and "referenced-test" in item:
        support_files.append(path)
        path = item["referenced-test"]

    subsuite = item.get("subsuite", "")
    if subsuite not in data[flavor]:
        data[flavor][subsuite] = ({}, set())

    if dir not in data[flavor][subsuite][0]:
        data[flavor][subsuite][0][dir] = []

    data[flavor][subsuite][0][dir].append(path)

    for support_path in support_files:
        data[flavor][subsuite][1].add(os.path.join(dir, support_path))

for flavor, flavor_data in data.items():
    for subsuite, subsuite_data in list(flavor_data.items()):
        flavor_data[subsuite] = [{key: sorted(value) for key, value in subsuite_data[0].items()},
                                 sorted(list(subsuite_data[1]))]

output = json.dumps(data, sort_keys=True).encode("utf8")

hash = hashlib.sha1(output).hexdigest()
out_path = os.path.join("%s", hash + ".json")

with open(out_path, "wb") as f:
    f.write(output)

print(hash)
""" % (include_wpt, out_path,)], cwd=self.repo.workdir)
        except subprocess.CalledProcessError:
            print("Getting test data failed with commit %s" % self.sha1)
            return
        print(out_data)
        test_hash = out_data.splitlines()[-1].decode("ascii")
        print("Got hash %s" % test_hash)
        self.test_hash = test_hash
        out_file = os.path.join(out_path, "%s.json" % self.test_hash)
    else:
        print("Using test cache for %s" % self.test_hash)

    try:
        with open(out_file) as f:
            data = TestData.from_json(json.load(f))
            self._test_data = data
            return data
    except Exception:
        os.unlink(out_file)
        if is_retry:
            raise
        return self.read_test_data(out_path, is_retry=True)
    finally:
        print("Took %.1ds" % (time.time() - t0))
