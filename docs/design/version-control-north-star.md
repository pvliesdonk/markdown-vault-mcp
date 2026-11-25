# Version control in markdown-vault-mcp: a north star

- **Status:** adopted north star. The account holder settled its open questions on 25 November 2026 and adopted it as a whole by merging [#2501](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2501). It follows spike [#2462](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2462).
- **Relates to:** the libgit2 backend (epic [#1313](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313)); database-backed history ([#1474](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1474)); submodules ([#1561](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1561), [#1562](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1562)); the decoupling study on [#1152](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1152); the [OKF north star](okf-north-star.md), whose multi-author rules this one must honour.

**A vault's history is served by one generalized version-control model, and git through libgit2 is one implementation of it.** The account holder's words: "a generalized version control, with libgit2 as one implementation" (25 November 2026). Everything that uses history works in terms of the model: the tools, the library, OKF and the command line. Which engine records that history is chosen per vault. It is not something every consumer has to know.

Two motivations meet here, both the account holder's. The [roadmap](roadmap.md#reliable-git-integration-with-an-optional-backend) records their 13 September 2026 clarification, in its own words rather than theirs. First, libgit2 is to address the fragile command-line integration, with eventual retirement of that integration as the longer-term ambition. Second, database-backed operation for MCP-only and library-only use is a distinct use case and an original reason for the abstraction. One model serves both, so neither motivation has to wait for the other.

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

One policy holds for every instance: a vault is a vault, as #1313 records the account holder's constraint in its own words. The product decides which shapes it supports. Each engine either serves a shape or refuses, at startup, with a message naming what to change. An engine's refusals are product decisions recorded here or on the engine's issue, never introduced by a pull request.

## Writers

The server is one writer among several: Obsidian with its git plugin, other clones of the vault, operators, and other jobs that commit. A local setting never establishes exclusive ownership; the [OKF north star](okf-north-star.md) says the same. The same vault content means the same thing whatever engine recorded it, or none. Where an engine has more evidence about how the content got there, it reports that evidence as evidence.

## Integrating divergence

Integration is computed away from the served files and applied in one step. A reader never sees a half-integrated vault, and a failed integration leaves the vault as it was ([#1313](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313)'s "reconciliation without a half-finished rebase").

Integration never rewrites a recorded revision. When two copies diverge, it records a merge revision that keeps both lines (account holder, 25 November 2026: "never rewrite"). A revision a caller holds therefore stays valid: an overwriting write returns the revision of the content it replaced (#1137), and the history tools return revisions too. History follows each line through its parents. Linearity is not promised.

## Conflicts

When both sides changed the same content, version control keeps both sides and reports the conflict. What to do with them depends on what the content is, and the subsystem that owns that kind of content decides. For OKF content that is the material table in the OKF north star. Where no owner has decided, the fallback keeps both sides as readable files and loses nothing.

## What callers see

Callers get the model's behaviour and nothing of the engine behind it. A caller relies on what the model promises: a revision identifies a vault state, a past document can be read back, and history follows renames. It is not told which engine keeps the history, and nothing invites it to infer one. An engine's own terms, output and configuration stay inside the server, including git's command output, remote addresses and branch names. The account holder: "Probably the less exposed the better. An llm will make assumptions about behaviour that may not be true" (25 November 2026). A revision identifier is opaque to the caller: on a git-backed vault it may be a commit identifier, but the caller is not told so and does not need to know. Git appearing in a result is tolerated, never required, and an engine that is not git never imitates git's terms. An operator may describe the deployment in its own words, including where the vault's documents are published, so that links between instances resolve. That is the operator's advice, which the server neither derives from its engine nor verifies ([#2169](https://github.com/pvliesdonk/markdown-vault-mcp/pull/2169)).

## Nested histories

A vault may contain nested repositories, such as git submodules. Each one is a history of its own inside the vault, and this north star covers them (account holder, 25 November 2026). Each path belongs to exactly one history, and the model answers which one. Reading, following history and comparing work on a nested history as on the vault's own. The destination includes writing into a nested history, as epic [#1562](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1562) plans: the change is recorded in the nested history, that history is published, and the outer history records the nested one's new state. A change that crosses both is applied completely or reported as incomplete, and never left half-recorded without a report. Until writes are designed, a write into a nested history is refused rather than lost, as it is today.

## Version control and storage

Version control and storage are different things (account holder, 25 November 2026). Storage decides where content and derived data live and how the server reaches them: files, indexes, vector stores, and database-owned content. It is the subject of its own north star ([#2463](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2463)). Version control decides history and exchange, whatever holds the bytes. A database-owned vault is a storage choice paired with a version-control engine. A git-backed vault on plain files and a database-backed vault share this model, not their storage.
