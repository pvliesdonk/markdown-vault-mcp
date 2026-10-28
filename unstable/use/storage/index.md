# Index storage and SQLite

With `MARKDOWN_VAULT_MCP_INDEX_PATH` set, the server keeps its search index in SQLite databases on disk:

- the keyword index at `MARKDOWN_VAULT_MCP_INDEX_PATH` (`index.db`);
- with the `sqlite` embedding store, the vectors beside it, or at `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` when that is set.

SQLite writes both in WAL mode, so each database comes with `-wal` and `-shm` files while it's open, plus a `.lock` file the server takes. The vault's notes are plain files and need none of this.

Without `INDEX_PATH` the index lives in memory, is rebuilt at every start, and nothing on this page applies. The Claude Code plugin and the Claude Desktop bundle set no index path, so they run that way unless you set one.

## Keep the index on a local disk

WAL needs every process that opens a database to share memory on one machine. In SQLite's words, "WAL does not work over a network filesystem." Put `MARKDOWN_VAULT_MCP_INDEX_PATH`, and `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` if you set it, on a disk local to the machine running the server, not on NFS, SMB or another network share. The vault itself may live elsewhere. The server doesn't check for this.

The keyword index can always be rebuilt from the notes with `markdown-vault-mcp reindex`. Paid embeddings are the part worth keeping on reliable storage ([Embeddings](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/use/embeddings/index.md)).

## One process writes an index

Only one process at a time writes a given index. While a server has it open, the index has an owner:

- `markdown-vault-mcp index`, `reindex` and `embeddings import` stop before writing anything. They exit with a message asking you to stop the server first, and leave the index unchanged.
- A second server started on the same `MARKDOWN_VAULT_MCP_INDEX_PATH` starts with the vault unavailable. Every tool says that another process is writing the index. The second server opens the vault within a few seconds of the first one exiting.
- `markdown-vault-mcp search` and other commands that only read are never blocked.

The lock is not taken on Windows. A lock across a network share or a virtual machine's shared folder may not hold, so in those setups make sure only one process writes the index yourself.

At shutdown, the server waits up to 30 seconds for indexing work in progress. It doesn't wait for work still running after that: it logs a warning, `index_close_skipped`, leaves the index open to that work, and the work stops when the process exits. What it had committed stays; run `markdown-vault-mcp reindex` if it was a long rebuild.

## Which SQLite your installation runs

The server uses Python's built-in `sqlite3`, so the SQLite library comes from the Python that runs it, and that Python comes from how you installed the server. At startup the server logs the version it got:

```
sqlite_runtime version=3.53.1
```

SQLite releases 3.7.0 to 3.51.2 have a bug that can corrupt a WAL database when two connections write or checkpoint it at the same instant; 3.51.3 and later fix it, as do the 3.50.7 and 3.44.6 backports. The project's analysis of the SQLite vector store found that its single writer never meets those conditions. A fixed SQLite removes the risk for every database, and the table shows which installations have one.

| How you installed it                                                                         | SQLite it brings                              | Fixed          |
| -------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------- |
| Docker image v5.1.0 (`latest` as of 2026-10-27)                                              | 3.46.1                                        | no             |
| Docker image built from the current Dockerfile (`edge` since 2026-10-27)                     | 3.53.1                                        | yes            |
| `uvx` or `uv tool install` (also the Claude Code plugin), on a Python uv downloaded recently | 3.53.1                                        | yes            |
| The same, on an older uv-managed Python uv already had                                       | 3.50.4 on older builds, such as Python 3.14.5 | no             |
| The same, with no uv-managed Python installed                                                | the system `python3`'s; see the rows below    | varies         |
| `pip` on a python.org installer for macOS or Windows (3.14)                                  | 3.50.4                                        | no             |
| `.deb` package, Debian 12 / 13                                                               | 3.40.1 / 3.46.1                               | no             |
| `.deb` package, Debian testing                                                               | 3.53.4                                        | yes            |
| `.deb` package, Ubuntu 24.04 / 26.04                                                         | 3.45.1 / 3.46.1                               | no, by version |
| `.rpm` package, Fedora 43 / 44                                                               | 3.50.2 / 3.51.2                               | no, by version |

The versions were observed on 2026-10-27. "No, by version" means the reported version is in the affected range; a distribution may have patched the fix in without changing it. The Claude Desktop bundle starts the server through uv, so the uv rows apply to it once it runs with an index path; whether Desktop uses its own copy of uv or yours is not recorded here.

The project doesn't bundle or check SQLite: the fix comes with the Python you run it on. uv prefers a Python already on the machine, its own managed builds first and then a system `python3`, over downloading one, and keeps using a Python it installed long ago. `uv python install 3.14` adds a current managed build, and `uv python upgrade` moves existing ones to their newest patch. The Docker image's SQLite comes from the uv-managed Python it was built with, and the Linux packages use the distribution's `python3` and its SQLite. In each case the `sqlite_runtime` line shows what you got.

The sources and the commands behind this table are in the project's design reference, `docs/design/reference/sqlite-runtime-by-channel.md`.
