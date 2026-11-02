#!/usr/bin/env python3
"""Pick label check: every open PR to main needs a release pick label.

Runs inside GitHub Actions on pull_request (needs GITHUB_REPOSITORY, GH_TOKEN,
gh, and PR = the PR number). Passes when at least one label starts with
`pick/release/` and has something after it. The version part can be anything.
It adds no labels: a person picks the release.

    pick_label.py
"""
import json
import os
import subprocess
import sys

PREFIX = "pick/release/"


def check(labels):
    """Return (ok, message) for the label names of one PR."""
    picks = sorted(l for l in labels if l.startswith(PREFIX) and len(l) > len(PREFIX))
    if picks:
        return True, f"✅ release label: {', '.join(picks)}"
    near = sorted(l for l in labels if l.lower().startswith(("pick", "release")))
    hint = f" Found `{'`, `'.join(near)}`: the label must start with `{PREFIX}`." if near else ""
    return False, (f"No label starting with `{PREFIX}`.{hint} "
                   f"Add one, for example `{PREFIX}v2.40.0`, to say which release this PR ships in.")


def main():
    repo, number = os.environ["GITHUB_REPOSITORY"], os.environ["PR"]
    # Read labels live: a rerun of this check reuses the old event payload.
    r = subprocess.run(["gh", "pr", "view", number, "--repo", repo, "--json", "labels"],
                       text=True, capture_output=True)
    if r.returncode != 0:
        print(f"::error::gh pr view {number} failed: {r.stderr.strip()}")
        sys.exit(1)
    ok, msg = check([l["name"] for l in json.loads(r.stdout)["labels"]])
    print(msg)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(msg + "\n\n")
    if not ok:
        print(f"::error::{msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
