---
description: "Run the PostgreSQL embedding store in production: provisioning, TLS, poolers, timeouts, space, backup and status."
kind: how-to
---

# PostgreSQL embedding store

With `MARKDOWN_VAULT_MCP_EMBEDDING_STORE=postgres`, a vault keeps its embeddings in a PostgreSQL schema and the [pgvector](https://github.com/pgvector/pgvector) extension scores them. [Embeddings](embeddings.md#keeping-the-embeddings-in-postgresql) says what the store is and when to choose it. This page is for the operator who runs it, from the first `CREATE SCHEMA` to the log line that says something went wrong.

The store supports PostgreSQL 15 to 18 with pgvector 0.7.0 or later (17 with pgvector 0.7.4 or later and 18 with pgvector 0.8.7 or later, the lowest versions built for them), the range a CI workflow tests on every change to the store. A pip or uv install needs the `postgres` extra (`pip install "markdown-vault-mcp[postgres]"`); without it the server refuses to start and names the install command. The Docker image, the Claude Desktop bundle and the Claude Code plugin install `[all]`, which includes it.

## Provision the database

Run this once per vault, as a superuser, in the database the connection string will name (`psql -d vectors`; create it first with `CREATE DATABASE vectors;` if it does not exist):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE vault_a LOGIN;
GRANT CONNECT ON DATABASE vectors TO vault_a;
CREATE SCHEMA vault_a AUTHORIZATION vault_a;
```

Then give the role its password with `\password vault_a` in `psql`, so the password never sits in a script or in your shell history.

The server never creates the extension: pgvector is not a trusted extension, so only a superuser can install it. The server creates its three tables (`vector_meta`, `vector_documents`, `vector_chunks`) in the schema on its first write, and touches nothing else. Each vector is stored with its chunk's text, title and path, so the schema, and every dump of it, holds a copy of the vault's embedded notes: protect it as you protect the vault.

The role must also be able to use the schema the extension lives in (`\dx vector` shows which). For `public` it can, unless `USAGE` was revoked from `PUBLIC` there. For any other schema, grant it:

```sql
GRANT USAGE ON SCHEMA extensions TO vault_a;
```

Without it the server refuses to open the store and its log names the `GRANT` to run.

**One role and one schema per vault, never shared.** The schema is the vault's ownership boundary. The server's writer lease stops two running servers from writing one schema at once, and the stored embedding model stops a server with another model from mixing its vectors in. What they miss is a copied configuration: a second vault pointed at the first one's schema while the first is stopped. Its reconciliation finds none of the first vault's notes in its own folder and deletes their rows. A role that owns only its own vault's schema cannot be pointed at another one, as long as each vault keeps its own credentials: copying a whole connection string copies its access too.

The role needs `CONNECT` on the database, ownership of its schema, and `USAGE` on the extension's schema. Give it nothing more:

- no superuser, `CREATEDB` or `CREATEROLE` attribute;
- no `CREATE` on the database, so it cannot add schemas;
- no ownership of, or privileges on, another vault's schema.

Ownership already lets the role create and drop its tables, and it needs no `GRANT` on them. Two defaults reach every role through `PUBLIC`: `CONNECT` on every database in the cluster and `TEMPORARY` on each. Where other databases share the cluster, revoke the first on them (`REVOKE CONNECT ON DATABASE other_db FROM PUBLIC;`), granting it back to the roles that need it.

## Connect securely

The server reads the connection from `MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_DSN`, a libpq connection string or URI. Ask for TLS with server verification:

```bash
MARKDOWN_VAULT_MCP_EMBEDDING_STORE=postgres
MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_DSN='postgresql://vault_a@db.example:5432/vectors?sslmode=verify-full&sslrootcert=/etc/markdown-vault-mcp/db-ca.pem'
MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_SCHEMA=vault_a
```

`sslmode=verify-full` requires TLS, a certificate signed by the CA in `sslrootcert` and a host name that matches it. libpq's default, `prefer`, falls back to an unencrypted connection without saying so.

Keep the password out of the connection string, in a password file:

```text
db.example:5432:vectors:vault_a:the-password
```

libpq reads it from `~/.pgpass` of the user the server runs as, or from the file `PGPASSFILE` names, only when the file is readable by its owner alone (`chmod 0600`), and only when no password was given another way: an exported `PGPASSWORD` wins over the file. `PGPASSWORD` works on its own too, but other users on the host may be able to read a process's environment.

The connection string is a secret: the server never logs it or shows it, and `embeddings_status` reports only the host, port and database libpq resolved from it.

## Connection poolers

A pooler such as PgBouncer can sit in front of PostgreSQL only in **session pooling** mode. The store relies on two things that transaction and statement pooling don't carry across transactions:

- the writer lease is a session-level advisory lock, held for as long as the server runs;
- each connection sets its `search_path` and timeouts once, with `SET`, when it opens.

Under transaction pooling a later transaction may run on a PostgreSQL connection that holds neither the lock nor the settings, and PgBouncer lists both session-level advisory locks and `SET` as never supported in that mode. The store's own pool already keeps its connections open, so a pooler in front of it adds little beyond a cap on the database's total connections.

## Connections and timeouts

Each server holds up to `MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_POOL_SIZE` connections in its pool, plus one for its writer lease, and one more for a moment while it opens the store. Count that for every server against PostgreSQL's `max_connections`, alongside everything else using the database.

The store bounds how long it waits:

| Wait | Limit |
| --- | --- |
| Connecting | 10 seconds, unless the connection string or `PGCONNECT_TIMEOUT` sets `connect_timeout` |
| Waiting for a free pool connection | 30 seconds |
| One query | `MARKDOWN_VAULT_MCP_EMBEDDING_POSTGRES_STATEMENT_TIMEOUT_S` |
| A write waiting for the lock another write holds | 10 seconds |
| The bulk write of a full rebuild | none |

A full rebuild writes every chunk in one transaction, which can take minutes on a large vault, so it runs without a statement timeout; TCP keepalives still notice a connection that died. The defaults and limits of both variables are in the [configuration reference](../reference/configuration.md).

Every connection names itself `markdown-vault-mcp:<schema>` in `pg_stat_activity` (unless the connection string or `PGAPPNAME` names it otherwise), so you can tell which vault a session belongs to.

## Space

The store deletes rows rather than truncating tables: a rebuild deletes the whole previous generation, and every changed note deletes its old chunks. A deleted row's space is not freed at once. `VACUUM` makes it reusable by new rows, and autovacuum, on by default, runs it once a table's deleted rows pass 50 plus a fifth of its rows. Right after a rebuild the tables can take about twice their live size. The next rebuild reuses that space; the disk never shrinks on its own.

`embeddings_status` reports the store's size as `stored_bytes`, the tables with their indexes. To hand space back to the operating system after a large deletion, run this in a maintenance window, as the vault's role or a superuser:

```sql
VACUUM FULL vault_a.vector_chunks;
```

`VACUUM FULL` writes a compacted copy of the table, so it needs room for that copy until it finishes. It also locks the table against every reader while it runs. A search waits behind it until the statement timeout passes and then fails; with the timeout at 0 it waits as long as the vacuum takes.

## Backup and restore

Back up one vault's schema with `pg_dump`, which does not block the server while it runs:

```bash
pg_dump -h db.example -U postgres -d vectors -n vault_a -Fc -f vault_a.dump
```

A schema dump holds the schema, its tables and their rows, but not the `vector` extension they depend on. To restore, stop the server, and as a superuser make sure the extension exists in the target database, remove the schema if it is still there, and restore:

```bash
psql -h db.example -U postgres -d vectors -c 'CREATE EXTENSION IF NOT EXISTS vector;'
psql -h db.example -U postgres -d vectors -c 'DROP SCHEMA IF EXISTS vault_a CASCADE;'
pg_restore -h db.example -U postgres -d vectors vault_a.dump
```

The restore recreates the schema owned by `vault_a`. In a new database, create the role and grant it `CONNECT` first, as in [Provision the database](#provision-the-database).

The embeddings are derived from the notes, so a restored dump older than the notes is fine. On its next start the server compares the stored notes with its keyword index. Notes changed or added since the dump are embedded again, and the rows of notes deleted since are removed; nothing else is re-embedded.

Without a backup, recreate the empty schema as in [Provision the database](#provision-the-database). The next start embeds every note again, which a paid provider bills. Until the schema exists, the server serves keyword search, and semantic and hybrid searches fail.

## Durability

Each write commits with `synchronous_commit` at least `on`, whatever the role or database sets, so a write the server reports done survives a PostgreSQL crash. A stronger setting such as `remote_apply` is kept.

PostgreSQL run with `fsync = off` can lose committed writes after a crash, and can corrupt the database. A writing server checks for it when it opens the store and logs `postgres_vector_store_not_durable` if so. The store still works, but its writes are only as safe as that setting.

## Reading status and logs

`embeddings_status` reports, for this store:

- `backend`: `"postgres"`;
- `search_mode`: `"streamed"`, since the database scores every search;
- `stored_bytes`: the size of the store's tables and indexes;
- `store_details`: the `host`, `port`, `database` and `schema`, the `postgresql_version` and `pgvector_version`, `writer` (whether this server writes the store) and `lease_held` (whether it still holds the writer lease). A writer that reports `lease_held: false` takes the lease back on its next write.

The server's log carries these events:

| Event | Level | Meaning |
| --- | --- | --- |
| `postgres_vector_store_opened` | INFO | The store opened; `read_only` and `fresh` say how. |
| `postgres_vector_store_lease_acquired` | DEBUG | This server became the writer of the schema; `pid` is its lease session in `pg_stat_activity`. |
| `postgres_vector_store_lease_lost` | WARNING | The lease session ended (a restart of PostgreSQL, an administrator's `pg_terminate_backend`); the next write takes it back. |
| `postgres_vector_store_lease_reacquired` | INFO | The lease is held again. |
| `postgres_vector_store_unavailable` | WARNING | PostgreSQL could not serve a request; `reason` says why (below). |
| `postgres_vector_store_read_retried` | WARNING | A search lost its connection and was retried once on a fresh one. |
| `postgres_vector_store_reset` | INFO | A rebuild emptied the store's tables (`how=emptied`), or created them, the first time or over damaged ones (`how=recreated`). |
| `postgres_vector_store_generation_published` | INFO | A full rebuild committed. |
| `postgres_vector_store_not_durable` | WARNING | PostgreSQL runs with `fsync = off`. |
| `embeddings_status_store_not_counted` | INFO or WARNING | Status counted no vectors, because the store holds another model's vectors (INFO, rebuilt on the next build) or its tables are damaged (WARNING). |

The `reason` of `postgres_vector_store_unavailable`:

| `reason` | What to check |
| --- | --- |
| `connect` | The host, the port, the network, or whether PostgreSQL is running; also a connection that dropped. PostgreSQL out of resources (a full disk, no memory), shutting down, or with every slot under `max_connections` taken (`too many clients already`) is reported here too, and the `detail` carries its message. |
| `timeout` | The connect timeout, a query that passed the statement timeout, or a write waiting longer than 10 seconds for a lock. |
| `no_password` | PostgreSQL asked for a password and none was found. Check the password file and its `0600` mode. |
| `refused` | PostgreSQL turned the connection down. Check the password and the role name, then that the database exists and the role has `CONNECT` on it. PostgreSQL also refuses a connection when the only slots left are reserved for other roles (`remaining connection slots are reserved`); the message says which. |
| `privilege` | The role lacks a privilege. For the extension's schema, the message names the `GRANT` to run. |
| `pool` | No pool connection became free within 30 seconds, or the pool could not connect. |
| `lease` | Another server writes this schema; the message names its `application_name` and session. |
| `commit_unknown` | The connection dropped while a write was committing, so it may or may not have landed; the next reconciliation brings the store in line with the notes either way. |
| `error` | Any other PostgreSQL error; the message carries its SQLSTATE. |

A missing schema, or a missing `vector` extension, is reported as not configured rather than unavailable, and the message gives the SQL that fixes it.
