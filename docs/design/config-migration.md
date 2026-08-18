# Config migration

This guide is for a project that ran `copier update` from a template version
older than the config-surface generator. It covers moving from **hand-written**
`.env.example`, `packaging/env.example`, `examples/*.env`, and
`docs/javascripts/config-wizard/wizard-spec.json` to **generated** ones.

If your project was created after the generator landed, there is nothing to
migrate. Skip this guide.

## Why this matters

Three of those files used to be domain-owned (`_skip_if_exists` in
`copier.yml`): `.env.example`, `packaging/env.example`, and
`docs/javascripts/config-wizard/wizard-spec.json`. You edited them directly,
and `copier update` left your edits alone. `examples/*.env` was never
domain-owned; it was already re-rendered on every update, only its source
is changing now.

All four files are now `generated`, produced by
`scripts/gen_config_surface.py` from
`fastmcp_pvl_core.server_config_surface()` plus `config-presentation.yml`,
and re-run on every `copier copy` (as a post-copy task) and at the end of
every `copier update` (as an after-stage migration, once the update has
restored your domain `config.py` — so the regenerated artifacts always
reflect your real domain fields). The first `copier update` that pulls
this change **overwrites whatever you hand-wrote in those four files**
with the generator's own output.

If you customised the help text or example value of a **core** var (as
opposed to adding a domain var), there is no preservation path at all:
`config-presentation.yml` is template-owned and gets overwritten the same
way, and step 2 below covers only domain fields. Re-apply that customisation
upstream in `fastmcp-pvl-core` or `config-presentation.yml` in the template
repo, not in the generated file.

## Steps

### 1. Copy out your hand-written help text before regenerating

Before running `copier update` (or immediately after, using `git diff` against
the pre-update commit), save the prose you wrote in `.env.example`,
`packaging/env.example`, and `wizard-spec.json` for every domain-specific
variable (the ones your project added beyond what the template shipped). That
text is about to be replaced and is the only copy of it.

### 2. Move each domain env var into `ProjectConfig`'s `CONFIG-FIELDS` block

For every domain env var you found in step 1, add or update its field in
`src/markdown_vault_mcp/config.py` between the `CONFIG-FIELDS-START` /
`CONFIG-FIELDS-END` sentinels, carrying the help text you copied out as
`metadata`:

```python
    vault_path: Path = field(
        default=Path("/data/vault"),
        metadata={"help": "Filesystem root of the vault.", "tags": ("storage",)},
    )
```

A composed sub-config section works as well: a `ProjectConfig` field whose
type is a dataclass with its own `from_env` (core's `TransferConfig`, or a
project-local section) contributes its vars with each *section* field's own
`metadata` — no need to flatten the section into top-level fields. This
needs `fastmcp-pvl-core` 4.6.0 or later, and each section read must stay
inline in its constructor keyword (`field=env(prefix, "LITERAL")`) for the
scan to attach the field's metadata.

**This step needs a human or a capable agent, not mechanical substitution.**
Deciding which sentence in a paragraph holds the actual help text, as
opposed to surrounding narrative that stays in prose docs, or a caveat that
belongs elsewhere, is a judgment call. A field's `metadata["help"]` becomes
the exact text shown in `.env.example` and the config wizard, so picking the
wrong sentence ships the wrong documentation to every future reader of the
generated files.

Also update `from_env` (between `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END`)
to read the var if it doesn't already, and `__post_init__` (between
`CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END`) for any invariant the old
prose described.

A **top-level** field carries its metadata whether you read it inline in the
constructor keyword (`cls(read_only=env(_ENV_PREFIX, "READ_ONLY"), ...)`) or
read it into a local first and pass it by name
(`read_only = parse(env(...))` then `cls(read_only=read_only)`): core resolves
a local-read var to the field whose name matches its suffix. A **composed
sub-config section** field does not get that fallback — its suffix carries the
section's prefix, which core cannot unambiguously strip — so a section field
must be read inline in its own constructor keyword
(`field=env(prefix, "LITERAL")`, with `env_int` / `env_float` for parsed
numbers) for its help, default, required-ness, and secret masking to reach the
generated artifacts. A value that genuinely cannot be built inline (a
deprecation remap of a legacy var name) belongs in `__post_init__`, off the
scanned path.

### 3. Declare only what the AST scan cannot see

The generator discovers domain vars by AST-scanning `ProjectConfig.from_env`
for every literal `env(prefix, "SUFFIX")` read, whether or not that suffix
matches a dataclass field. See `scripts/gen_config_surface.py`'s
`_discover_domain_vars`. That covers every var your project reads inside
`from_env`, with or without a matching field. Add an entry to
`config-presentation.domain.yml` only for a var the scan cannot
see:

- A deprecated alias that is no longer read inside `ProjectConfig.from_env`
  at all. An alias still read there is already covered by the scan;
  declaring it here raises the duplicate-name error described below.
- A var read directly via `os.environ` or a helper, outside
  `ProjectConfig.from_env` entirely.

Do not duplicate a var here that already has a `CONFIG-FIELDS` entry, or one
still read inside `from_env` without a field; either produces a
duplicate-name error from the generator.

Breaking the literal-read rule inside `from_env` — reading a var through a
project helper (`opt_int(prefix, "MAX_CHUNK_CHARS")`) or `os.environ`
instead of `env`/`env_int`/`env_float` — fails generation with an error
naming the call site, rather than silently dropping the var from every
generated artifact. Fix it by switching to a literal `env(...)` read (plus
a value-parsing helper if needed) or by declaring the var here. For a
flagged string literal that is not an env var at all, list it under
`scan_ignore:` in `config-presentation.domain.yml`.

The inverse direction raises the same duplicate-name error: a var the scan
*does* see that the template already declares. `config-presentation.yml`
declares `MARKDOWN_VAULT_MCP_SERVER_NAME` and `MARKDOWN_VAULT_MCP_INSTRUCTIONS`
with template provenance, so a literal `env(...)` read of either inside
`from_env` gets discovered as a domain var too, and the generator exits with
the duplicate-name error. If your project honors either var (an anticipated
pattern since the template started honoring them), keep the field but move
the read outside `from_env` — for example a module-level helper your server
assembly calls. The scan only walks `from_env`, so the read stays invisible
to it while runtime behavior is unchanged.

### 4. Run the generator and diff

```bash
python scripts/gen_config_surface.py
git diff
```

Confirm every domain var you moved in step 2 reappears in `.env.example`,
`packaging/env.example`, and (if it should be wizard-visible)
`docs/javascripts/config-wizard/wizard-spec.json`. A var that does not
reappear means its field metadata, tags, or `config-presentation.domain.yml`
entry is missing or misspelled. Fix that before continuing, not by
hand-editing the generated file.

### 5. Delete the now-duplicated hand-written tables

Once the diff in step 4 confirms every var survived the move, delete the
hand-written prose you copied out in step 1 from wherever it lived
(`README.md`, `docs/configuration.md`, or similar). It now duplicates the
generated artifact and would drift from it over time if kept.

## Verifying you're done

```bash
python scripts/gen_config_surface.py --check
```

Exits 0 when every generated artifact matches what the generator would
produce right now, the same check CI runs on every push.

## The OIDC tables became generated too

A later template version turned two more tables into generated regions:
the OIDC environment-variable tables in `docs/deployment/oidc.md` and
`docs/guides/authentication.md`. This is a separate hazard from the
four-file migration above and can hit a project that already finished
that migration, if it adopted the generator before this version and still
has hand-written OIDC tables in those two files.

The first `copier update` that pulls this version in 3-way-merges each
table. If you never touched either table, the merge lands cleanly: `ours`
matches `base`, so `theirs`, the marker pair with the generator's rows
between them, replaces it without a conflict. If you customised a table,
`ours` diverges from `base` and copier reports a conflict there: `base`
is the old hand-written rows, `ours` is your customisation, `theirs` is
the `GENERATED-ENV-TABLE-OIDC-REQUIRED` or `GENERATED-ENV-TABLE-OIDC-OPTIONAL`
marker pair with the generator's rows already filled in.

Resolve the conflict by keeping the marker pair, both the `-START` line
and the `-END` line, exactly as the update delivers them, for each
region. Only the marker pair has to survive; the rows between the
markers are the generator's output and get overwritten on every run, so
do not carry your customised rows forward inside them. Custom OIDC notes
that must survive belong outside the marker pair, before or after it in
the same file.

If a marker gets dropped or broken while resolving the conflict, or at
any later point, `python scripts/gen_config_surface.py --check` (the
check the generated project's CI runs) fails loudly instead of silently
producing the wrong table. It raises a `SystemExit` naming the file and
the region:

```text
ERROR: docs/deployment/oidc.md: region 'OIDC-REQUIRED' is missing its END marker ('<!-- GENERATED-ENV-TABLE-OIDC-REQUIRED-END -->').
```

Recovery: restore the missing marker line around the region so it reads
`<!-- GENERATED-ENV-TABLE-OIDC-REQUIRED-START — generated by
scripts/gen_config_surface.py; do not edit -->` above the table and
`<!-- GENERATED-ENV-TABLE-OIDC-REQUIRED-END -->` below it (swap in
`OIDC-OPTIONAL` for the second region), then rerun the generator.

OIDC env-var tables are rarely hand-customised, so most downstream
projects hit the clean merge above, not the conflict.

## The README domain table became generated too

A later template version turns `README.md`'s `## Domain configuration`
table generated as well. Before this version, that section held a
hand-written table of domain env vars: a project author added a row by
hand for each variable their `ProjectConfig` introduced beyond what the
template ships. Now the table is spliced between the
`GENERATED-ENV-TABLE-DOMAIN-START` and `GENERATED-ENV-TABLE-DOMAIN-END`
markers, and `scripts/gen_config_surface.py` produces every row in it
from each domain field's dataclass `metadata`, the same `help` and
`tags` that already drive `.env.example` and the config wizard.

The same 3-way-merge hazard described above for the OIDC tables applies
here. If you never hand-edited the domain table, the merge lands
cleanly. If you did, `copier update` reports a conflict, comparing your
customisation against a base of your project's previous hand-written
rows and a template side that already carries the marker pair with the
generator's rows filled in.

Resolve it the same way:

1. Move each hand-written row's content into the corresponding
   `ProjectConfig` field's `metadata`, between the `CONFIG-FIELDS-START`
   and `CONFIG-FIELDS-END` sentinels in `src/markdown_vault_mcp/config.py`:
   `help` supplies the description, and `tags` (the generator adds
   `domain` automatically) controls where the row is placed. A domain
   field declared with neither a `default` nor a `default_factory`
   renders `Required: Yes`; one that has a default, including an
   explicit `= None`, renders `Required: No`.
2. Keep the `GENERATED-ENV-TABLE-DOMAIN-START` / `-END` marker pair
   intact and let the generator own every row between them; do not
   hand-edit inside the markers.
3. When resolving the conflict, take the template's side for the
   marked region, then rerun the generator so the rows reflect your
   moved metadata.

The `config-presentation.domain.yml` escape hatch (for a var the AST scan
cannot see) expresses required-ness the same way a dataclass field does:
a manual entry with no `default:` key renders `Required: Yes`, and one
with an explicit `default: null` (or a real default) renders
`Required: No`.

Dropping or breaking either marker fails the same guard as the OIDC
tables: `scripts/gen_config_surface.py --check` (and a plain generation
run) raises a `SystemExit` naming the file and the region instead of
silently producing the wrong table:

```text
ERROR: README.md: region 'DOMAIN' is missing its START marker ('<!-- GENERATED-ENV-TABLE-DOMAIN-START — generated by scripts/gen_config_surface.py; do not edit -->').
```

Recovery follows the same pattern as the OIDC guard above: restore the
missing marker line around the region, then rerun the generator.

## `server.json`'s package env-var arrays became generated too

A later template version generates the two `environmentVariables` arrays
inside `server.json`'s `packages` entries: one for the `pypi` package, one
for the `oci` package. Before this version, an author added or edited
entries in those arrays by hand. Now each array is produced by
`scripts/gen_config_surface.py` from `ProjectConfig` field metadata plus
`config-presentation.yml`'s `packaging:` map, which says which of the two
packages a given var belongs in.

JSON has no comment syntax, so this file cannot carry a marker-pair
region the way the OIDC and domain tables above do. The generator splices
structurally instead: for each declared array it walks to the array's
parent object and replaces that one array wholesale, leaving every other
key in the document, including `version` and the `oci` package's
`identifier`, untouched. Those two keys are rewritten separately by
`scripts/stamp_manifests.py` on stable releases, so keeping the generator
off them is intentional, not an oversight.

**Merge hazard for the release-managed keys:** `copier update` can leave
conflict hunks in `server.json` whose template side carries the
fresh-scaffold values — `"version": "0.1.0"` and an `oci` identifier
ending `:v0.1.0`. Resolving toward the template side (natural, since the
env-var arrays get regenerated anyway) silently regresses those
release-managed keys, and no shipped gate catches it: the generator
leaves both keys alone by design, and `gen_config_surface.py --check`
passes either way. When resolving such a conflict, keep your side's
`version` and `oci` `identifier` (or restore them from the pre-update
commit), and diff `server.json` against your default branch before
committing. A regressed identifier ships a wrong manifest until the next
release re-bumps it.

**Overwrite hazard:** if you hand-edited entries inside either
`environmentVariables` array, the next `gen_config_surface.py --check`
(or a plain generation run) overwrites the whole array with the
generator's output, silently discarding your edits; a structural
whole-array replacement has no `ours`/`theirs` to reconcile the way a
3-way merge would. Move domain env-var customisation into the escape
hatch the README domain table migration above already describes: your
project's `ProjectConfig` field `metadata` (so the generator emits it) or
a `config-presentation.domain.yml` entry for a var the AST scan cannot
see.

**The `pypi` array only carries what a local install needs.** OIDC and
auth vars such as `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` and
`MARKDOWN_VAULT_MCP_BEARER_TOKEN` now appear only in the `oci` package's
array, the one a remote HTTP deployment reads. The `pypi` package's
array keeps only what a stdio install needs: the log-level and
rich-logging switches, together with `MARKDOWN_VAULT_MCP_SERVER_NAME`,
`MARKDOWN_VAULT_MCP_INSTRUCTIONS`, and `MARKDOWN_VAULT_MCP_KV_STORE_URL`.
If you diff your project's old `server.json` against the regenerated
one, expect the `pypi` array to shrink. That is the intended split, not
lost data; the vars that dropped out of the `pypi` array are still
present, unchanged, in the `oci` array.

Restructuring `server.json` itself, not just editing an array's
contents, raises a different guard. If a package is reordered or a new
one is inserted so that the `pypi` and `oci` entries no longer sit at
index 0 and 1, the generator's array-path resolver still finds two
arrays at those positions, but the wrong ones, and refuses to guess:

```text
ERROR: server.json: array path ['packages', 1, 'environmentVariables'] resolved to a package whose registryType is 'pypi', but the entry declares packaging 'oci'. The packages were probably reordered or one was inserted. Restore the declared order (pypi first, oci second) in this file; editing config-presentation.yml instead will not survive, since copier update overwrites it. Left unfixed, the two packages' environment sets are swapped.
```

Recovery: restore the declared package order in `server.json`, `pypi`
first and `oci` second, then rerun the generator.

A more severe restructuring, deleting a `packages` entry outright or
removing `server.json` itself, fails even earlier, before the
registry-type check runs:

```text
ERROR: /home/dev/my-project/server.json does not exist — a `kind: json-splice` artifact must already exist; this generator only replaces the declared array(s) inside it, never the surrounding file.
```

Recovery: restore `server.json` from git history (or `copier update`'s
pre-update commit) before rerunning the generator; this generator only
ever replaces the two declared arrays inside the file, never creates the
file itself.
