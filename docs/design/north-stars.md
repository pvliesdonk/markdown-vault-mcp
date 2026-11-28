# Subsystems governed by a north star

A north star is the adopted, whole-lifetime vision for one complex subsystem:
where it is going and the major road there, in as much detail as that needs.
It does not settle every decision.
A change to a subsystem listed here is made against its north star. Where the
north star and `design.md` differ, the north star says where the subsystem is
going and `design.md` says what is built today.

## Governed subsystems

### OKF

- **North star:** [okf-north-star.md](okf-north-star.md), adopted on
  11 September 2026 and again on 25 November 2026, after it was reopened
  ([#2459](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2459)).
- **Under it:** [okf-roadmap.md](okf-roadmap.md) sequences the work.
  [okf.md](okf.md) records the earlier design; it is historical wherever it
  conflicts with the north star.
  [reference/okf-v0.3.md](reference/okf-v0.3.md) records what the external
  specification says.
- **Governs these modules** in `src/markdown_vault_mcp/`: `okf.py`,
  `_okf_write.py`, `_okf_convention.py`, `_okf_log_repair.py`, `okf_bundle.py`,
  `managers/okf_migrate.py`, `citations.py`, `managers/citations.py`,
  `_frontmatter_splice.py`, `_trivial_edit.py` and `_yaml12.py`.
- **Governs OKF behaviour anywhere else:**
  - code that branches on OKF detection or the OKF settings (`OkfDetector`,
    `okf_mode`, `okf_write`, `okf_verify`);
  - the tools tagged `okf` or `okf-enforce`;
  - the OKF server-instructions snippet;
  - link resolution where OKF decides a link's meaning;
  - how reserved files (`index.md`, `log.md`) are written, recovered or
    merged;
  - the user guide [docs/use/okf.md](../use/okf.md).

### Version control

- **North star:** [version-control-north-star.md](version-control-north-star.md), adopted on 25 November 2026 by merging [#2501](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2501), after spike [#2462](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2462).
- **Under it:** epic [#1313](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313) (the libgit2 engine) and the roadmap's "Reliable Git integration" and "Vaults with git submodules" sections sequence the work.
- **Governs these modules** in `src/markdown_vault_mcp/`: the `git/` package, `managers/git_query.py`, `_tools/git.py`, `write_callback.py`, `_commit_scope.py`, `_server_commit_scope.py` and `_webhooks.py`.
- **Governs version-control behaviour anywhere else:**
  - code that branches on whether a vault has version control, or on the git settings;
  - the history, revision-read and sync tools;
  - refusals of writes into nested repositories;
  - the user guide's git pages.

## Not yet governed

These subsystems are to get a north star. Each one's research issue lists the
decisions it would have to make, and its writing issue carries the scope the
account holder chose:

| Subsystem | Research | North star |
|---|---|---|
| Storage and search | [#2463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2463) | [#2538](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2538) |
| The library and its public surfaces | [#2464](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2464) | [#2540](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2540) |
| The release system | [#2465](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2465) | [#2547](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2547) |
| The documentation system | [#2467](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2467) | [#2557](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2557) |

A subsystem moves into the governed list in the change that adopts its north
star.

## Deliberately ungoverned

### Vault views

The MCP app and its visual surfaces have no north star. In the account
holder's words, 28 November 2026: "The visual surface does not have an actual
*vision*.  It has been created ad-hoc." They chose to omit a north star and
record why ([#2466](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2466)).
A change to vault views is judged on its own issue's outcome. A north star is
written if a vision forms.

## Silent, settled or ruled out

The `working-under-a-north-star` skill
([.agents/skills/working-under-a-north-star](../../.agents/skills/working-under-a-north-star/SKILL.md))
classifies each decision a change needs:
- **Silent:** the north star does not cover the question. Decide it as an
  implementation choice and state it in the pull request.
- **Settled:** follow the north star.
- **Ruled out:** the change would contradict the north star, or the road
  ahead it sets is unclear. Stop and reopen it as a whole, by the method of
  [#2459](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2459).
