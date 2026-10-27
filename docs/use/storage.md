---
description: "What the server's index files need from the storage they sit on, why one process writes them at a time, and which SQLite each installation method brings."
kind: explanation
---

# Index storage and SQLite

The server keeps its search index in SQLite databases next to each other:

- the keyword index at `MARKDOWN_VAULT_MCP_INDEX_PATH` (`index.db`);
- with the `sqlite` embedding store, the vectors beside it.

SQLite writes both in WAL mode, so each database comes with `-wal` and `-shm` files while it's open, plus a `.lock` file the server takes. The vault's notes are plain files and need none of this. Everything on this page is about the index.

## Keep the index on a local disk

WAL needs every process that opens a database to share memory on one machine. In SQLite's words, "WAL does not work over a network filesystem." Put `MARKDOWN_VAULT_MCP_INDEX_PATH` on a disk local to the machine running the server, not on NFS, SMB or another network share. The vault itself may live elsewhere. The server doesn't check for this.

The index can always be rebuilt from the notes with `markdown-vault-mcp reindex`. Paid embeddings are the part worth keeping on reliable storage ([Embeddings](embeddings.md)).

## One process writes an index

Only one process at a time writes a given index. While a server has it open, the index has an owner:

- `markdown-vault-mcp index`, `reindex` and `embeddings import` stop before writing anything. They exit with a message asking you to stop the server first, and leave the index unchanged.
- A second server started on the same `MARKDOWN_VAULT_MCP_INDEX_PATH` starts with the vault unavailable. Every tool says that another process is writing the index. The second server opens the vault within a few seconds of the first one exiting.
- `markdown-vault-mcp search` and other commands that only read are never blocked.

The lock is not taken on Windows. A lock across a network share or a virtual machine's shared folder may not hold, so in those setups make sure only one process writes the index yourself.

At shutdown, the server waits up to 30 seconds for indexing work in progress. Work that takes longer is left to finish rather than cut off, and the server logs `index_close_skipped`.

## Which SQLite your installation runs

The server uses Python's built-in `sqlite3`, so the SQLite library comes from the Python that runs it, and that Python comes from how you installed the server. At startup the server logs the version it got:

```text
sqlite_runtime version=3.53.1
```

SQLite releases 3.7.0 to 3.51.2 have a bug that can corrupt a WAL database when two connections write or checkpoint it at the same instant; 3.51.3 and later fix it, as do the 3.50.7 and 3.44.6 backports. With one owner per index, the server writes each database from one thread, so those conditions don't arise. A fixed SQLite still removes the risk entirely, and the table shows which installations have one.

| How you installed it | SQLite it brings | Fixed |
|---|---|---|
| Docker image v5.1.0 (`latest` as of 2026-10-27) | 3.46.1 | no |
| Docker image built after v5.1.0 (`edge`, and releases after v5.1.0) | 3.53.1 | yes |
| `uvx`, `uv tool install`, the Claude Code plugin or the Claude Desktop bundle, on a Python uv downloaded recently | 3.53.1 | yes |
| The same, on an older uv-managed Python uv already had | 3.50.4 on older builds, such as Python 3.14.5 | no |
| `pip` on a python.org installer for macOS or Windows (3.14) | 3.50.4 | no |
| `.deb` or `.rpm` package, Debian 12 / 13 | 3.40.1 / 3.46.1 | no |
| `.deb` package, Debian testing | 3.53.4 | yes |
| `.deb` package, Ubuntu 24.04 / 26.04 | 3.45.1 / 3.46.1 | no, by version |
| `.rpm` package, Fedora 43 / 44 | 3.50.2 / 3.51.2 | no, by version |

The versions were observed on 2026-10-27. "No, by version" means the reported version is in the affected range; a distribution may have patched the fix in without changing it.

The project doesn't bundle or check SQLite. To get a fixed one:

- **Docker:** use an image newer than v5.1.0.
- **uv:** run `uv python upgrade`, then check the `sqlite_runtime` line after the next start. uv uses a Python it already has before it downloads a new one, so an old build stays in use until you upgrade it.
- **Linux packages:** the packages use the distribution's `python3` and its SQLite, so the fix arrives with the distribution.

The sources and the commands behind this table are in the project's design reference, `docs/design/reference/sqlite-runtime-by-channel.md`.
