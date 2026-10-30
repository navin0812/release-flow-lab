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

## Release-manager workflows

| Workflow | Who | What |
| --- | --- | --- |
| Pick decision | PR check | PR to `main` needs `pick/release/vX.Y.Z` or `pick/none` |
| Prepare rc | release manager | sync `release/vX.Y.Z` to its label, tag `vX.Y.Z-rc.N`, draft pre-release |
| Merge forward | release manager | merge the tag prd runs into an open release branch (`release/vX.Y.Z`) |
| UAT sign-off | release manager | mark the image UAT runs as passed |
| On deploy request | anyone, prd needs approval | prd only takes a signed-off image (or `emergency_reason`) |
| Finalize release | release manager | tag `vX.Y.Z` on the commit prd runs, draft release, create the next pick labels (next patch, minor, major) |
| Release status | anyone | what each env runs, state of each pick label |

Release managers: repo variable `RELEASE_MANAGERS` (comma-separated logins).
State lives in GitHub: labels, Deployments, the `uat-signoff` commit status.
Logic: `.github/scripts/release.py`.

Steps to follow: the Runbook in the owner's notes.
