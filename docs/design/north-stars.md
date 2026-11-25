# Subsystems governed by a north star

A north star is the adopted, whole-lifetime vision for one complex subsystem.
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

## Not yet governed

These subsystems are expected to get a north star. Each one's research issue
lists the decisions it would have to make:

| Subsystem | Research |
|---|---|
| Git integration | [#2462](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2462) |
| Storage and search | [#2463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2463) |
| The library and its public surface | [#2464](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2464) |
| The release system | [#2465](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2465) |
| Vault views | [#2466](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2466) |
| The documentation system | [#2467](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2467) |

A subsystem moves into the governed list in the change that adopts its north
star.

## When the north star does not support a decision

Stop the change; do not settle the decision in the session or the pull
request. Reopen the north star instead: open a research issue, audit where the
north star touches the code, and bring the findings and a proposed revision to
the account holder, who adopts it as a whole.
[#2459](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2459) is the
worked example.
