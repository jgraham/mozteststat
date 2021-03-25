import re
import subprocess

import pygit2
from mozautomation import commitparser

wpt_sync_re = re.compile(rb".*(?:\[wpt PR \d+\]|Update web-platform-tests to [0-9a-fA-F]{40})")

def iter_tree(tree, names=None):
    stack = [(tree, [])]
    while stack:
        obj, path = stack.pop()
        for item in obj:
            if isinstance(item, pygit2.Tree):
                new_path = path[:]
                new_path.append(item.name)
                stack.append((item, new_path))
            else:
                if names is None or item.name in names:
                    file_path = path[:]
                    file_path.append(item.name)
                    str_path = "/".join(file_path)
                    yield str_path, item


def paths_changed(commit, parent):
    stack = [(commit.tree, parent.tree, "")]

    diffs = {}

    while stack:
        commit_tree, parent_tree, path = stack.pop()
        if (commit_tree is not None and
            parent_tree is not None and
            commit_tree.id == parent_tree.id):
            continue

        if commit_tree is not None:
            for item in commit_tree:
                name = item.name
                item_path = "%s/%s" % (path, name) if path else name
                if isinstance(item, pygit2.Tree):
                    stack.append((item,
                                  parent_tree[name] if (parent_tree is not None and
                                                        name in parent_tree and
                                                        isinstance(parent_tree[name],
                                                                   pygit2.Tree)) else None,
                                  item_path))
                else:
                    if parent_tree is None or name not in parent_tree:
                        diffs[item_path] = ("A", item)
                    elif item.id != parent_tree[name].id:
                        diffs[item_path] = ("M", item)

        if parent_tree is not None:
            for item in parent_tree:
                name = item.name
                if commit_tree is None or name not in commit_tree:
                    item_path = "%s/%s" % (path, name) if path else name
                    if isinstance(item, pygit2.Tree):
                        stack.append((None, item, item_path))
                    else:
                        diffs[item_path] = ("D", None)

    return diffs


class Repo():
    def __init__(self, path):
        self._commit_cache = {}
        self.repo = pygit2.Repository(path)
        self._cinnabar_notes = None

    def lookup(self, rev):
        pygit2_commit = self.repo.revparse_single(rev)
        return Commit(self, pygit2_commit)

    @property
    def cinnabar_notes(self):
        if self._cinnabar_notes is None:
            self._cinnabar_notes = self.repo.revparse_single("refs/notes/cinnabar")
        return self._cinnabar_notes

    @property
    def workdir(self):
        return self.repo.workdir

    def git(self, *args):
        args = ("git",) + args
        return subprocess.check_output(args, cwd=self.workdir)


class Commit():
    def __new__(cls, repo, commit):
        if commit.id not in repo._commit_cache:
            repo._commit_cache[commit.id] = super().__new__(cls)
        return repo._commit_cache[commit.id]

    def __init__(self, repo, commit):
        self.repo = repo
        self.commit = commit

        self.is_backed_out = False
        self.test_hash = None

        self._cinnabar_data = None

    @property
    def sha1(self):
        return str(self.commit.id)

    @property
    def hg_sha(self):
        return self.cinnabar_data.get("changeset")

    @property
    def msg(self):
        # type: () -> bytes
        return self.commit.raw_message

    @property
    def is_backout(self):
        # type: () -> bool
        return commitparser.is_backout(self.msg)

    @property
    def parents(self):
        return [self.repo.lookup(str(parent.id)) for parent in self.commit.parents]

    @property
    def is_wpt_sync(self):
        return wpt_sync_re.match(self.msg)

    @property
    def tree(self):
        return self.commit.tree

    @property
    def cinnabar_data(self):
        if self._cinnabar_data is None:
            cinnabar_tree = self.repo.cinnabar_notes.tree
            sha1 = self.sha1
            parts = (sha1[:2], sha1[2:4], sha1[4:])
            obj = cinnabar_tree
            for part in parts:
                try:
                    obj = obj[part]
                except KeyError:
                    return {}
            assert isinstance(obj, pygit2.Blob)
            data = obj.read_raw()
            cinnabar_data = {}
            for line in data.split(b"\n"):
                parts = line.split(b" ", 1)
                if parts[0] in (b"changeset", b"manifest"):
                    cinnabar_data[parts[0].decode("ascii")] = parts[1].decode("ascii")
                elif parts[0] == b"extra":
                    continue
                else:
                    cinnabar_data[parts[0].decode("ascii")] = [item.decode("utf-8")
                                                               for item in parts[1].split(b"\0")]
            self._cinnabar_data = cinnabar_data
        return self._cinnabar_data

    def commits_backed_out(self):
        commits = []
        bugs = []
        if self.is_backout:
            nodes_bugs = commitparser.parse_backouts(self.msg)
            if nodes_bugs is None:
                # We think this a backout, but have no idea what it backs out
                # it's not clear how to handle that case so for now we pretend it isn't
                # a backout
                return commits, set(bugs)

            nodes, bugs = nodes_bugs
            # Assuming that all commits are listed.
            for node in nodes:
                commits.append(node.decode("ascii"))

        return commits, set(bugs)

    @property
    def is_merge(self):
        return len(self.commit.parents) > 1

    @property
    def bug_numbers(self):
        return commitparser.parse_bugs(self.msg)
