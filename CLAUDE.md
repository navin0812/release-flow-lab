# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`release-flow-lab` is a test repo (GitHub: `navin0812/release-flow-lab`) for a cherry-pick + label release flow. It is not a real app. `app/*.txt` and the `Dockerfile` (busybox) are stand-ins so that images can be built and "deployed". There is no build system or linter. All logic is GitHub Actions YAML plus two Python scripts: `release.py` (the Release flow) and `pick.py` (the Pick workflow, the only part meant for contest-wordpress). Only `pick.py` has tests.

Two goals shape every change:
- Three workflows (`on-push`, `on-release`, `on-deploy-request`) **mirror production files** as closely as possible. Every difference from production must stay marked with a `# lab:` comment. Keep that marker when you edit these files, and do not add unmarked differences.
- The release workflows (`release-flow`, `pick-decision`) are lab-only additions.

## Architecture

**All release logic lives in `.github/scripts/release.py`** (stdlib only, shells out to `gh` and `git`). Subcommands: `check-labels` (pick-decision) and `release start|uat|prd [--version X.Y.Z] [--exclude 12,15] [--dry-run]` (Release flow). Workflows run it as `python3 -I .github/scripts/release.py <cmd>`. Run it only inside Actions: it needs `GITHUB_REPOSITORY`, `GH_TOKEN`, `RELEASE_MANAGERS`, and a full-history checkout.

**State lives in GitHub, not in files.** Do not add state files.
- prd baseline: the newest `vX.Y.Z` tag.
- Open releases: branches `release/vX.Y.Z`, each cut from the prd tag before it. Mainline = newest open branch with patch 0; hotfix = patch > 0.
- Pick decision: PR labels `pick/release/vX.Y.Z` or `pick/none`. `pick-decision` adds the mainline label on PR open/reopen and skips closed PRs. It fails a PR with no pick label, also when no release is open. It reads labels live (a rerun reuses the old event payload). `start` labels open PRs and reruns their check, because labels added with `GITHUB_TOKEN` start no workflow.
- Pick result: `-x` trailers ("cherry picked from commit") and revert commits on the release branch, read by `contains()`. `pick-conflict/release/vX.Y.Z` marks a PR that did not apply.

**Release lifecycle (Release flow `target`):**
1. `start`: cut `release/vX.Y.Z` from the latest prd tag (default next major). Mainline only: label every merged or open PR not yet in prd that has no pick label.
2. PRs merge to `main` (squash only; `merged_prs()` and `contains()` depend on it).
3. `uat`: sync the branch to its label (cherry-pick `-x` labelled PRs, revert picks whose label was removed or that are in `exclude_prs`), tag `vX.Y.Z-rc.N`, publish the pre-release. Conflict → stop, push nothing.
4. `prd`: same sync, tag `vX.Y.Z`, publish as Latest, delete the branch and its labels. Warns if the commit is not an rc.
5. Deploys are manual via `on-deploy-request`. The `prd` environment has a required reviewer.

**Image build:** releases created with `GITHUB_TOKEN` start no `on-release` run, and a dispatch on the tag would run the workflow file from the release branch (old). So `release-flow` has its own `build-x86` job; the script hands it the tag via `GITHUB_OUTPUT` (`build_image()`). Rerunning `uat` with no change rebuilds the current rc image.

**Image tags:** `sha-<run_number>-<sha>-amd64` from `on-push`; `<git tag>-amd64` from Release flow / `on-release`. The shell in `ecs_deploy` parses these formats.

**Stand-ins for production** (`.github/actions/*`): ECR → GHCR, ECS/CodeDeploy → a recorded GitHub Deployment, self-hosted runner → `ubuntu-latest`. The actions keep the same inputs as the org's versions so the workflow files can mirror production.

**Conventions:** `release-flow` uses `concurrency: release-flow` (no cancel). Release managers come from repo variable `RELEASE_MANAGERS` (comma-separated logins), checked by `require_release_manager()`. `on_conflict()` in `release.py` is the deliberate policy hook for conflict handling.

**Pick workflow (`pick.yaml` + `pick.py`).** Standalone and cherry-pick only: no tags, releases, builds, or release managers. A person tags and releases in the GitHub UI. Input: label `pick/<branch>` (or `label`) selects merged PRs; `exclude_prs` only excludes. One run does all or nothing: a conflict, or a PR that changes `.github/workflows/`, stops the run and pushes nothing (`GITHUB_TOKEN` cannot push workflow files). It writes no labels and shows its result in the run summary. It checks out the script from the default branch and the target branch separately, because old branches have no script. Plan and decisions are in the owner's notes (`Pick Tool Plan`). Squash merges only; do not add rebase-merge handling without asking.

**Pick label check (`pick-label.yaml` + `pick_label.py`).** Standalone, separate from `pick-decision` (which belongs to the Release flow and needs an open `release/vX.Y.Z`). On every PR to `main` (opened, reopened, synchronize, labeled, unlabeled) it passes if some label starts with `pick/release/` and has something after it (the version part can be anything) and the branch `release/<same part>` exists (label and branch match 1:1, the same name Pick uses for `pick/<branch>`), or if the PR has `pick/none` (stays on main only; no branch lookup). `pick/none` plus a release label fails: the two contradict. A missing branch fails; any lookup error other than 404 also fails and is never read as "missing". Creating a branch starts no run, so re-run the check after it. It adds no labels, reads the labels live, and is advisory (a required check needs a ruleset, which needs admin).

## Commands

To exercise changes, run workflows on GitHub:

```sh
gh workflow run release-flow.yaml -f target=uat -f dry_run=true   # dry run pushes nothing
gh run list --workflow release-flow.yaml -L1
gh workflow run pick.yaml -f branch=uat-test -f dry_run=true      # Pick: dry run first
```

Use `dry_run=true` first when you change `release.py` or `pick.py`.

Unit tests for `pick.py` (stdlib only, real git in a temp dir, no network, about 25 s):

```sh
python3 -I -m unittest discover -s tests -v
```

## Local-only helpers (untracked, do not commit)

- `setup-repo.sh config|baseline`: one-time repo setup (environments, variables, labels, rulesets, merge settings). It does not deploy. Deploys are manual on purpose. Not checked against the new flow.
- `rf.sh`: source it for `gh` wrappers (`rf_runs`, `rf_latest`, `rf_log`, ...). It sets `GH_REPO` and `cd`s into the repo.

The step-by-step Runbook is in the owner's notes, not in this repo. It describes the old flow until updated.
