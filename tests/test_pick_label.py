"""Tests for .github/scripts/pick_label.py. Stdlib only, no git, no network.

    python3 -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
import pick_label  # noqa: E402


class CheckTest(unittest.TestCase):
    def ok(self, labels):
        return pick_label.check(labels)[0]

    def test_version_part_can_be_anything(self):
        for label in ["pick/release/v2.40.0", "pick/release/2.40", "pick/release/sprint-12",
                      "pick/release/x", "pick/release/v1/hotfix"]:
            self.assertTrue(self.ok([label]), label)

    def test_one_good_label_among_others_passes(self):
        self.assertTrue(self.ok(["bug", "php", "pick/release/v1"]))

    def test_several_release_labels_pass(self):
        ok, msg = pick_label.check(["pick/release/v1", "pick/release/v2"])
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
        for label in ["pick/none", "pick/feature/CRAH-4536", "pick/uat", "picked/release/v1",
                      "pick-conflict/release/v1", "Pick/release/v1", "release/v1", "xpick/release/v1"]:
            self.assertFalse(self.ok([label]), label)

    def test_message_names_the_near_miss(self):
        ok, msg = pick_label.check(["picked/release/v1", "bug"])
        self.assertFalse(ok)
        self.assertIn("picked/release/v1", msg)
        self.assertNotIn("`bug`", msg)
        self.assertIn("pick/release/", msg)

    def test_message_without_near_miss_gives_an_example(self):
        ok, msg = pick_label.check(["bug"])
        self.assertFalse(ok)
        self.assertIn("pick/release/v2.40.0", msg)


if __name__ == "__main__":
    unittest.main()
