"""Tests for .github/scripts/pick_label.py. Stdlib only, no git, no network.

    python3 -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
import pick_label  # noqa: E402


def branches(*names):
    """A fake branch lookup: only these branches exist. Records every lookup."""
    def exists(name):
        exists.calls.append(name)
        return name in names
    exists.calls = []
    return exists


EVERY = lambda name: True  # noqa: E731 - every branch exists


class CheckTest(unittest.TestCase):
    def ok(self, labels):
        return pick_label.check(labels, EVERY)[0]

    def test_version_part_can_be_anything(self):
        for label in ["pick/release/v2.40.0", "pick/release/2.40", "pick/release/sprint-12",
                      "pick/release/x", "pick/release/v1/hotfix"]:
            self.assertTrue(self.ok([label]), label)

    def test_one_good_label_among_others_passes(self):
        self.assertTrue(self.ok(["bug", "php", "pick/release/v1"]))

    def test_several_release_labels_pass(self):
        ok, msg = pick_label.check(["pick/release/v1", "pick/release/v2"], EVERY)
        self.assertTrue(ok)
        self.assertIn("pick/release/v1", msg)
        self.assertIn("pick/release/v2", msg)

    def test_no_labels_fails(self):
        self.assertFalse(self.ok([]))
        self.assertFalse(self.ok(["bug", "documentation"]))

    def test_prefix_without_version_fails(self):
        self.assertFalse(self.ok(["pick/release/"]))
        self.assertFalse(self.ok(["pick/release"]))

    def test_other_pick_labels_fail(self):
        for label in ["pick/feature/CRAH-4536", "pick/uat", "picked/release/v1", "pick/none/x",
                      "pick-conflict/release/v1", "Pick/release/v1", "release/v1", "xpick/release/v1"]:
            self.assertFalse(self.ok([label]), label)

    def test_pick_none_passes(self):
        ok, msg = pick_label.check(["pick/none"], EVERY)
        self.assertTrue(ok)
        self.assertIn("pick/none", msg)
        self.assertTrue(self.ok(["bug", "pick/none"]))

    def test_pick_none_with_a_release_label_fails(self):
        ok, msg = pick_label.check(["pick/none", "pick/release/v1"], EVERY)
        self.assertFalse(ok)
        self.assertIn("pick/none", msg)
        self.assertIn("pick/release/v1", msg)

    def test_failure_message_offers_pick_none(self):
        self.assertIn("pick/none", pick_label.check(["bug"], EVERY)[1])

    def test_message_names_the_near_miss(self):
        ok, msg = pick_label.check(["picked/release/v1", "bug"], EVERY)
        self.assertFalse(ok)
        self.assertIn("picked/release/v1", msg)
        self.assertNotIn("`bug`", msg)
        self.assertIn("pick/release/", msg)

    def test_message_without_near_miss_gives_an_example(self):
        ok, msg = pick_label.check(["bug"], EVERY)
        self.assertFalse(ok)
        self.assertIn("pick/release/v2.40.0", msg)


class BranchTest(unittest.TestCase):
    """The label pick/release/<x> must match the branch release/<x> 1:1."""

    def test_label_with_its_branch_passes(self):
        exists = branches("release/v2.40.0")
        ok, msg = pick_label.check(["pick/release/v2.40.0"], exists)
        self.assertTrue(ok)
        self.assertEqual(exists.calls, ["release/v2.40.0"])
        self.assertIn("release/v2.40.0", msg)

    def test_label_without_its_branch_fails(self):
        ok, msg = pick_label.check(["pick/release/v2.40.0"], branches())
        self.assertFalse(ok)
        self.assertIn("release/v2.40.0", msg)
        self.assertIn("branch", msg)

    def test_hotfix_suffix_is_part_of_the_branch_name(self):
        exists = branches("release/v2.39.1-hotfix")
        self.assertTrue(pick_label.check(["pick/release/v2.39.1-hotfix"], exists)[0])
        self.assertFalse(pick_label.check(["pick/release/v2.39.1"], exists)[0])

    def test_every_release_label_needs_its_branch(self):
        exists = branches("release/v1")
        ok, msg = pick_label.check(["pick/release/v1", "pick/release/v2"], exists)
        self.assertFalse(ok)
        self.assertIn("release/v2", msg)
        self.assertNotIn("`release/v1`", msg)

    def test_pick_none_makes_no_lookup(self):
        exists = branches()
        self.assertTrue(pick_label.check(["pick/none"], exists)[0])
        self.assertEqual(exists.calls, [])

    def test_no_release_label_makes_no_lookup(self):
        exists = branches()
        self.assertFalse(pick_label.check(["bug", "picked/release/v1"], exists)[0])
        self.assertEqual(exists.calls, [])

    def test_conflict_message_is_given_before_any_lookup(self):
        exists = branches()
        ok, msg = pick_label.check(["pick/none", "pick/release/v1"], exists)
        self.assertFalse(ok)
        self.assertIn("cannot be combined", msg)
        self.assertEqual(exists.calls, [])


class BranchExistsTest(unittest.TestCase):
    """branch_exists: 200 = yes, 404 = no, anything else is an error (never 'missing')."""

    def run_gh(self, returncode, stderr=""):
        class R:
            pass
        r = R()
        r.returncode, r.stdout, r.stderr = returncode, "", stderr
        return lambda *a, **k: r

    def test_found(self):
        orig = pick_label.subprocess.run
        pick_label.subprocess.run = self.run_gh(0)
        try:
            self.assertTrue(pick_label.branch_exists("o/r", "release/v1"))
        finally:
            pick_label.subprocess.run = orig

    def test_not_found(self):
        orig = pick_label.subprocess.run
        pick_label.subprocess.run = self.run_gh(1, "gh: Not Found (HTTP 404)")
        try:
            self.assertFalse(pick_label.branch_exists("o/r", "release/v1"))
        finally:
            pick_label.subprocess.run = orig

    def test_other_errors_are_not_treated_as_missing(self):
        orig = pick_label.subprocess.run
        pick_label.subprocess.run = self.run_gh(1, "gh: Bad credentials (HTTP 401)")
        try:
            with self.assertRaises(RuntimeError):
                pick_label.branch_exists("o/r", "release/v1")
        finally:
            pick_label.subprocess.run = orig


if __name__ == "__main__":
    unittest.main()
