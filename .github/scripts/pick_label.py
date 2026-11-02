#!/usr/bin/env python3
"""Pick label check: every open PR to main needs a pick decision.

Runs inside GitHub Actions on pull_request (needs GITHUB_REPOSITORY, GH_TOKEN,
gh, and PR = the PR number). Passes when at least one label starts with
`pick/release/` and has something after it (the version part can be anything),
or when the PR has `pick/none` (keep it on main only). The two cannot be
combined. It adds no labels: a person decides.

    pick_label.py
"""
import json
import os
import subprocess
import sys

PREFIX = "pick/release/"
NONE = "pick/none"


def check(labels):
    """Return (ok, message) for the label names of one PR."""
    picks = sorted(l for l in labels if l.startswith(PREFIX) and len(l) > len(PREFIX))
    if NONE in labels:
        if picks:
            return False, (f"`{NONE}` cannot be combined with `{'`, `'.join(picks)}`. "
                           f"Keep `{NONE}` to stay on main only, or keep the release label to ship it.")
        return True, f"✅ `{NONE}`: this PR stays on main only"
    if picks:
        return True, f"✅ release label: {', '.join(picks)}"
    near = sorted(l for l in labels if l.lower().startswith(("pick", "release")))
    hint = f" Found `{'`, `'.join(near)}`: the label must start with `{PREFIX}`." if near else ""
    return False, (f"No label starting with `{PREFIX}`.{hint} "
                   f"Add one, for example `{PREFIX}v2.40.0`, to say which release this PR ships in, "
                   f"or `{NONE}` to keep it on main only.")


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
