#!/usr/bin/env python3

import argparse
import csv
import json
import logging
import math
import multiprocessing
import os
import time
import traceback
from collections import OrderedDict, defaultdict, deque
from datetime import datetime
from queue import Empty

from .gitutils import Repo, paths_changed
from .testdata import TestData


suites = ["crashtest", "reftest", "mochitest", "web-platform-tests", "web-platform-tests-meta"]


def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("gecko_root", help="Path to gecko root")
    parser.add_argument("--rebuild", action="store_true", help="Don't use existing data")
    parser.add_argument("--processes", action="store", type=int, default=4,
                        help="Number of processes to use")
    parser.add_argument("out_path", type=os.path.abspath, help="Path to write output")
    return parser


def setup_logging():
    fmt_str = "[%(asctime)s] %(processName)s:%(levelname)s:%(message)s"
    formatter = logging.Formatter(fmt=fmt_str, style="%")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    debug_handler = logging.FileHandler("debug.log")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    root_logger.addHandler(debug_handler)


setup_logging()


class BugCommits():
    def __init__(self, date):
        self.commits = []
        self.shas = set()
        self.date = date

    def append(self, commit):
        assert commit.sha1 not in self.shas
        self.commits.append(commit)
        self.shas.add(commit.sha1)


def is_relevant_commit(commit):
    return (not (commit.is_backed_out or
                 commit.is_merge or
                 commit.is_wpt_sync) and
            commit.bug_numbers)


def get_commits_by_bug(gecko_root):
    logging.info("Reading commits")
    repo = Repo(gecko_root)

    backed_out = set()
    commits_by_bug = OrderedDict()
    seen = set()

    last_trustworthy_date = None
    min_date = datetime(2019, 1, 1)

    queue = deque([repo.lookup("mozilla/central")])
    while True:
        commit = queue.popleft()

        if commit.sha1 in seen:
            continue

        logging.debug("Commit %s - %s", commit.sha1, commit.msg.split(b"\n", 1)[0].decode("utf-8"))
        seen.add(commit.sha1)

        if backed_out:
            hg_sha = commit.hg_sha

            if hg_sha:
                found = None
                for sha in backed_out:
                    if hg_sha.startswith(sha):
                        found = sha
                        break
                if found is not None:
                    logging.debug("Commit was backed out")
                    backed_out.remove(sha)
                    commit.is_backed_out = True

        hg_backout_shas, _ = commit.commits_backed_out()
        if hg_backout_shas:
            logging.debug("Commit backs out %s" % ",".join(hg_backout_shas))
        backed_out |= set(hg_backout_shas)

        if not hg_backout_shas and is_relevant_commit(commit):
            logging.debug("Adding commit")
            bug_number = commit.bug_numbers[0]
            if bug_number not in commits_by_bug:
                commits_by_bug[bug_number] = BugCommits(last_trustworthy_date)

            commits_by_bug[bug_number].append(commit)

        elif commit.is_merge or hg_backout_shas:
            date = datetime.utcfromtimestamp(commit.commit.commit_time)
            logging.debug("Using commit date %s" % date)
            if last_trustworthy_date is None:
                for item in commits_by_bug.values():
                    if item.date is None:
                        item.date = date
            last_trustworthy_date = date
            if last_trustworthy_date < min_date:
                break

        for parent in commit.parents:
            if parent.sha1 not in seen:
                queue.append(parent)

    return commits_by_bug


def group_commits(commits):
    group = []
    for commit in commits:
        if not group or group[-1].parents[0] == commit:
            group.append(commit)
        else:
            yield group
            group = []
    if group:
        yield group


def maybe_test_paths(diff_paths):
    skip_exts = {"h", "cpp", "rs"}
    rv = {
        "A": set(),
        "M": set(),
        "D": set()
    }
    for path, (status, obj) in diff_paths.items():
        if "." not in path or path.rsplit(".", 1)[1] not in skip_exts:
            rv[status].add(path)
    return rv


def get_suites_changes(repo_path, by_bug_queue, result_queue, progress=None):
    test_data = None
    commit_head = None
    commit_parent = None

    repo = Repo(repo_path)

    if progress is not None:
        progress.start()

    try:
        while True:
            maybe_data = by_bug_queue.get()
            if maybe_data is None:
                logging.info("Process finished; no more bugs")
                result_queue.put(None)
                return

            bug, date, commit_shas = maybe_data

            logging.debug("Processing bug %s" % bug)
            commits = [repo.lookup(sha) for sha in commit_shas]

            changed = {"A": set(), "M": set()}
            for commit_range in group_commits(commits):
                logging.debug("Using commits %s" % " ".join(item.sha1 for item in commits))
                commit_head = commit_range[0]
                if test_data is None:
                    test_data = TestData(commit_head)
                else:
                    test_data.update(commit_head)

                commit_parent = commit_range[-1].parents[0]

                diff_paths = maybe_test_paths(paths_changed(commit_head, commit_parent))
                if any(value for value in diff_paths.values()):
                    for change_type, suites in test_data.changes(diff_paths,
                                                                 changed).items():
                        changed[change_type] |= suites
                else:
                    logging.debug("No possible test changes")

            result_queue.put((date, bug, changed))

            if progress is not None:
                progress.done()
    except Exception:
        logging.critical("Subprocess had an exception:\n%s", traceback.format_exc())
        raise


def get_test_changes(repo_path, commits_by_bug, seen_bugs, by_bug_file, by_month_file,
                     num_processes=4):
    by_bug_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()

    progress = ProgressMeter(len(commits_by_bug))

    for bug_number, commits in commits_by_bug.items():
        if bug_number in seen_bugs:
            timestamp, bug_number, changed = seen_bugs[bug_number]
            date = datetime.utcfromtimestamp(timestamp)
            result_queue.put((date, bug_number, changed))
        else:
            progress.queue_bug()
            by_bug_queue.put((bug_number,
                              commits.date,
                              [commit.sha1 for commit in commits.commits]))

    processes = None
    if num_processes > 1:
        processes = [multiprocessing.Process(target=get_suites_changes,
                                             args=(repo_path, by_bug_queue, result_queue))
                     for i in range(num_processes)]
        for proc in processes:
            by_bug_queue.put(None)
            proc.start()
    else:
        get_suites_changes(repo_path, by_bug_queue, result_queue, progress=progress)
        progress = None

    headings, by_month = get_by_month()
    all_data = []

    try:
        handle_results(processes, result_queue, progress, all_data, by_month)
    finally:
        if processes is not None:
            for proc in processes:
                proc.join(2)
                if proc.is_alive():
                    proc.terminate()
            by_bug_queue.close()
            result_queue.close()

        with open(by_bug_file, "w") as f:
            json.dump(all_data, f)

        with open(by_month_file, "w") as f:
            field_names = ["month"] + headings
            writer = csv.DictWriter(f, field_names)
            writer.writeheader()
            for month, data in by_month.items():
                data["month"] = month
                writer.writerow(data)


def get_by_month():
    headings = []
    for status in ["added", "modified", "total"]:
        for suite in suites:
            headings.append("%s-%s" % (suite, status))

    headings.extend(["total-added", "total-modified", "test-total", "total"])
    by_month = defaultdict(lambda: {heading: 0 for heading in headings})
    return headings, by_month


class ProgressMeter:
    def __init__(self, total_bugs):
        self.total_bugs = total_bugs
        self.last_percent_done = 0
        self.queued_bugs = 0
        self.t0 = None
        self.cache_count = None
        self.processed_count = 0

    def start(self):
        self.cache_count = self.total_bugs - self.queued_bugs
        self.t0 = time.time()
        logging.info("Processing %i bugs, %i in cache, %i from source" % (self.total_bugs,
                                                                          self.cache_count,
                                                                          self.queued_bugs))

    def queue_bug(self):
        assert self.t0 is None
        self.queued_bugs += 1

    def done(self):
        assert self.t0 is not None
        self.processed_count += 1
        fraction_done = (self.processed_count - self.cache_count) / self.queued_bugs
        int_percent_done = math.floor(100 * fraction_done)
        if int_percent_done > self.last_percent_done:
            time_passed = time.time() - self.t0
            total_estimate = time_passed / fraction_done
            time_remaining = total_estimate - time_passed
            logging.info("Done: %d%% Remaining estimate: %.0ds", int_percent_done, time_remaining)
            self.last_percent_done = int_percent_done


def handle_results(processes, result_queue, progress, all_data, by_month):
    num_processes = len(processes) if processes is not None else 1
    finished_proc_count = 0

    status_names = {"A": "added", "M": "modified"}

    if progress is not None:
        progress.start()

    while True:
        try:
            maybe_data = result_queue.get(True, timeout=10)
        except Empty:
            if num_processes > 1 and not any(process.is_alive() for process in processes):
                break

        if maybe_data is None:
            finished_proc_count += 1
            if finished_proc_count == num_processes:
                break
            continue

        date, bug_number, changed = maybe_data

        month_str = date.strftime("%Y-%m")
        by_month[month_str]["total"] += 1

        json_safe_changed = {}
        all_suites_changed = set()
        for status, suites_changed in changed.items():
            status_name = status_names[status]
            if suites_changed:
                json_safe_changed[status] = list(sorted(suites_changed))
                by_month[month_str]["total-%s" % status_name] += 1
                for suite in suites_changed:
                    by_month[month_str]["%s-%s" % (suite, status_name)] += 1
                    all_suites_changed.add(suite)

        for suite in all_suites_changed:
            by_month[month_str]["%s-total" % (suite,)] += 1

        if all_suites_changed and all_suites_changed != {"web-platform-tests-meta"}:
            by_month[month_str]["test-total"] += 1

        all_data.append((date.timestamp(), bug_number, json_safe_changed))

        if progress is not None:
            progress.done()


def run():
    parser = get_parser()
    args = parser.parse_args()

    by_bug_file = os.path.join(args.out_path, "by_bug.json")
    by_month_file = os.path.join(args.out_path, "by_month.csv")

    seen_bugs = {}
    if not args.rebuild and os.path.exists(by_bug_file):
        with open(by_bug_file) as f:
            try:
                for item in json.load(f):
                    seen_bugs[item[1]] = tuple(item)
            except ValueError:
                logging.warn("Loading cached data failed, rebuilding")

    commits_by_bug = get_commits_by_bug(args.gecko_root)

    get_test_changes(args.gecko_root,
                     commits_by_bug,
                     seen_bugs,
                     by_bug_file,
                     by_month_file,
                     args.processes)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        import pdb
        pdb.post_mortem()
