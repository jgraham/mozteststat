import io
import logging
import re
import os
import tokenize
from collections import defaultdict

from .cache import path_cache
from .mochitest import MochitestData
from .reftest import ReftestData

manifest_types = {"REFTEST_MANIFESTS": "reftest",
                  "MOCHITEST_MANIFESTS": "mochitest",
                  "CRASHTEST_MANIFESTS": "crashtest"}

manifest_re = re.compile(b"|".join(item.encode("ascii") for item in manifest_types.keys()))


@path_cache
def read_mozbuild(path, obj):
    logging.debug("Reading %s for %s" % (path, obj.id))

    data = obj.read_raw()

    path_prefix = path.rsplit("/", 1)[0] + "/"

    if not manifest_re.search(data):
        entries = {}
        return entries

    data_buf = io.BytesIO(data)

    entries = defaultdict(list)

    state = None

    for tok_type, tok_value, _, _, _ in tokenize.tokenize(data_buf.readline):
        if state is None:
            if tok_type == tokenize.NAME and tok_value in manifest_types:
                state = (manifest_types[tok_value], "start")
        else:
            test_type, substate = state
            if substate == "start" and tok_type == tokenize.OP:
                assert tok_value == "+="
                state = (test_type, "after_op")
            elif substate == "after_op" and tok_type == tokenize.OP:
                assert tok_value == "["
                state = (test_type, "before_entry")
            elif substate == "before_entry":
                if tok_type == tokenize.STRING:
                    manifest_path = os.path.normpath(path_prefix + tok_value[1:-1])
                    entries[test_type].append(manifest_path)
                    state = (test_type, "after_entry")
                elif tok_type == tokenize.OP:
                    if tok_value == "]":
                        state = None
                    else:
                        assert False
                else:
                    assert tok_type == tokenize.NL
            elif substate == "after_entry":
                if tok_type == tokenize.OP:
                    if tok_value == ",":
                        state = (test_type, "before_entry")
                    elif tok_value == "]":
                        state = None
                    else:
                        assert False
                else:
                    assert tok_type == tokenize.NL

    return entries


class MozBuildData:
    def __init__(self, path):
        self.path = path
        self._by_type = {
            "mochitest": None,
            "reftest": None,
            "crashtest": None
        }
        self.suites = set()

    def update(self, commit, obj):
        updated_suites = set()
        paths = read_mozbuild(self.path, obj)

        new_suites = set()
        old_suites = self.suites

        for suite, manifest_paths in paths.items():
            manifest_paths = set(manifest_paths)
            existing = self._by_type[suite]
            new_suites.add(suite)
            if existing is None or existing.manifest_paths != manifest_paths:
                updated_suites.add(suite)
                if suite == "mochitest":
                    data = MochitestData(commit, manifest_paths)
                else:
                    data = ReftestData(commit, manifest_paths)
                self._by_type[suite] = data
        self.suites = new_suites

        removed_suites = old_suites - new_suites
        for suite in removed_suites:
            self._by_type[suite] = None
        updated_suites |= removed_suites

        return updated_suites

    def update_suites(self, new_commit, path_changes, path_cache):
        suites_with_updates = set()
        for suite, data in self._by_type.items():
            if data:
                has_updates = data.update(new_commit, path_changes, path_cache)
                if has_updates:
                    suites_with_updates.add(suite)
        if suites_with_updates:
            logging.debug("Paths changed in suites: %s" % " ".join(suites_with_updates))
        return suites_with_updates

    def get_manifest_paths(self, suite):
        suite_data = self._by_type[suite]
        if suite_data is None:
            return set()
        return suite_data.manifest_paths

    def get_data(self, suite):
        suite_data = self._by_type[suite]
        if suite_data is None:
            return 0, set()
        return suite_data.get_data()

    @classmethod
    def for_file(cls, commit, path, obj):
        rv = cls(path)
        rv.update(commit, obj)
        return rv
