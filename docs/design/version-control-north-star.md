# Version control in markdown-vault-mcp: a north star

> **Proposed, not adopted ([#2500](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2500)).** The account holder rediscusses this draft and adopts it as a whole. Questions only the account holder can settle are marked **Open**. The research behind it is on [#2462](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2462).

- **Status:** proposed north star.
- **Relates to:** the libgit2 backend (epic [#1313](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313)); database-backed history ([#1474](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1474)); submodules ([#1561](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1561), [#1562](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1562)); the decoupling study on [#1152](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1152); the [OKF north star](okf-north-star.md), whose multi-author rules this one must honour.

**A vault's history is served by one generalized version-control model, and git through libgit2 is one implementation of it.** The account holder's words: "a generalized version control, with libgit2 as one implementation" (25 November 2026). Everything that uses history works in terms of the model: the tools, the library, OKF and the command line. Which engine records that history is chosen per vault. It is not something every consumer has to know.

Two motivations meet here, both the account holder's, as the [roadmap](roadmap.md#reliable-git-integration-with-an-optional-backend) records their 13 September 2026 clarification. First, "libgit2 addresses the fragile CLI integration, with eventual retirement of that integration as the longer-term ambition". Second, "database-backed MCP-only/library-only operation is a distinct use case and an original reason for the abstraction". One model serves both, so neither motivation has to wait for the other.

## The model

Version control here means five things a vault can do with its history:

1. **Record** a change to the vault as a new revision, with who made it and why.
2. **Read** any document as it was at a past revision.
3. **Follow** a document's history, across renames.
4. **Compare** two revisions of a document or of the vault.
5. **Exchange** history with other copies: receive what they recorded, integrate it with what this copy recorded, and publish the result.

A revision identifies a state of the whole vault, and it is immutable once recorded. A document's content version, the etag and content hash a conditional write checks, identifies the bytes of one document. These are different things, and the model keeps them apart: a caller never has to guess which one it holds.

## Policy above the engines, mechanism below

The rules for *what* to record and *how* to integrate are decided once, above any engine:
- what a write records;
- when the server records on its own;
- how divergent histories are integrated;
- how a conflict surfaces;
- what is refused, and when.

An engine supplies the mechanism: storing content and states, comparing them, merging two states without touching the served files, moving the vault to a new state in one step, and transferring history to and from other copies. A second engine therefore implements primitives, not a second copy of the policy. A rule written once holds for every engine.

## Engines

- **Git, through libgit2.** It is the engine for git-backed vaults. The git command-line integration stays the default until libgit2 serves every repository shape the product supports. After that it is retired, as the account holder intends. Until then, no part of the server runs the git command line outside an engine.
- **No version control.** It is an engine in its own right: it records nothing, has no history, and says so. It replaces the scattered "is there git?" checks.
- **Database-backed history** ([#1474](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1474)) is another engine of the same model, if it is built. Git remains a likely fit wherever the vault is synchronised with Obsidian.

## Repository shapes

One policy holds for every instance: "a vault is a vault", as #1313 records the account holder's constraint. The product decides which shapes it supports. Each engine either serves a shape or refuses, at startup, with a message naming what to change. An engine's refusals are product decisions recorded here or on the engine's issue, never introduced by a pull request.

## Writers

The server is one writer among several: Obsidian with its git plugin, other clones of the vault, operators, and other jobs that commit. A local setting never establishes exclusive ownership; the [OKF north star](okf-north-star.md) says the same. The same vault content means the same thing whatever engine recorded it, or none. Where an engine has more evidence about how the content got there, it reports that evidence as evidence.

## Integrating divergence

Integration is computed away from the served files and applied in one step. A reader never sees a half-integrated vault, and a failed integration leaves the vault as it was ([#1313](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313)'s "reconciliation without a half-finished rebase").

> **Open (shape of history).** When two copies diverge, does integration produce a merge revision that keeps both lines, or rebase this copy's revisions onto the other's? Rebasing keeps history linear, but rewrites revisions the server may already have reported to a caller. A merge never rewrites, but its history has two parents. Both options suit the model.

## Conflicts

When both sides changed the same content, version control keeps both sides and reports the conflict. What to do with them depends on what the content is, and the subsystem that owns that kind of content decides. For OKF content that is the material table in the OKF north star. Where no owner has decided, the fallback keeps both sides as readable files and loses nothing.

## What callers see

Tools and the library speak of revisions, history, comparisons and synchronisation, not of git internals. Git terms appear where an operator configures git, and in evidence an engine reports as git evidence.

> **Open (git terms in tool results).** History tools return commit identifiers today. Should a caller see an opaque revision identifier that any engine can supply, with git identifiers carried as evidence? Or are git identifiers part of the model, because git-backed vaults are the main case?

## Open questions the account holder raised

> **Open (submodules).** Do submodules belong to this north star or to another one?
>
> - **(a) This one.** A submodule is a second history inside the vault, so the model would answer which history a path belongs to. It would also cover recording, reading and exchanging across several histories, and how far the server keeps a nested history current.
> - **(b) Another one,** about composing a vault from parts. Submodules would sit there beside other ways of bringing in content, such as the OKF north star's disjoint bundles.
>
> *Recommendation:* (a). The questions a submodule raises (who records a change to a path, how it is exchanged, what its history is) are version-control questions. Composition from separate bundles needs no second history.

> **Open (storage and version control).** Are storage and version control different things?
>
> - **(a) Yes, with a boundary.** Storage decides where content and derived data live, and how the server reaches them: files, indexes, vector stores, and database-owned content. Version control decides history and exchange, whatever holds the bytes. A database-owned vault is then a storage choice paired with a version-control engine, and each has its own north star ([#2463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2463) for storage).
> - **(b) No:** one north star for where content lives and how its history is kept.
>
> *Recommendation:* (a). The two change for different reasons and are replaced independently. A git-backed vault on plain files and a database-backed vault share the version-control model but not the storage.
