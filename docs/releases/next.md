# Next release

<!-- notes-range-end: b2b4c9d94f715fa2cb8923d0093e2638a39b7007 -->

<!-- RELEASE-SUMMARY NEXT START -->
This release adds one opt-in feature and fixes what the 5.0 surface got
wrong. With `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`, `write`,
`edit` and `append` name the links they just added that resolve to no
note. A configured embedding provider now turns on search by meaning
without `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`, and the change-tracking file
no longer defaults into the vault. Conventions files reach the model whole
up to 32,768 characters, and every shipped prompt reads them before it
picks a folder. The documentation site is reorganised by what a reader
is trying to do, and its reference pages are generated from the code.
Read [Upgrading](#upgrading) if you set an embedding provider.
<!-- RELEASE-SUMMARY NEXT END -->

## Upgrading

1. **If you set `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` but not
   `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`:** in 5.0 the provider was ignored
   and the server searched by keyword only. Now it embeds the vault with
   that provider at start. That includes a Claude Desktop bundle or Claude
   Code plugin install where you filled in the embedding provider field,
   and a plain `docker run` of the image. With
   `MARKDOWN_VAULT_MCP_INDEX_PATH` set, the vectors are stored beside the
   index and the vault is embedded once. Without it, the vectors are held
   in memory and the whole vault is embedded again at every start, which
   OpenAI and Voyage bill each time. Set `MARKDOWN_VAULT_MCP_INDEX_PATH`
   to keep them, or clear the provider to stay on keyword search
   ([#1708](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1708)).
   The shipped `compose.yml` sets both paths and is not affected.
2. **If you never set `MARKDOWN_VAULT_MCP_STATE_PATH`:** nothing is
   required. With `MARKDOWN_VAULT_MCP_INDEX_PATH` set, the first start
   copies the change-tracking file out of the vault's
   `.markdown_vault_mcp/` folder to its new place beside the index.
   Without it, the state is held in memory and the old folder is simply
   no longer used. Either way the server logs `legacy_state_file_unused`
   at each start while the folder is there, and the folder is then safe
   to delete. To keep the old location instead, set
   `MARKDOWN_VAULT_MCP_STATE_PATH` to it
   ([#1693](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1693)).
3. **If you run the shipped `compose.yml`:** check that `.env` still sets
   `MARKDOWN_VAULT_MCP_SOURCE_DIR`. In 5.0 Compose refused to start
   without it; it now starts and serves an empty `./vault` folder beside
   `compose.yml`
   ([#1707](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1707)).

**Clients:** nothing to do beyond reconnecting. No tool, prompt or argument
was renamed or removed. A client picks up the changed prompt text and tool
descriptions when it reconnects after the restart. On a read-only server the example `zettelkasten` prompt is no longer
listed.

**State:** kept. The text index is not rebuilt: the rules that turn notes
into index rows did not change. The change-tracking file moves beside the
index (step 2), so under Compose it is now
`/data/state/index.db.state.json` on the `state-data` volume. A deployment
that starts embedding under step 1 also rebuilds its keyword index once in
the background, because the recorded embedding model changes.

**Security posture:** wider in one case, narrower in another. A deployment
in step 1 now sends its notes to the configured embedding provider, which
for OpenAI or Voyage means a third party. A server, read-only ones
included, no longer writes a state folder into the vault by default. The
image's locked `urllib3` and `pyjwt` move past newly published advisories
([#1687](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1687));
the server makes its own requests through `httpx` and does not verify
tokens with `pyjwt`.

## Links a write adds that do not resolve

A link to a note that does not exist is easy to write and only shows up
later, when someone runs `get_broken_links`. This release lets `write`,
`edit` and `append` report such links in the same response that wrote
them.

The request came from [@mikebronner](https://github.com/mikebronner), who
also wrote the change. Their
[issue](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1725)
puts it this way: "The writer only finds out later, if someone runs
`get_broken_links`", and "This matters most when an LLM writes the
note." From "one vault served by this server" it reports that "On
2026-09-30, 19 of the 29 cross-references written that day were broken at
write time." It asks
that "A vault that does not want this sees no change in behaviour."

Turn it on with `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`. It is
off by default, and then the three responses are exactly as in 5.0. When
it is on, each response carries `unresolved_links`. It lists the target,
as written, of each link the call added that `get_broken_links` would
report, once each and in document order. Links the note already held are
left out, so an old broken link is not repeated on every edit. `[]` means
every new link resolves. `null` means the check could not run: the
first index build is still going, or the refresh failed or timed out. The write
still succeeds and the server logs `unresolved_link_check_skipped`.
Each call waits for the index to take in the write before it answers
([#1727](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1727)).
Python callers pass `report_unresolved_links=True` to `write`, `edit` or
`append` on the writer facet. The setting is in the
[configuration reference](../reference/configuration.md) and the field in
the [writing tools reference](../reference/tools/writer.md).

## Search by meaning from the provider alone

In 5.0, setting an embedding provider did nothing unless
`MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` was also set: "the server runs
keyword-only search and says nothing"
([#1708](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1708)).
Now an explicit `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` is enough. The
vectors go to `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` when it is set, beside
an on-disk index otherwise (`index-db-embeddings.npy` and `.json` beside
`index.db`), and into memory when there is no index path. A provider that
fails to load without a path set logs `embedding_provider_load_failed` and
leaves the server on keyword search. Detecting the provider from the
environment still needs `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`, so an
install that merely has a backend available does not start embedding
([#1731](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1731)).
The [embeddings guide](../use/embeddings.md) covers the providers.

The `search` command line, run without `MARKDOWN_VAULT_MCP_INDEX_PATH`,
printed an empty result "for a word that is in the vault"
([#1691](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1691)).
It now builds an in-memory index before it queries, and only reads an
on-disk one. With no index path, a `--mode semantic` or `hybrid` query
embeds the whole vault for that one query
([#1726](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1726),
[command line reference](../reference/cli.md)).

## Nothing written into the vault by default

The change-tracking file records each note's content hash, so a restart
re-reads only the notes that changed. In 5.0 it defaulted to
`.markdown_vault_mcp/state.json` inside the vault, in every mode: "With
`MARKDOWN_VAULT_MCP_READ_ONLY=true` the server still writes into the
vault"
([#1693](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1693)).
It now defaults beside the index as `index.db.state.json`, or into memory
when there is no index path, where it loses nothing because an in-memory
index is rebuilt at every start anyway. The file format is unchanged, and
with an index path the old file is copied over on the first start
(Upgrading step 2;
[#1721](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1721)).
[What an upgrade of this server touches](../upgrade/index.md) describes
where each kind of state lives.

## Conventions reach the model whole

A vault's conventions file tells the model where notes go and how to link
them. In 5.0, `get_conventions` cut each file at 4,000 characters, about
60 lines, and so did the `conventions` key in `write`, `edit`, `fetch` and
`get_context` results
([#1722](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1722)).
The limit is now 32,768 characters per file. A longer file is cut there
with a note naming the file to read for the rest, and the server logs
`conventions_truncated` at WARNING once per file. The limit is fixed, not
a setting
([#1723](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1723)).

The shipped prompts now read those conventions. In 5.0, "Only two of the
seventeen shipped prompts call that tool"; the others named fixed folders
such as `0-Inbox` and `Research/`
([#1709](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1709)).
Every built-in prompt, every prompt in the example packs and
`create_from_template` now call `get_conventions` first. A prompt that
writes takes its folder from the conventions and asks you when they name
none, so `research` no longer defaults to `Research/`. The example
`zettelkasten` prompt edits notes and is now tagged `write`, so a
read-only server no longer lists it
([#1717](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1717),
[#1732](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1732)). The
[prompts reference](../reference/prompts.md) lists them.

## The documentation

The site is now organised by what a reader is trying to do: Get started,
Deploy, Use, Reference and Upgrade, with a security model page at the top
and the maintainer pages under Contribute. The epic asked that "Each
question has one page that answers it"
([#1665](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1665)).
Every page that moved redirects from its old address. The tools, prompts,
resources, command line and configuration references are generated from
the code, and a check in CI fails when they fall behind it. `llms.txt` takes
its sections from the site's navigation, so a page no longer goes missing
from it ([#1668](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1668),
[#1690](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1690)). The
[Does it fit?](https://github.com/pvliesdonk/markdown-vault-mcp#does-it-fit)
block in the README states what the server assumes, one vault per server, Markdown only
and embeddings held in memory, and what to use when those do not hold
([#1378](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1378)).
[What an upgrade of this server touches](../upgrade/index.md) is new too.

These published instructions were wrong in 5.0 and are corrected:

- The two Claude Desktop setups labelled read-only did not set
  `MARKDOWN_VAULT_MCP_READ_ONLY`, so they ran a server that could write,
  edit and delete notes
  ([#1660](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1660)).
- The Python examples failed as written: the write example raised
  `ReadOnlyError`, and the quick start searched before building the index
  and got `[]`
  ([#1661](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1661)).
- The `read` reference left out `etag`, which the write tools take as
  `if_match`, and `edit` promised line numbers that `read` never shows
  ([#1662](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1662)).
- `get_index_status` was documented without most of the fields other
  pages told readers to watch, `embeddings_status` reported `available:
  true` before the build finished, and nothing said what a finished build
  looks like
  ([#1663](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1663)).
- The 5.0 upgrade notes put server state on the wrong volume and left
  `MARKDOWN_VAULT_MCP_SOURCE_DIR` and client action unresolved; the
  [5.0 page](5.0.md) now carries the corrected section
  ([#1664](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1664)).
- Install pages disagreed on which package runs the server, and the
  unquoted `[all]` extra failed in zsh
  ([#1667](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1667)).
- Guides linked to `examples/` and design pages the site does not serve
  ([#1669](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1669)).

## Smaller fixes

- An upload to a note link whose body is not valid UTF-8 answered 500. It
  now answers 415, and a body the vault refuses for another reason answers
  422, with the link still usable for a corrected upload
  ([#1718](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1718),
  [transfer links](../deploy/transfer-links.md)).
- Where OKF is on, the `create_download_link` description now names the
  `okf-bundle` and `okf-bundle:<folder>` refs, so a model can find the
  bundle download
  ([#1720](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1720)).
- The help text for `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and
  `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` said the claim overrides the
  committer. It sets the commit author; the committer stays
  `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME`. Only the text changed
  ([#1716](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1716)).
- `get_server_info` gains a `protocol` block with the MCP versions the
  server supports, the protocol version in use and the client's name and
  version, and each request's start log line names the protocol version
  and client too, from fastmcp-pvl-core 10.2.0
  ([pvl-core#420](https://github.com/pvliesdonk/fastmcp-pvl-core/pull/420)).
