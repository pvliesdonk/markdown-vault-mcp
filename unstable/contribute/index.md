# Contribute

These pages are for whoever changes the project, including the agent working on a contributor's behalf. The rules for issues and pull requests, and where a fix belongs, are in `CONTRIBUTING.md`; `AGENTS.md` carries the conventions and the gates every pull request passes; the skills under `.agents/skills/` carry the task procedures. `SECURITY.md` says how to report a vulnerability privately, and the [security model](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/security-model/index.md) is the page such a report argues against.

## Local development

The pull-request gate, the same commands CI runs:

```
uv sync --all-extras --all-groups                    # dev and docs tooling
uv run pre-commit install                            # hooks, once per clone
uv run pytest -x -q                                  # tests
uv run ruff check --fix . && uv run ruff format .    # lint and format
uv run mypy src/ tests/                              # type-check
```

CI runs the tests on Python 3.11 through 3.14; 3.14 also collects branch coverage and enforces the 80% total and patch coverage thresholds. To reproduce that run: `uv run --python 3.14 pytest --cov --cov-report=xml --durations=20`.

### Moving a clone

`uv sync` writes `.venv/bin/*` scripts with absolute shebangs. After moving the repository, `uv run pytest` fails with `ModuleNotFoundError: No module named 'fastmcp'`. Recreate the environment:

```
rm -rf .venv
uv sync --all-extras --all-groups
```

### `uv.lock` after a template update

When a template update adds a dependency, the `uv sync --locked` step in CI fails against the stale lockfile. Run `uv lock` and commit the refreshed `uv.lock` with the update. CI never rewrites `uv.lock` in its own workspace, so lockfile drift shows as a red install step with a clear message.

## After scaffolding

Once, after `copier copy` and `gh repo create --push`:

1. Fill in the `DOMAIN-*` blocks in `README.md` and `AGENTS.md`. The `GENERATED-*` regions are not yours: the generators own them and rewrite them on every run.
1. Configure the GitHub secrets below.
1. Install the tooling and the hooks (the first two commands above).
1. Run the gate; push the first commit. CI should be green.

## GitHub secrets

The workflows read two required repository secrets and one optional token. Configure them under **Settings → Secrets and variables → Actions**, or with `gh secret set`:

| Secret                    | Used by                                                                                    | How to generate                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RELEASE_TOKEN`           | `release-prepare.yml`, `release.yml`, `copier-update.yml`, `renovate.yml`, `bootstrap.yml` | A fine-grained personal access token from <https://github.com/settings/personal-access-tokens/new> with `contents: write`, `pull_requests: write` and `administration: write` (bootstrap applies the repository rulesets, auto-merge, the security settings and the About block). It must belong to a repository admin: the shipped rulesets grant bypass to the admin role, and the tag and GitHub release that knope creates after a release pull request merges rely on it. Scoped to this repository. |
| `SONAR_TOKEN`             | `ci.yml`                                                                                   | <https://sonarcloud.io>: after importing the repository, open **Administration → Analysis Method**. Turn Automatic Analysis off first, because SonarQube Cloud refuses a CI scan while it is on; choosing GitHub Actions then shows the token. Until the secret exists, CI skips the scan.                                                                                                                                                                                                                |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude.yml`                                                                               | Optional. Run `claude setup-token` locally; configure it only for `@claude` or opted-in automatic review.                                                                                                                                                                                                                                                                                                                                                                                                 |

```
gh secret set RELEASE_TOKEN
gh secret set SONAR_TOKEN
# Optional: enables @claude and opted-in automatic review.
gh secret set CLAUDE_CODE_OAUTH_TOKEN
```

`GITHUB_TOKEN` is provided by GitHub; nothing to configure. Dependency updates come from Renovate (`renovate.yml`), which reuses `RELEASE_TOKEN`, maintains `uv.lock` and merges patch and minor bumps once `CI Success` is green. `bootstrap.yml` enables auto-merge, applies the rulesets under `.github/rulesets/`, turns on private vulnerability reporting and Dependabot alerts, and fills the repository's About block from `pyproject.toml` on the first push; [repository protection](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/repository-protection/index.md) has the per-branch posture. GitHub Actions versions are updated in the template and arrive through template updates, not per repository.

## The other pages here

- [Documentation structure](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/docs-structure/index.md): where documentation goes and who owns it.
- [Release process](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/release-process/index.md): how a release is cut and what it publishes.
- [Template updates](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/template-updates/index.md): applying the weekly template update.
- [Repository protection](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/repository-protection/index.md): rulesets, required checks and bypass.
- [Integration branches](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/contribute/integration-branches/index.md): landing an epic as one merge.
