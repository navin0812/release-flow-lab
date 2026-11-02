# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`release-flow-lab` is a test repo (GitHub: `navin0812/release-flow-lab`) for a cherry-pick + label flow. It is not a real app. `app/*.txt` and the `Dockerfile` (busybox) are stand-ins so that images can be built and "deployed". There is no build system or linter. All logic is GitHub Actions YAML plus two Python scripts: `pick.py` (the Pick workflow) and `pick_label.py` (the Pick label check). Both are meant for contest-wordpress, and both have tests.

Only the cherry-pick is automated. Tagging, releasing, building and deploying are manual. The old Release flow (`release-flow`, `pick-decision`, `release.py`) was removed: it does not fit contest-wordpress. It stays in git history.

One goal shapes every change: three workflows (`on-push`, `on-release`, `on-deploy-request`) **mirror production files** as closely as possible. Every difference from production must stay marked with a `# lab:` comment. Keep that marker when you edit these files, and do not add unmarked differences.

## Architecture

**State lives in GitHub, not in files.** Do not add state files.

**Image tags:** `sha-<run_number>-<sha>-amd64` from `on-push`; `<git tag>-amd64` from `on-release`. The shell in `ecs_deploy` parses these formats.

**Stand-ins for production** (`.github/actions/*`): ECR → GHCR, ECS/CodeDeploy → a recorded GitHub Deployment, self-hosted runner → `ubuntu-latest`. The actions keep the same inputs as the org's versions so the workflow files can mirror production.

**Pick workflow (files `auto-cherry-pick.yaml` + `pick.py`).** Standalone and cherry-pick only: no tags, releases, builds, or release managers (none exist in contest-wordpress). A person tags and releases in the GitHub UI. Input: label `pick/<branch>` (or `label`) selects merged PRs; `exclude_prs` only excludes. One run does all or nothing: a conflict, or a PR that changes `.github/workflows/`, stops the run and pushes nothing (`GITHUB_TOKEN` cannot push workflow files). It writes no labels and shows its result in the run summary. It checks out the script from the default branch and the target branch separately, because old branches have no script. Plan and decisions are in the owner's notes (`Pick Tool Plan`). Squash merges only; do not add rebase-merge handling without asking.

**Pick label check (`pick-label.yaml` + `pick_label.py`).** Standalone. On every PR to `main` (opened, reopened, synchronize, labeled, unlabeled) it passes if some label starts with `pick/release/` and has something after it (the version part can be anything) and the branch `release/<same part>` exists (label and branch match 1:1, the same name Pick uses for `pick/<branch>`), or if the PR has `pick/none` (stays on main only; no branch lookup). `pick/none` plus a release label fails: the two contradict. A missing branch fails; any lookup error other than 404 also fails and is never read as "missing". Creating a branch starts no run, so re-run the check after it. It adds no labels, reads the labels live, and is advisory (a required check needs a ruleset, which needs admin).

## Commands

To exercise changes, run workflows on GitHub:

```sh
gh workflow run auto-cherry-pick.yaml -f branch=uat-test -f dry_run=true      # auto-cherry-pick: dry run first
gh run list --workflow auto-cherry-pick.yaml -L1
```

Use `dry_run=true` first when you change `pick.py`.

Unit tests for `pick.py` and `pick_label.py` (stdlib only, real git in a temp dir, no network, about 25 s):

```sh
python3 -I -m unittest discover -s tests -v
```

## Local-only helpers (untracked, do not commit)

- `setup-repo.sh config|baseline`: one-time repo setup (environments, variables, labels, rulesets, merge settings). It does not deploy. Deploys are manual on purpose. Written for the removed Release flow; not checked against Pick.
- `rf.sh`: source it for `gh` wrappers (`rf_runs`, `rf_latest`, `rf_log`, ...). It sets `GH_REPO` and `cd`s into the repo.

The step-by-step Runbook is in the owner's notes, not in this repo. It describes the old flow until updated.
