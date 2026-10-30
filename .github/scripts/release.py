#!/usr/bin/env python3
"""Release manager for the cherry-pick + label flow.

Runs inside GitHub Actions (needs GITHUB_REPOSITORY, GH_TOKEN, gh, git with
full history). State lives in GitHub, not in files:

  pick decision   PR labels pick/release/vX.Y.Z | pick/none
  pick state      PR labels picked/release/vX.Y.Z | pick-conflict/release/vX.Y.Z
                  (one release branch per full version: release/vX.Y.Z)
  what env runs   newest successful Deployment with task "deploy:image"
                  (written by .github/actions/ecs_deploy)
  UAT sign-off    commit status "uat-signoff" on the image's commit

    release.py check-labels                      (pull_request event)
    release.py prepare-rc X.Y.Z [--dry-run]
    release.py merge-forward X.Y.Z [--dry-run]
    release.py uat-signoff IMAGE_TAG
    release.py release-gate ENV IMAGE_TAG [--emergency REASON]
    release.py finalize X.Y.Z [--dry-run]
    release.py status
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
PICK_LABEL = re.compile(r"^pick/(release/v\d+\.\d+\.\d+)$")
PICK, PICKED, CONFLICT, NONE = "pick/", "picked/", "pick-conflict/", "pick/none"
SIGNOFF = "uat-signoff"


# ---------- plumbing ----------

def run(*cmd, check=True, stdin=None):
    r = subprocess.run(cmd, text=True, capture_output=True, input=stdin)
    if check and r.returncode != 0:
        fail(f"{' '.join(cmd)} failed:\n{r.stderr}{r.stdout}")
    return r.stdout.strip() if check else r


def git(*args, check=True):
    return run("git", *args, check=check)


def api(path, method="GET", body=None):
    args = ["gh", "api", "-X", method, path]
    if body is not None:
        args += ["--input", "-"]
    out = run(*args, stdin=json.dumps(body) if body is not None else None)
    return json.loads(out) if out else None


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


def final_tag_at(commit):
    return next((t for t in git("tag", "--points-at", commit).split() if FINAL_TAG.match(t)), None)


def image_commit(image_tag):
    """Commit an image was built from: git tag for vX-amd64, short sha for sha-N-<sha>-amd64."""
    m = re.match(r"^sha-\d+-([0-9a-f]+)-amd64$", image_tag)
    ref = m.group(1) if m else image_tag.removesuffix("-amd64")
    return api(f"repos/{REPO}/commits/{ref}")["sha"]


# ---------- GitHub state ----------

def deployments(env):
    """Successful image deployments to env, newest first: [{image, commit}]."""
    out = []
    for d in api(f"repos/{REPO}/deployments?environment={env}&task=deploy:image&per_page=100"):
        statuses = api(f"repos/{REPO}/deployments/{d['id']}/statuses?per_page=1")
        if statuses and statuses[0]["state"] in ("success", "inactive"):
            out.append({"image": (d.get("payload") or {}).get("image"), "commit": d["sha"]})
    return out


def running(env):
    d = deployments(env)
    if not d:
        fail(f"no image deployment recorded for {env}; deploy one with 'On deploy request'")
    return d[0]


def merged_prs():
    """Merged PRs to main: {number: {title, sha, labels}}. The repo allows squash merges only."""
    rows = json.loads(run("gh", "pr", "list", "--repo", REPO, "--state", "merged", "--base", "main",
                          "--limit", "1000", "--json", "number,title,mergeCommit,labels"))
    return {r["number"]: {"title": r["title"], "sha": r["mergeCommit"]["oid"],
                          "labels": {l["name"] for l in r["labels"]}}
            for r in rows if r.get("mergeCommit")}


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


def set_labels(changes):
    """[(pr, label, add)] → apply, creating labels on first use."""
    for pr, label, add in changes:
        if add:
            run("gh", "label", "create", label, "--repo", REPO, "--force",
                "--color", "0e8a16" if label.startswith(PICKED) else "b60205",
                "--description", "Set by prepare-rc")
            run("gh", "pr", "edit", str(pr), "--repo", REPO, "--add-label", label)
        else:
            run("gh", "pr", "edit", str(pr), "--repo", REPO, "--remove-label", label, check=False)


def next_pick_labels(ver):
    """Pick labels to have ready once vX.Y.Z ships: next patch, next minor, next major."""
    x, y, z = map(int, ver.split("."))
    return [f"{PICK}release/v{x}.{y}.{z + 1}", f"{PICK}release/v{x}.{y + 1}.0",
            f"{PICK}release/v{x + 1}.0.0"]


def ensure_pick_labels(ver):
    for label in next_pick_labels(ver):
        run("gh", "label", "create", label, "--repo", REPO, "--force", "--color", "1d76db",
            "--description", f"Ship this PR on {label[len(PICK):]}")


def signed_off(commit):
    for s in api(f"repos/{REPO}/commits/{commit}/statuses?per_page=100"):
        if s["context"] == SIGNOFF:
            return s if s["state"] == "success" else None
    return None


# ---------- the judgement call ----------

def on_pick_conflict(pr, sha, branch, dry_run):
    """A cherry-pick of PR #pr did not apply cleanly on branch.

    Default: stop the whole run, push nothing, and mark the PR
    pick-conflict/<branch> so the problem is visible. A person resolves
    the pick by hand and reruns prepare-rc. Change this if your team
    prefers to skip the PR and list it in the rollup instead.
    """
    git("cherry-pick", "--abort", check=False)
    if not dry_run:
        set_labels([(pr, CONFLICT + branch, True)])
    fail(f"#{pr} conflicts on {branch}. Labelled {CONFLICT + branch}. Resolve by hand:\n"
         f"    git switch {branch} && git cherry-pick -x {sha}\n"
         f"    (fix files) && git cherry-pick --continue && git push\n"
         f"or add the PR it depends on to {PICK + branch}, then rerun prepare-rc.")


# ---------- commands ----------

def cmd_check_labels(a):
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    labels = {l["name"] for l in event["pull_request"]["labels"]}
    picks = sorted(l for l in labels if l.startswith(PICK))
    if not picks:
        fail("Add a pick decision: pick/release/vX.Y.Z (ship on that branch) or pick/none.")
    if NONE in picks and len(picks) > 1:
        fail(f"pick/none cannot be combined with {', '.join(p for p in picks if p != NONE)}")
    bad = [p for p in picks if p != NONE and not PICK_LABEL.match(p)]
    if bad:
        fail(f"{', '.join(bad)}: use pick/ + a release branch name, e.g. pick/release/v1.1.0")
    say(f"✅ pick decision: {', '.join(picks)}")


def cmd_prepare_rc(a):
    require_release_manager()
    ver = a.version.lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        fail(f"{a.version}: use X.Y.Z")
    branch = f"release/v{ver}"
    want, done, bad = PICK + branch, PICKED + branch, CONFLICT + branch
    mode = "DRY RUN, nothing is pushed" if a.dry_run else "live"
    say(f"## prepare-rc v{ver} ({mode})")

    git("fetch", "-q", "--tags", "--force", "origin")
    if git("tag", "-l", f"v{ver}"):
        fail(f"v{ver} is already released; use the next patch number")
    prd = running("prd")
    prd_tag = final_tag_at(prd["commit"])

    if git("ls-remote", "--heads", "origin", branch) == "":
        if not prd_tag:
            fail(f"prd runs {prd['image']}, which has no final tag; finalize or roll back first")
        git("switch", "-q", "-c", branch, prd_tag)
        say(f"- cut `{branch}` from last prd tag `{prd_tag}`")
    else:
        git("switch", "-q", "-C", branch, f"origin/{branch}")

    if not is_ancestor(prd["commit"], "HEAD"):
        fail(f"{branch} does not contain what prd runs ({prd_tag or prd['image']}). "
             f"Run 'Merge forward' for {ver} first.")

    base = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "--exclude", "*-*", "HEAD")
    prs = merged_prs()
    order = {sha: i for i, sha in enumerate(git("rev-list", "--reverse", "origin/main").split())}
    key = lambda n: order.get(prs[n]["sha"], 1 << 30)

    wanted = {n for n, v in prs.items() if want in v["labels"]}
    in_prd = contains(prd["commit"], prs)
    have = contains("HEAD", prs)
    to_pick = sorted(wanted - have, key=key)
    to_revert = sorted((have - contains(base, prs)) - wanted - in_prd, key=key, reverse=True)

    say(f"- branch `{branch}`, base `{base}`, prd runs `{prd['image']}`")
    say(f"- label `{want}`: {', '.join(f'#{n}' for n in sorted(wanted)) or 'none'}")

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    changes = []
    for n in to_revert:
        h = pick_commit(prs[n]["sha"], base)
        git("revert", "--no-edit", h)
        changes.append((n, done, False))
        say(f"- reverted #{n} {prs[n]['title']} (label `{want}` removed)")
    for n in to_pick:
        sha = prs[n]["sha"]
        if git("cherry-pick", "-x", sha, check=False).returncode != 0:
            on_pick_conflict(n, sha, branch, a.dry_run)
        changes += [(n, done, True), (n, bad, False)]
        say(f"- picked #{n} {prs[n]['title']}")

    if not to_pick and not to_revert:
        here = [t for t in git("tag", "--points-at", "HEAD").split() if t.startswith(f"v{ver}-rc.")]
        if here:
            say(f"= no change: HEAD is already `{here[0]}`")
            return
        if git("rev-parse", "HEAD") == git("rev-parse", f"{base}^{{commit}}"):
            say(f"= nothing to release: `{branch}` equals `{base}`")
            return
        say("- no picks or reverts; the branch changed by a merge forward")

    n_rc = len(git("tag", "-l", f"v{ver}-rc.*").split()) + 1
    tag = f"v{ver}-rc.{n_rc}"
    final = sorted(contains("HEAD", prs) - contains(base, prs), key=key)
    notes = (f"Release candidate on `{branch}` (base `{base}`).\n\n"
             + "".join(f"- #{n} {prs[n]['title']}\n" for n in final)
             + (f"\nDropped this round: {', '.join(f'#{n}' for n in to_revert)}\n" if to_revert else "")
             + f"\nNext: publish this pre-release to build `{tag}-amd64`, then deploy it to uat.\n")
    if a.dry_run:
        say(f"- would tag `{tag}`, push `{branch}`, and draft the pre-release:\n\n{notes}")
        return
    git("tag", "-a", tag, "-m", tag)
    git("push", "-q", "origin", branch, tag)
    run("gh", "release", "create", tag, "--repo", REPO, "--verify-tag", "--draft", "--prerelease",
        "--title", tag, "--notes", notes)
    set_labels(changes)
    say(f"- tagged `{tag}` and drafted the pre-release")
    say(f"**Next:** publish the draft (`gh release edit {tag} --draft=false`). "
        f"That starts `On release`, which builds `{tag}-amd64`.")


def cmd_merge_forward(a):
    require_release_manager()
    ver = a.version.lstrip("v")
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        fail(f"{a.version}: use X.Y.Z")
    branch = f"release/v{ver}"
    say(f"## merge-forward into {branch} ({'DRY RUN' if a.dry_run else 'live'})")
    git("fetch", "-q", "--tags", "--force", "origin")
    if git("ls-remote", "--heads", "origin", branch) == "":
        fail(f"{branch} does not exist")
    prd = running("prd")
    prd_tag = final_tag_at(prd["commit"])
    if not prd_tag:
        fail(f"prd runs {prd['image']}, which has no final tag; finalize the hotfix first")
    git("switch", "-q", "-C", branch, f"origin/{branch}")
    if is_ancestor(prd["commit"], "HEAD"):
        say(f"= `{branch}` already contains `{prd_tag}`")
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    msg = f"Merge {prd_tag} into {branch}"
    if git("merge", "--no-ff", "-m", msg, f"{prd_tag}^{{commit}}", check=False).returncode != 0:
        git("merge", "--abort", check=False)
        fail(f"{msg} has conflicts. Resolve by hand:\n"
             f"    git switch {branch} && git merge --no-ff {prd_tag}\n"
             f"    (fix files) && git commit && git push")
    if a.dry_run:
        say(f"- would push: {msg}")
        return
    git("push", "-q", "origin", branch)
    say(f"- pushed `{msg}` as `{git('rev-parse', '--short', 'HEAD')}`")


def cmd_uat_signoff(a):
    require_release_manager()
    if not re.match(r"^v\d+\.\d+\.\d+(-rc\.\d+)?-amd64$", a.image):
        fail(f"{a.image}: only release images (vX.Y.Z[-rc.N]-amd64) can be signed off")
    uat = running("uat")
    if uat["image"] != a.image:
        fail(f"UAT runs {uat['image']}, not {a.image}; deploy it to uat first")
    api(f"repos/{REPO}/statuses/{uat['commit']}", "POST",
        {"state": "success", "context": SIGNOFF, "description": f"{a.image} by @{ACTOR}"})
    say(f"✅ UAT signed off `{a.image}` (commit `{uat['commit'][:7]}`) by @{ACTOR}")


def cmd_release_gate(a):
    if a.env != "prd":
        say(f"✅ `{a.env}`: no release gate")
        return
    require_release_manager()
    emergency = (a.emergency or "").strip()
    if not re.match(r"^v\d+\.\d+\.\d+(-rc\.\d+)?-amd64$", a.image):
        if not emergency:
            fail(f"{a.image} is not a release image; prd takes vX.Y.Z[-rc.N]-amd64 only")
    else:
        commit = image_commit(a.image)
        s = signed_off(commit)
        if s:
            say(f"✅ `{a.image}`: UAT sign-off ({s['description']})")
            return
        if any(d["image"] == a.image for d in deployments("prd")):
            say(f"✅ `{a.image}`: ran on prd before (rollback)")
            return
    if not emergency:
        fail(f"REFUSED: {a.image} has no UAT sign-off. Set emergency_reason only when prd is down.")
    print(f"::warning::EMERGENCY prd deploy without UAT by @{ACTOR}: {emergency}")
    say(f"⚠️ EMERGENCY deploy of `{a.image}` without UAT by @{ACTOR}. Reason: {emergency}")


def cmd_finalize(a):
    require_release_manager()
    ver = a.version.lstrip("v")
    tag = f"v{ver}"
    say(f"## finalize {tag} ({'DRY RUN' if a.dry_run else 'live'})")
    git("fetch", "-q", "--tags", "--force", "origin")
    if git("tag", "-l", tag):
        fail(f"{tag} already exists (tags never move)")
    prd = running("prd")
    if not re.match(rf"^{re.escape(tag)}-rc\.\d+-amd64$", prd["image"] or ""):
        fail(f"prd runs {prd['image']}, not an rc of {tag}; deploy the rc to prd first")
    rc = prd["image"].removesuffix("-amd64")
    notes = run("gh", "release", "view", rc, "--repo", REPO, "--json", "body", "--jq", ".body")
    notes = notes.split("\nNext:")[0] + f"\nprd runs `{prd['image']}`. This release tags the same commit.\n"
    if a.dry_run:
        say(f"- would tag `{tag}` on `{prd['commit'][:7]}` (same as `{rc}`) and draft the release")
        say(f"- would create labels: {', '.join(next_pick_labels(ver))}")
        return
    git("tag", "-a", tag, prd["commit"], "-m", tag)
    git("push", "-q", "origin", tag)
    run("gh", "release", "create", tag, "--repo", REPO, "--verify-tag", "--draft", "--latest",
        "--title", tag, "--notes", notes)
    say(f"- tagged `{tag}` on `{prd['commit'][:7]}` and drafted the release")
    ensure_pick_labels(ver)
    say(f"- created labels: {', '.join(next_pick_labels(ver))}")
    say(f"**Next:** publish it (`gh release edit {tag} --draft=false`). "
        f"`On release` then builds `{tag}-amd64`. Do not deploy that image; prd keeps `{prd['image']}`.")


def cmd_status(a):
    say("## Release status")
    say("| Env | Image | Commit |\n| --- | --- | --- |\n" + "\n".join(
        f"| {e} | `{d['image']}` | `{d['commit'][:7]}` |" if (d := (deployments(e) or [None])[0])
        else f"| {e} | (none) | |" for e in ("dev", "uat", "prd")))
    rows = []
    for n, v in sorted(merged_prs().items()):
        for l in sorted(v["labels"]):
            m = PICK_LABEL.match(l)
            if m:
                br = m.group(1)
                state = ("conflict" if CONFLICT + br in v["labels"]
                         else "picked" if PICKED + br in v["labels"] else "pending")
                rows.append(f"| #{n} | `{l}` | {state} | {v['title']} |")
    say("| PR | Label | State | Title |\n| --- | --- | --- | --- |\n" + ("\n".join(rows) or "| | (none) | | |"))


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("check-labels")
    q = s.add_parser("prepare-rc"); q.add_argument("version"); q.add_argument("--dry-run", action="store_true")
    q = s.add_parser("merge-forward"); q.add_argument("version"); q.add_argument("--dry-run", action="store_true")
    q = s.add_parser("uat-signoff"); q.add_argument("image")
    q = s.add_parser("release-gate"); q.add_argument("env"); q.add_argument("image")
    q.add_argument("--emergency", default="")
    q = s.add_parser("finalize"); q.add_argument("version"); q.add_argument("--dry-run", action="store_true")
    s.add_parser("status")
    a = p.parse_args()
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
