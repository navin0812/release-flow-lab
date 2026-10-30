# release-flow-lab

Test repo for a cherry-pick + label release flow. It mirrors three production
workflows (`on-push`, `on-release`, `on-deploy-request`) as closely as possible
and adds release-manager workflows. Every difference from production is marked
`# lab:` in the workflow files.

| Production | Lab stand-in |
| --- | --- |
| ECR | GHCR (`ghcr.io/<owner>/<repo>`) |
| ECS + CodeDeploy | `ecs_deploy` renders the task definition, records a GitHub Deployment |
| Self-hosted runner | `ubuntu-latest` |
| Org deploy-actions | `.github/actions/*` with the same inputs |

## Release flow

```
Release flow → start   release/vX.Y.Z from the latest prd tag; label PRs not yet in prd
merge PRs              pick-decision adds pick/release/vX.Y.Z on open (pick/none opts out)
Release flow → uat     pick labelled PRs, revert unlabelled ones, tag vX.Y.Z-rc.N, build image
On deploy request      environment uat, image_tag vX.Y.Z-rc.N-amd64
Release flow → prd     same sync, tag vX.Y.Z (Latest), build image, delete branch + label
On deploy request      environment prd, image_tag vX.Y.Z-amd64
```

| Workflow | Trigger | What |
| --- | --- | --- |
| Pick decision | open PR to `main` (required check) | On open, adds the label of the open mainline release. Passes with `pick/release/vX.Y.Z` or `pick/none`. |
| Release flow | manual: `target` start / uat / prd | See above. `version` is optional. `exclude_prs` drops merged PRs from the release. `dry_run` pushes nothing. |
| On release | release published, or dispatched by Release flow | Builds `<tag>-amd64`. |
| On deploy request | manual | Deploys an image tag to an environment. |

Rules:

- **Version.** `start` with no version → next major after the latest prd tag. Type `X.Y.Z+1` for a hotfix.
- **Mainline vs hotfix.** Mainline = open branch with patch 0; new PRs get its label. Hotfix PRs are labelled by hand.
- **Drop a PR.** Remove its pick label (works on merged PRs too), or list it in `exclude_prs`, then run `uat` or `prd`. The pick is reverted. The next `start` labels it again unless it has `pick/none`.
- **Conflict.** The run stops, pushes nothing, and labels the PR `pick-conflict/release/vX.Y.Z`.
- **prd without UAT.** If the branch changed after the last rc, `prd` still ships and warns in the release notes.

Release managers: repo variable `RELEASE_MANAGERS` (comma-separated logins).
State lives in GitHub: tags, `release/v*` branches, PR labels, `-x` cherry-pick trailers.
Logic: `.github/scripts/release.py`.
