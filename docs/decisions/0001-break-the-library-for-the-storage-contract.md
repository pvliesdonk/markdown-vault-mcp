# 1. Break the library for the storage contract

Date: 2026-10-08. Status: accepted. Issues: #1503, #1765.

## Context

Package 015 needs a lifecycle and publication contract for selectable
embedding stores (#1503). The additive route kept the released `VectorStore`
and `Vault.close()` semantics and bolted the new behaviour beside them.
`Vault.close()` has closed an injected `git_strategy` and `on_write` since
v3.0.0, so a store or strategy shared with another consumer would be closed
under it.

The owner set the frame on 8 October 2026: "We want the best future proof
architecture. And that may be a breaking change. Especially since this [is]
library only, and nobody uses the library except ourselves and milestone 020
is about promoting the library to real first class." And on shared parts:
where "we already may know *we are going to need it*", build them now rather
than a shortcut because #1474 owns the content store.

## Decision

Break the Python library where the contract needs it, and ship 015 as a
major. The operator surface upgrades automatically; the MCP surface changes
only additively.

The first break is the ownership rule (#1765): the vault closes what it
opens; the caller closes what it injects. The vault closes its index, its
writer and its write-callback queue. An injected git strategy, write
callback, embedding provider or vector store is closed by whoever built it,
after the vault. The server and the CLI do this through
`VaultInstances.close()`.

The same rule applies to every store backend the contract adds, so a
backend sharing a connection pool with another consumer is never closed
under it.

## Consequences

- A library caller that relied on `Vault.close()` closing its git strategy
  must close it itself, after the vault, or the final push never flushes.
  `Vault.close()` still stops the pull loop it started.
- The ordering is fixed: vault first (drains commits), then the strategy
  (flushes the push).
- Later breaks in the same package (the vector protocols, #1767) follow the
  same decision and need no new record.
