# release-flow-lab

Test repo for a cherry-pick + label flow. It mirrors three production
workflows (`on-push`, `on-release`, `on-deploy-request`) as closely as possible
and adds the auto-cherry-pick and Pick label workflows. Tagging, releasing and deploying stay manual. Every difference from production is marked
`# lab:` in the workflow files.

| Production | Lab stand-in |
| --- | --- |
| ECR | GHCR (`ghcr.io/<owner>/<repo>`) |
| ECS + CodeDeploy | `ecs_deploy` renders the task definition, records a GitHub Deployment |
| Self-hosted runner | `ubuntu-latest` |
| Org deploy-actions | `.github/actions/*` with the same inputs |

## Pick flow

A person labels a merged PR `pick/<branch>` (for a release branch: `pick/release/vX.Y.Z`).
auto-cherry-pick cherry-picks the labelled PRs onto that branch. Everything else is manual.

| Workflow | Trigger | What |
| --- | --- | --- |
| auto-cherry-pick | manual: `branch`, optional `label`, `exclude_prs` | Cherry-picks (`-x`) the merged PRs that carry label `pick/<branch>` onto the branch, in `main` merge order. All or nothing: a conflict, or a PR that changes `.github/workflows/`, stops the run and pushes nothing. The result is in the run summary. Every run shows a preview (dry run), then waits: click "Review deployments" and approve `auto-cherry-pick` to pick for real with the same inputs. Reject to stop. Needs environment `auto-cherry-pick` with required reviewers; runs `pick-run.yaml`. Standalone: does not tag, release or build. |
| Pick label | open PR to `main` | Passes if a label starts with `pick/release/` and has more after it (`pick/release/v2.40.0`) **and** the branch `release/<same part>` exists, or if the PR has `pick/none` (main only, no branch lookup). The two together fail. Creating a branch starts no run: re-run the check after it. Adds no labels. Advisory: it shows red, it cannot block the merge without a ruleset. |
| On release | release published by a person | Builds `<tag>-amd64`. Production mirror. |
| On deploy request | manual | Deploys an image tag to an environment. |

Tests for the scripts: `python3 -I -m unittest discover -s tests`.
Logic: `.github/scripts/pick.py` and `.github/scripts/pick_label.py`.
