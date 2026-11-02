#!/usr/bin/env python3
"""Pick label check: every open PR to main needs a pick decision.

Runs inside GitHub Actions on pull_request (needs GITHUB_REPOSITORY, GH_TOKEN,
gh, and PR = the PR number). Passes when at least one label starts with
`pick/release/` and has something after it (the version part can be anything)
and the matching branch exists: `pick/release/<x>` needs branch `release/<x>`,
the same name the Pick workflow uses (`pick/<branch>`). Also passes when the PR
has `pick/none` (keep it on main only). The two cannot be combined. It adds no
labels: a person decides.

    pick_label.py
"""
import json
import os
import subprocess
import sys
import urllib.parse

PREFIX = "pick/release/"
NONE = "pick/none"


def branch_of(label):
    """The branch a pick label names: pick/release/v1 -> release/v1."""
    return label[len("pick/"):]


def branch_exists(repo, branch):
    """True if the branch exists. A 404 means no. Any other failure raises, so an
    outage or a bad token is never read as 'the branch is missing'."""
    ref = urllib.parse.quote(branch, safe="/")
    r = subprocess.run(["gh", "api", f"repos/{repo}/git/ref/heads/{ref}"],
                       text=True, capture_output=True)
    if r.returncode == 0:
        return True
    if "HTTP 404" in r.stderr:
        return False
    raise RuntimeError(f"gh api for branch {branch} failed: {r.stderr.strip()}")


def check(labels, branch_exists):
    """Return (ok, message) for the label names of one PR.

    branch_exists(name) -> bool is passed in, so tests need no network."""
    picks = sorted(l for l in labels if l.startswith(PREFIX) and len(l) > len(PREFIX))
    if NONE in labels:
        if picks:
            return False, (f"`{NONE}` cannot be combined with `{'`, `'.join(picks)}`. "
                           f"Keep `{NONE}` to stay on main only, or keep the release label to ship it.")
        return True, f"✅ `{NONE}`: this PR stays on main only"
    if picks:
        missing = [l for l in picks if not branch_exists(branch_of(l))]
        if missing:
            lines = "; ".join(f"`{l}` needs branch `{branch_of(l)}`" for l in missing)
            return False, (f"No branch for the release label: {lines}. "
                           f"Create the branch, or fix the label. Then re-run this check: "
                           f"creating a branch does not start it.")
        return True, f"✅ release label: {', '.join(picks)} (branch exists)"
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
    try:
        ok, msg = check([l["name"] for l in json.loads(r.stdout)["labels"]],
                        lambda b: branch_exists(repo, b))
    except RuntimeError as e:
        print(f"::error::{e}")
        sys.exit(1)
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
