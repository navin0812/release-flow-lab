# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`release-flow-lab` is a test repo (GitHub: `navin0812/release-flow-lab`) for a cherry-pick + label release flow. It is not a real app. `app/*.txt` and the `Dockerfile` (busybox) are stand-ins so that images can be built and "deployed". There is no build system, linter, or test suite. All logic is GitHub Actions YAML plus one Python script.

Two goals shape every change:
- Three workflows (`on-push`, `on-release`, `on-deploy-request`) **mirror production files** as closely as possible. Every difference from production must stay marked with a `# lab:` comment. Keep that marker when you edit these files, and do not add unmarked differences.
- The release-manager workflows (`prepare-rc`, `merge-forward`, `uat-signoff`, `finalize-release`, `release-status`, `pick-decision`) are lab-only additions.

## Architecture

**All release logic lives in `.github/scripts/release.py`** (stdlib only, shells out to `gh` and `git`). Each release-manager workflow is a thin wrapper that calls one subcommand: `check-labels`, `prepare-rc`, `merge-forward`, `uat-signoff`, `release-gate`, `finalize`, `status`. Workflows run it as `python3 -I .github/scripts/release.py <cmd>`. Run it only inside Actions: it needs `GITHUB_REPOSITORY`, `GH_TOKEN`, `RELEASE_MANAGERS`, and a full-history checkout.

**State lives in GitHub, not in files.** Do not add state files.
- Pick decision: PR labels `pick/release/vX.Y` or `pick/none` (enforced on PRs to `main` by `pick-decision`).
- Pick result: labels `picked/release/vX.Y` and `pick-conflict/release/vX.Y`, set by `prepare-rc`.
- What an env runs: the newest successful GitHub Deployment with task `deploy:image` for that environment. The lab `ecs_deploy` action writes it (`.github/actions/ecs_deploy`). Env name comes from the taskdef path `build/<env>/taskdef.json`.
- UAT sign-off: commit status `uat-signoff` on the image's commit. `release-gate` reads it before any `prd` deploy.

**Release lifecycle:**
1. PR to `main` gets a pick label. Squash merge only (`merged_prs()` and `contains()` depend on it).
2. `prepare-rc` syncs `release/vX.Y` to its label (cherry-pick `-x`, or revert if a label was removed), tags `vX.Y.Z-rc.N`, drafts a pre-release. Publishing the draft triggers `on-release`, which builds `<tag>-amd64`.
3. Someone deploys the rc to `uat` via `on-deploy-request`. `uat-signoff` marks it passed.
4. `prd` takes only a signed-off release image, a past prd image (rollback), or any image with `emergency_reason`.
5. `finalize` tags `vX.Y.Z` on the same commit prd runs, so the final tag never moves the code.
6. `merge-forward` merges the tag prd runs into an open release branch.

**Image tags:** `sha-<run_number>-<sha>-amd64` from `on-push`; `<git tag>-amd64` from `on-release`. `image_commit()` in `release.py` and the shell in `ecs_deploy` both parse these formats. Change both together.

**Stand-ins for production** (`.github/actions/*`): ECR → GHCR, ECS/CodeDeploy → a recorded GitHub Deployment, self-hosted runner → `ubuntu-latest`. The actions keep the same inputs as the org's versions so the workflow files can mirror production.

**Conventions:** release-manager workflows share `concurrency: release-manager` (no cancel). Release managers come from repo variable `RELEASE_MANAGERS` (comma-separated logins), checked by `require_release_manager()`. `on_pick_conflict()` in `release.py` is the deliberate policy hook for conflict handling.

## Commands

No build/test commands exist. To exercise changes, run workflows on GitHub:

```sh
gh workflow run "Prepare rc" -f version=1.1.0 -f dry_run=true   # dry run pushes nothing
gh workflow run "Release status"
gh run list --workflow "Prepare rc" -L1
```

`prepare-rc`, `merge-forward`, and `finalize` support `--dry-run`. Use it first.

## Local-only helpers (untracked, do not commit)

- `setup-repo.sh config|baseline`: one-time repo setup (environments, variables, labels, rulesets, merge settings; tags `v1.0.0`, cuts `release/v1.0`). It does not deploy. Deploys are manual on purpose.
- `rf.sh`: source it for `gh` wrappers (`rf_runs`, `rf_latest`, `rf_log`, ...). It sets `GH_REPO` and `cd`s into the repo.

The step-by-step Runbook is in the owner's notes, not in this repo. The README links to it by name only.
