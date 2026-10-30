#!/usr/bin/env python3
"""Release flow: cherry-pick + label, one release branch per full version.

Runs inside GitHub Actions (needs GITHUB_REPOSITORY, GH_TOKEN, gh, git with
full history). State lives in GitHub, not in files:

  prd baseline    newest vX.Y.Z tag
  open releases   branches release/vX.Y.Z (cut from the prd tag they follow)
  pick decision   PR labels pick/release/vX.Y.Z | pick/none
  pick state      -x trailers on the release branch ("cherry picked from commit")

    release.py check-labels                            (pull_request event)
    release.py release start [--version X.Y.Z] [--dry-run]
    release.py release uat   [--version X.Y.Z] [--exclude 12,15] [--dry-run]
    release.py release prd   [--version X.Y.Z] [--exclude 12,15] [--dry-run]

Mainline release = newest open branch with patch 0. New PRs get its label.
Hotfix release = patch > 0. Its PRs are labelled by hand.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("GITHUB_REPOSITORY", "")
ACTOR = os.environ.get("GITHUB_ACTOR", "")
FINAL_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
PICK_LABEL = re.compile(r"^pick/release/v(\d+\.\d+\.\d+)$")
PICK, CONFLICT, NONE = "pick/", "pick-conflict/", "pick/none"


# ---------- plumbing ----------

def run(*cmd, check=True, stdin=None):
    r = subprocess.run(cmd, text=True, capture_output=True, input=stdin)
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


def require_release_manager():
    managers = [m.strip() for m in os.environ.get("RELEASE_MANAGERS", "").split(",") if m.strip()]
    if not managers:
        fail("repository variable RELEASE_MANAGERS is not set")
    if ACTOR not in managers:
        fail(f"@{ACTOR} is not a release manager ({', '.join(managers)})")


def is_ancestor(a, b):
    return git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def semver(v):
    return tuple(map(int, v.lstrip("v").split(".")))


# ---------- versions and branches ----------

def latest_prd_tag():
    tags = [t for t in git("tag", "-l", "v*").split() if FINAL_TAG.match(t)]
    if not tags:
        fail("no vX.Y.Z tag yet; tag the first prd commit v0.0.0 to bootstrap")
    return max(tags, key=semver)


def next_version(latest):
    """Version 'start' uses when none is typed: next major after the prd tag."""
    major = semver(latest)[0]
    return f"{major + 1}.0.0"


def open_releases():
    """Open release branches as X.Y.Z, newest first."""
    heads = git("ls-remote", "--heads", "origin", "release/v*").splitlines()
    vers = [h.split("refs/heads/release/v")[-1] for h in heads]
    return sorted((v for v in vers if VERSION.match(v)), key=semver, reverse=True)


def mainline(releases):
    """Release new PRs are labelled for: newest open branch with patch 0."""
    return next((v for v in releases if semver(v)[2] == 0), None)


def pick_label(ver):
    return f"{PICK}release/v{ver}"


def ensure_label(ver):
    run("gh", "label", "create", pick_label(ver), "--repo", REPO, "--force", "--color", "1d76db",
        "--description", f"Ship this PR in v{ver}")


def add_label(pr, label):
    run("gh", "pr", "edit", str(pr), "--repo", REPO, "--add-label", label)


def remove_label(pr, label):
    run("gh", "pr", "edit", str(pr), "--repo", REPO, "--remove-label", label, check=False)


# ---------- PRs ----------

def merged_prs():
    """Merged PRs to main: {number: {title, sha, labels}}. The repo allows squash merges only."""
    rows = json.loads(run("gh", "pr", "list", "--repo", REPO, "--state", "merged", "--base", "main",
                          "--limit", "1000", "--json", "number,title,mergeCommit,labels"))
    return {r["number"]: {"title": r["title"], "sha": r["mergeCommit"]["oid"],
                          "labels": {l["name"] for l in r["labels"]}}
            for r in rows if r.get("mergeCommit")}


def open_prs():
    rows = json.loads(run("gh", "pr", "list", "--repo", REPO, "--state", "open", "--base", "main",
                          "--limit", "1000", "--json", "number,labels"))
    return {r["number"]: {l["name"] for l in r["labels"]} for r in rows}


def contains(ref, prs):
    """PR numbers whose change is live in ref (ancestor, or -x pick not later reverted)."""
    by_sha = {v["sha"]: n for n, v in prs.items()}
    have = {n for n, v in prs.items() if is_ancestor(v["sha"], ref)}
    picked = {}
    for rec in git("log", "--reverse", "--format=%H%x00%B%x01", ref).split("\x01"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, body = rec.split("\x00", 1)
        m = re.search(r"cherry picked from commit ([0-9a-f]{40})", body)
        if m and m.group(1) in by_sha:
            picked[h] = by_sha[m.group(1)]
            have.add(picked[h])
        r = re.search(r"This reverts commit ([0-9a-f]{40})", body)
        if r and r.group(1) in picked:
            have.discard(picked[r.group(1)])
    return have


def pick_commit(pr_sha, base):
    for h in git("rev-list", f"{base}..HEAD").split():
        if f"cherry picked from commit {pr_sha}" in git("log", "-1", "--format=%B", h):
            return h
    fail(f"no cherry-pick of {pr_sha[:7]} found in {base}..HEAD")


def has_pick(labels):
    return any(l.startswith(PICK) for l in labels)


# ---------- the judgement call ----------

def on_conflict(pr, op, branch, dry_run):
    """A pick or revert of PR #pr did not apply cleanly on branch.

    Stop the whole run and push nothing. Label the PR pick-conflict/<branch>
    so the problem is visible. A person resolves it by hand and reruns.
    """
    git(op, "--abort", check=False)
    if not dry_run:
        label = CONFLICT + branch
        run("gh", "label", "create", label, "--repo", REPO, "--force", "--color", "b60205",
            "--description", "Release flow could not apply this PR")
        add_label(pr, label)
    fail(f"#{pr}: {op} conflicts on {branch}. Nothing was pushed. Either:\n"
         f"- remove its `{PICK}{branch}` label (or list it in exclude_prs), then rerun, or\n"
         f"- open a PR into `{branch}` that applies it by hand, then rerun.")


# ---------- pick-decision check ----------

def cmd_check_labels(a):
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    pr = event["pull_request"]
    picks = sorted(l["name"] for l in pr["labels"] if l["name"].startswith(PICK))
    releases = open_releases()

    if not picks and event["action"] in ("opened", "reopened"):
        ver = mainline(releases)
        if ver:
            ensure_label(ver)
            add_label(pr["number"], pick_label(ver))
            picks = [pick_label(ver)]
            say(f"- added `{picks[0]}` (open release). Add `{NONE}` instead to keep this PR out.")

    if not picks:
        if not mainline(releases):
            say("✅ no release is open; 'Release flow → start' labels this PR later")
            return
        fail(f"No pick label. Add `{pick_label(mainline(releases))}` to ship it, "
             f"or `{NONE}` to keep it on main only.")
    if NONE in picks and len(picks) > 1:
        fail(f"{NONE} cannot be combined with {', '.join(p for p in picks if p != NONE)}")
    for p in picks:
        if p == NONE:
            continue
        m = PICK_LABEL.match(p)
        if not m:
            fail(f"{p}: use {PICK}release/vX.Y.Z or {NONE}")
        if m.group(1) not in releases:
            fail(f"{p}: release/v{m.group(1)} is not open (open: {', '.join(releases) or 'none'})")
    say(f"✅ pick decision: {', '.join(picks)}")


# ---------- release flow ----------

def resolve_version(a):
    """--version, else the mainline release, else the only open release."""
    if a.version:
        ver = a.version.lstrip("v")
    else:
        releases = open_releases()
        ver = mainline(releases) or (releases[0] if len(releases) == 1 else None)
        if not ver:
            fail(f"open releases: {', '.join(releases) or 'none'}. Type a version, "
                 f"or run 'start' first.")
    if not VERSION.match(ver):
        fail(f"{a.version}: use X.Y.Z")
    return ver


def start(a):
    base = latest_prd_tag()
    ver = (a.version or next_version(base)).lstrip("v")
    if not VERSION.match(ver):
        fail(f"{a.version}: use X.Y.Z")
    branch = f"release/v{ver}"
    if semver(ver) <= semver(base):
        fail(f"v{ver} is not newer than prd ({base})")
    if git("tag", "-l", f"v{ver}"):
        fail(f"v{ver} is already released")
    releases = open_releases()
    if ver in releases:
        fail(f"{branch} is already open")
    hotfix = semver(ver)[2] != 0
    if not hotfix and mainline(releases):
        fail(f"release/v{mainline(releases)} is still open; ship it to prd first")

    # Mainline: label every PR not yet in prd that has no pick decision.
    backlog = []
    if not hotfix:
        prs = merged_prs()
        in_prd = contains(base, prs)
        backlog = sorted([n for n, v in prs.items() if n not in in_prd and not has_pick(v["labels"])]
                         + [n for n, labels in open_prs().items() if not has_pick(labels)])

    say(f"- cut `{branch}` from prd tag `{base}`" + (" (hotfix: label its PRs by hand)" if hotfix else ""))
    say(f"- label `{pick_label(ver)}` on: {', '.join(f'#{n}' for n in backlog) or 'none'}")
    if a.dry_run:
        return
    git("push", "-q", "origin", f"{base}^{{commit}}:refs/heads/{branch}")
    ensure_label(ver)
    for n in backlog:
        add_label(n, pick_label(ver))
    say(f"**Next:** merge PRs as usual, then run 'Release flow → uat'.")


def sync(ver, a):
    """Make release/vX.Y.Z match its label: pick labelled PRs, revert unlabelled ones."""
    branch, want = f"release/v{ver}", pick_label(ver)
    if ver not in open_releases():
        fail(f"{branch} is not open; run 'start' first")
    git("switch", "-q", "-C", branch, f"origin/{branch}")
    base = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "--exclude", "*-*", "HEAD")

    prs = merged_prs()
    exclude = {int(n) for n in re.findall(r"\d+", a.exclude or "")}
    for n in sorted(exclude):
        if n not in prs:
            fail(f"#{n} is not a merged PR to main")
        prs[n]["labels"].discard(want)
    order = {sha: i for i, sha in enumerate(git("rev-list", "--reverse", "origin/main").split())}
    key = lambda n: order.get(prs[n]["sha"], 1 << 30)

    wanted = {n for n, v in prs.items() if want in v["labels"]}
    have = contains("HEAD", prs)
    to_pick = sorted(wanted - have, key=key)
    to_revert = sorted((have - contains(base, prs)) - wanted, key=key, reverse=True)

    say(f"- branch `{branch}`, base `{base}`")
    say(f"- label `{want}`: {', '.join(f'#{n}' for n in sorted(wanted)) or 'none'}")
    for n in to_revert:
        if git("revert", "--no-edit", pick_commit(prs[n]["sha"], base), check=False).returncode != 0:
            on_conflict(n, "revert", branch, a.dry_run)
        say(f"- removed #{n} {prs[n]['title']}")
    for n in to_pick:
        if git("cherry-pick", "-x", prs[n]["sha"], check=False).returncode != 0:
            on_conflict(n, "cherry-pick", branch, a.dry_run)
        if not a.dry_run:
            remove_label(n, CONFLICT + branch)
        say(f"- picked #{n} {prs[n]['title']}")

    final = sorted(contains("HEAD", prs) - contains(base, prs), key=key)
    notes = (f"`{branch}` from `{base}`.\n\n"
             + "".join(f"- #{n} {prs[n]['title']}\n" for n in final)
             + (f"\nRemoved this round: {', '.join(f'#{n}' for n in to_revert)}\n" if to_revert else ""))
    return branch, base, bool(to_pick or to_revert), exclude, notes


def publish(tag, notes, prerelease):
    """Create the GitHub release, then start the image build.

    A release made with GITHUB_TOKEN does not trigger 'On release', but
    workflow_dispatch does, so dispatch it on the tag.
    """
    run("gh", "release", "create", tag, "--repo", REPO, "--verify-tag", "--title", tag, "--notes", notes,
        "--prerelease" if prerelease else "--latest")
    run("gh", "workflow", "run", "on-release.yaml", "--repo", REPO, "--ref", tag)
    say(f"- published `{tag}` and started 'On release' (builds `{tag}-amd64`)")


def uat(a):
    ver = resolve_version(a)
    branch, base, changed, exclude, notes = sync(ver, a)
    if not changed:
        here = [t for t in git("tag", "--points-at", "HEAD").split() if t.startswith(f"v{ver}-rc.")]
        if here:
            say(f"= no change: `{branch}` is already `{here[0]}`")
            return
        if git("rev-parse", "HEAD") == git("rev-parse", f"{base}^{{commit}}"):
            fail(f"nothing to ship: `{branch}` equals `{base}`. Label PRs `{pick_label(ver)}` first.")
    n_rc = len(git("tag", "-l", f"v{ver}-rc.*").split()) + 1
    tag = f"v{ver}-rc.{n_rc}"
    if a.dry_run:
        say(f"- would push `{branch}`, tag and publish `{tag}`:\n\n{notes}")
        return
    git("tag", "-a", tag, "-m", tag)
    git("push", "-q", "origin", branch, tag)
    publish(tag, notes, prerelease=True)
    for n in exclude:
        remove_label(n, pick_label(ver))
    say(f"**Next:** when the build is green, run 'On deploy request' "
        f"with environment `uat`, image_tag `{tag}-amd64`.")


def prd(a):
    ver = resolve_version(a)
    tag = f"v{ver}"
    if git("tag", "-l", tag):
        fail(f"{tag} already exists (tags never move)")
    branch, base, changed, exclude, notes = sync(ver, a)
    if git("rev-parse", "HEAD") == git("rev-parse", f"{base}^{{commit}}"):
        fail(f"nothing to ship: `{branch}` equals `{base}`")
    rc = next((t for t in git("tag", "--points-at", "HEAD").split() if t.startswith(f"{tag}-rc.")), None)
    if rc:
        notes += f"\nSame commit as `{rc}` (tested in UAT).\n"
    else:
        warn = f"`{tag}` is not an rc that UAT ran: the branch changed since the last rc."
        print(f"::warning::{warn}")
        say(f"⚠️ {warn}")
        notes += f"\n⚠️ {warn}\n"
    if a.dry_run:
        say(f"- would tag and publish `{tag}` (Latest), then delete `{branch}` "
            f"and label `{pick_label(ver)}`:\n\n{notes}")
        return
    git("tag", "-a", tag, "-m", tag)
    git("push", "-q", "origin", tag)
    publish(tag, notes, prerelease=False)
    git("push", "-q", "origin", "--delete", branch)
    for label in (pick_label(ver), CONFLICT + branch, f"picked/{branch}"):
        run("gh", "label", "delete", label, "--repo", REPO, "--yes", check=False)
    say(f"- deleted `{branch}` and its labels. PRs left out go to the next 'start'.")
    say(f"**Next:** when the build is green, run 'On deploy request' "
        f"with environment `prd`, image_tag `{tag}-amd64`.")


def cmd_release(a):
    require_release_manager()
    say(f"## Release flow: {a.target} ({'DRY RUN, nothing is pushed' if a.dry_run else 'live'})")
    git("fetch", "-q", "--tags", "--force", "origin")
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    {"start": start, "uat": uat, "prd": prd}[a.target](a)


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("check-labels")
    q = s.add_parser("release")
    q.add_argument("target", choices=["start", "uat", "prd"])
    q.add_argument("--version", default="")
    q.add_argument("--exclude", default="")
    q.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
