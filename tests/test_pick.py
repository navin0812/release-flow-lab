"""Tests for .github/scripts/pick.py. Stdlib only, real git, no network.

    python3 -m unittest discover -s tests -v

Each test builds a bare "origin" and a clone. main gets squash-style commits
(one per PR); branch `uat` starts at the first commit. PR data is passed in,
so `gh` is never called.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
import pick  # noqa: E402

ENV = {
    "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Bot", "GIT_COMMITTER_EMAIL": "bot@example.com",
}


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                       env={**os.environ, **ENV})
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


class PickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd0 = os.getcwd()
        self.addCleanup(os.chdir, self.cwd0)
        self.origin = os.path.join(self.tmp.name, "origin.git")
        self.work = os.path.join(self.tmp.name, "work")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True)
        subprocess.run(["git", "clone", "-q", self.origin, self.work], check=True, capture_output=True)
        git("config", "user.name", "Bot", cwd=self.work)
        git("config", "user.email", "bot@example.com", cwd=self.work)
        self.base = self.commit({"a.txt": "base\n"}, "base")
        git("push", "-q", "origin", "main", cwd=self.work)
        git("branch", "uat", cwd=self.work)
        git("push", "-q", "origin", "uat", cwd=self.work)
        self.prs = {}

    # -- helpers --
    def commit(self, files, msg):
        for name, text in files.items():
            path = os.path.join(self.work, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        git("add", "-A", cwd=self.work)
        git("commit", "-q", "-m", msg, cwd=self.work)
        return git("rev-parse", "HEAD", cwd=self.work)

    def merge_pr(self, number, files, title=None):
        """A squash merge to main: one commit, '(#n)' in the title."""
        title = title or f"PR {number}"
        sha = self.commit(files, f"{title} (#{number})")
        git("push", "-q", "origin", "main", cwd=self.work)
        self.prs[number] = {"title": title, "sha": sha}
        return sha

    def on_uat(self):
        git("switch", "-q", "uat", cwd=self.work)
        os.chdir(self.work)

    def log(self, ref="uat"):
        return git("log", "--format=%s", ref, cwd=self.work).splitlines()

    def remote_tip(self, branch="uat"):
        return git("rev-parse", branch, cwd=self.origin)

    def run_pick(self, **kw):
        self.on_uat()
        git("fetch", "-q", "origin", cwd=self.work)
        git("reset", "-q", "--hard", "origin/uat", cwd=self.work)  # like a fresh checkout of the branch
        return pick.execute("uat", self.prs, **kw)

    # -- tests --
    def test_picks_in_main_order_whatever_the_dict_order(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"f2.txt": "2\n"})
        self.merge_pr(3, {"f3.txt": "3\n"})
        self.prs = {3: self.prs[3], 1: self.prs[1], 2: self.prs[2]}
        picked = self.run_pick()
        self.assertEqual(picked, [1, 2, 3])
        self.assertEqual(self.log()[:3], ["PR 3 (#3)", "PR 2 (#2)", "PR 1 (#1)"])

    def test_pick_carries_x_trailer(self):
        sha = self.merge_pr(1, {"f1.txt": "1\n"})
        self.run_pick()
        body = git("log", "-1", "--format=%B", "uat", cwd=self.work)
        self.assertIn(f"(cherry picked from commit {sha})", body)

    def test_skips_pr_already_an_ancestor_of_the_branch(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        git("push", "-q", "origin", "main:uat", cwd=self.work)  # uat now holds PR 1 by history
        picked = self.run_pick()
        self.assertEqual(picked, [])

    def test_skips_pr_already_picked_with_x(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.run_pick()
        before = self.remote_tip()
        picked = self.run_pick()
        self.assertEqual(picked, [])
        self.assertEqual(self.remote_tip(), before)

    def test_exclude_skips_a_labelled_pr(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"f2.txt": "2\n"})
        picked = self.run_pick(exclude={2})
        self.assertEqual(picked, [1])
        self.assertNotIn("PR 2 (#2)", self.log())

    def test_exclude_of_unknown_number_fails(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        with self.assertRaises(SystemExit):
            self.run_pick(exclude={99})
        self.assertNotIn("PR 1 (#1)", self.log("origin/uat"))

    def test_conflict_stops_everything_and_pushes_nothing(self):
        # uat changes a.txt itself, so PR 2 (also a.txt) cannot apply.
        self.on_uat()
        self.commit({"a.txt": "uat edit\n"}, "uat only")
        git("push", "-q", "origin", "uat", cwd=self.work)
        git("switch", "-q", "main", cwd=self.work)
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"a.txt": "main edit\n"})
        before_remote, before_head = self.remote_tip(), None
        with self.assertRaises(SystemExit):
            self.run_pick()
        before_head = git("rev-parse", "HEAD", cwd=self.work)
        self.assertEqual(self.remote_tip(), before_remote)
        # PR 1 was picked locally before the conflict; it must not reach origin.
        self.assertNotIn("PR 1 (#1)", git("log", "--format=%s", "uat", cwd=self.origin).splitlines())
        # no half-finished cherry-pick left behind
        self.assertFalse(os.path.exists(os.path.join(self.work, ".git", "CHERRY_PICK_HEAD")))
        self.assertTrue(before_head)

    def test_empty_pick_counts_as_already_applied(self):
        # The same change reached uat by hand, without a -x trailer.
        self.on_uat()
        self.commit({"f1.txt": "1\n"}, "hand copy of PR 1")
        git("push", "-q", "origin", "uat", cwd=self.work)
        git("switch", "-q", "main", cwd=self.work)
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"f2.txt": "2\n"})
        picked = self.run_pick()
        self.assertEqual(picked, [2])

    def test_workflow_file_change_stops_before_push_and_names_the_pr(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {".github/workflows/ci.yaml": "name: ci\n"})
        before = self.remote_tip()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.run_pick()
        self.assertEqual(self.remote_tip(), before)
        self.assertIn("#2", out.getvalue())
        self.assertNotIn("#1", out.getvalue().split("Workflow files")[-1])

    def test_dry_run_pushes_nothing(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        before = self.remote_tip()
        picked = self.run_pick(dry_run=True)
        self.assertEqual(picked, [1])
        self.assertEqual(self.remote_tip(), before)

    def test_real_run_pushes_the_branch(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.run_pick()
        self.assertEqual(self.remote_tip(), git("rev-parse", "uat", cwd=self.work))
        self.assertIn("PR 1 (#1)", git("log", "--format=%s", "uat", cwd=self.origin).splitlines())

    def test_refuses_main(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        git("switch", "-q", "main", cwd=self.work)
        os.chdir(self.work)
        with self.assertRaises(SystemExit):
            pick.execute("main", self.prs)

    # -- drift --
    def drift_after_pick(self, labels):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_pick(dry_run=True, labels=labels)
        return out.getvalue()

    def test_drift_only_in_dry_run(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"f2.txt": "2\n"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_pick(exclude={2})
        self.assertNotIn("Drift from main", out.getvalue())

    def test_drift_lists_main_only_in_groups_by_label(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.merge_pr(2, {"f2.txt": "2\n"})
        self.merge_pr(3, {"f3.txt": "3\n"})
        self.merge_pr(4, {"f4.txt": "4\n"})
        self.prs = {1: self.prs[1]}  # only PR 1 is selected; 2-4 stay on main only
        out = self.drift_after_pick({1: ["pick/uat"], 2: ["pick/release/v9"], 3: ["pick/none"], 4: []})
        main_only = out.split("Drift from main")[-1]
        self.assertNotIn("PR 1 (#1)", main_only)  # picked, so not drift
        self.assertRegex(main_only, r"pick label, not on this branch \(1\)\*\*\n- PR 2 \(#2\)")
        self.assertRegex(main_only, r"pick/none \(1\)\*\*\n- PR 3 \(#3\)")
        self.assertRegex(main_only, r"no pick label \(1\)\*\*\n- PR 4 \(#4\)")

    def test_drift_lists_branch_only_commits(self):
        self.on_uat()
        self.commit({"hand.txt": "x\n"}, "hotfix by hand")
        git("push", "-q", "origin", "uat", cwd=self.work)
        git("switch", "-q", "main", cwd=self.work)
        self.merge_pr(1, {"f1.txt": "1\n"})
        out = self.drift_after_pick({1: ["pick/uat"]})
        self.assertRegex(out, r"not from main.*\(1\)\*\*\n- hotfix by hand")

    def test_drift_with_unknown_labels_still_lists_main_only(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        self.prs = {}  # nothing selected, so PR 1 stays on main only
        out = self.drift_after_pick(None)
        self.assertIn("labels unknown", out)
        self.assertIn("- PR 1 (#1)", out)

    def test_drift_list_is_capped(self):
        for n in range(1, pick.DRIFT_LIST_MAX + 6):
            self.merge_pr(n, {f"f{n}.txt": "x\n"})
        self.prs = {}
        out = self.drift_after_pick(None)
        self.assertIn("... and 5 more", out)

    def test_drift_never_pushes(self):
        self.merge_pr(1, {"f1.txt": "1\n"})
        before = self.remote_tip()
        self.drift_after_pick({1: ["pick/uat"]})
        self.assertEqual(self.remote_tip(), before)


if __name__ == "__main__":
    unittest.main()
