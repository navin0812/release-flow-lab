#!/usr/bin/env python3
"""Pick: cherry-pick the merged PRs that carry a label onto a branch.

Runs inside GitHub Actions (needs GITHUB_REPOSITORY, GH_TOKEN, gh, and git
with full history, checked out on the target branch). State lives in GitHub:

  selection   PR label, default pick/<branch>
  skip list   --exclude, merged PR numbers
  pick state  -x trailers on the branch ("cherry picked from commit")

    pick.py <branch> [--label L] [--exclude 12,15] [--dry-run]

One run does all of it or nothing. A conflict, or a PR that changes
.github/workflows/ (GITHUB_TOKEN cannot push those), stops the run and pushes
nothing. The repo merges by squash only: one commit per PR.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

REPO = os.environ.get("GITHUB_REPOSITORY", "")
WORKFLOWS = ".github/workflows"


# ---------- plumbing ----------

def run(*cmd, check=True):
    r = subprocess.run(cmd, text=True, capture_output=True)
    if check and r.returncode != 0:
        fail(f"{' '.join(cmd)} failed:\n{r.stderr}{r.stdout}")
    return r.stdout.strip() if check else r


def git(*args, check=True):
    return run("git", *args, check=check)


def say(msg=""):
    print(msg)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(msg + "\n\n")


def fail(msg):
    print(f"::error::{msg.splitlines()[0]}")
    say(f"❌ {msg}")
    sys.exit(1)


# ---------- PRs ----------

def merged_prs(label):
    """Merged PRs to main with the label: {number: {title, sha}}."""
    if run("gh", "api", f"repos/{REPO}/labels/{quote(label, safe='')}", check=False).returncode != 0:
        fail(f"label `{label}` does not exist. Check the spelling, or create it and label the PRs.")
    rows = json.loads(run("gh", "pr", "list", "--repo", REPO, "--state", "merged", "--base", "main",
                          "--label", label, "--limit", "1000", "--json", "number,title,mergeCommit"))
    return {r["number"]: {"title": r["title"], "sha": r["mergeCommit"]["oid"]}
            for r in rows if r.get("mergeCommit")}


def is_ancestor(a, b):
    return git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def picked_sources(ref):
    """Commits that ref already carries as -x picks."""
    log = git("log", "--format=%B", ref)
    return set(re.findall(r"cherry picked from commit ([0-9a-f]{40})", log))


# ---------- the judgement call ----------

def on_conflict(pr, title):
    """The pick of PR #pr did not apply cleanly.

    Stop the whole run and push nothing. A person decides: drop the PR from
    this run, or pick it by hand, then run again.
    """
    files = git("diff", "--name-only", "--diff-filter=U")
    git("cherry-pick", "--abort", check=False)
    fail(f"#{pr} {title}: cherry-pick conflicts. Nothing was pushed.\n"
         f"Conflicting files:\n{files}\n"
         f"Either:\n"
         f"- list #{pr} in exclude_prs (or remove its label), then run again, or\n"
         f"- pick it by hand onto the branch, then run again.")


def is_empty_pick():
    """The change is already on the branch: no conflict, nothing to commit."""
    return (not git("diff", "--name-only", "--diff-filter=U")
            and git("diff", "--cached", "--quiet", check=False).returncode == 0)


# ---------- the pick ----------

def execute(branch, prs, exclude=frozenset(), dry_run=False):
    """Pick every PR in prs onto HEAD (checked out on branch). Returns the PR numbers picked."""
    if branch == "main":
        fail("refusing to pick onto main")
    unknown = sorted(n for n in exclude if n not in prs)
    if unknown:
        fail(f"{', '.join(f'#{n}' for n in unknown)} in exclude_prs: not a merged PR with this label")

    order = {sha: i for i, sha in enumerate(git("rev-list", "--reverse", "origin/main").split())}
    have = picked_sources("HEAD")
    todo, present = [], []
    for n in sorted(prs, key=lambda n: order.get(prs[n]["sha"], 1 << 30)):
        if n in exclude:
            continue
        sha = prs[n]["sha"]
        (present if is_ancestor(sha, "HEAD") or sha in have else todo).append(n)

    picked, applied = [], []
    for n in todo:
        if git("cherry-pick", "-x", prs[n]["sha"], check=False).returncode != 0:
            if not is_empty_pick():
                on_conflict(n, prs[n]["title"])
            git("cherry-pick", "--skip")
            applied.append(n)
        else:
            picked.append(n)

    # GITHUB_TOKEN cannot push a change to workflow files, so stop now, with a name.
    blocked = [n for n in picked
               if git("diff-tree", "--no-commit-id", "--name-only", "-r", prs[n]["sha"], "--", WORKFLOWS)]
    if blocked:
        fail(f"{', '.join(f'#{n}' for n in blocked)} change(s) `{WORKFLOWS}/`. Nothing was pushed.\n"
             f"Workflow files: GITHUB_TOKEN cannot push them. Either:\n"
             f"- list them in exclude_prs and run again, then push those PRs by hand, or\n"
             f"- push them by hand first, then run again.")

    report(branch, prs, exclude, picked, present, applied, dry_run)
    if picked and not dry_run:
        git("push", "origin", f"HEAD:refs/heads/{branch}")
    return picked


def report(branch, prs, exclude, picked, present, applied, dry_run):
    line = lambda n: f"- #{n} {prs[n]['title']}"
    say(f"## Pick onto `{branch}`" + (" (dry run, nothing pushed)" if dry_run else ""))
    say("\n".join([f"**Picked ({len(picked)})**", *map(line, picked)]) if picked
        else "Nothing to pick.")
    if present:
        say("\n".join([f"Already on the branch ({len(present)})", *map(line, present)]))
    if applied:
        say("\n".join([f"Already applied by hand, no new commit ({len(applied)})", *map(line, applied)]))
    if exclude:
        say(f"Excluded: {', '.join(f'#{n}' for n in sorted(exclude))}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("branch")
    p.add_argument("--label", default="")
    p.add_argument("--exclude", default="")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    label = a.label or f"pick/{a.branch}"
    exclude = {int(n) for n in re.findall(r"\d+", a.exclude)}
    execute(a.branch, merged_prs(label), exclude, a.dry_run)


if __name__ == "__main__":
    main()
