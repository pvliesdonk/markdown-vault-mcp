# Research Workflows Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `docs/guides/research-workflows.md` guide (~500 lines) that codifies the LLM-augmented research loop in a markdown vault, with scholar-mcp as an optional power-up. Wire it into navigation, the Guides index, and cross-links from README / `docs/index.md` / Zettelkasten / PARA guides.

**Architecture:** Pure documentation PR. Five files created or modified; no `src/`, `tests/`, or `examples/` changes. The guide is organising-scheme-agnostic (works with Zettelkasten, PARA, or neither) and scholar-mcp-optional (every baseline flow works without it).

**Tech Stack:** Markdown, MkDocs Material (existing stack); no new dependencies.

**Reference spec:** [`docs/superpowers/specs/2026-04-19-research-workflows-guide-design.md`](../specs/2026-04-19-research-workflows-guide-design.md)

---

## Task 1: Write `docs/guides/research-workflows.md`

**Files:**
- Create: `docs/guides/research-workflows.md`

**Before you start:** read these reference files to orient on style, tone, and cross-link targets.

- `docs/guides/zettelkasten.md` — closest structural match. Study its heading hierarchy, admonition use, mix of prose and code blocks, and Next Steps section.
- `docs/guides/para.md` — similar shape with baseline-vs-prompt framing; good reference for the "baseline flow + with X callout" pattern.
- `docs/superpowers/specs/2026-04-19-research-workflows-guide-design.md` — the authoritative design; everything in this task conforms to it.
- `src/markdown_vault_mcp/static/prompts/propose-links.md` — the prompt this guide references by name.
- `examples/zettelkasten/templates/literature.md` — the existing literature note template the guide recommends.

### Content anchors — the guide has six sections and must include the following

**Section 1 — Intro (~30 lines)**

- Title: `# Research workflows with markdown-vault-mcp`
- Opening paragraph: research in a vault with Claude as a persistent sparring partner; the vault is the durable workspace across sessions, Claude is the reasoning agent.
- Second paragraph: the five phases as a non-linear loop (seed → ground → absorb → interconnect → produce), not a pipeline. Most sessions touch two or three phases.
- Third paragraph: scholar-mcp framing — "This guide's baseline works with just Claude, markdown-vault-mcp, and web search. [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) is a separate MCP server that adds rigour (citation graphs, BibTeX export, PDF-to-Markdown conversion) — optional but recommended when you need real paper IDs and don't trust the model's memory for citations."
- Admonition:
  ```markdown
  !!! note
      This guide is organising-scheme-agnostic. It works with [Zettelkasten](zettelkasten.md), [PARA](para.md), or no opinionated structure at all. The phase model in this guide composes the vault's generic search, linking, and write tools; it doesn't assume a particular folder layout.
  ```

**Section 2 — Setup (~40 lines)**

- Prerequisite pointer: "Install and configure markdown-vault-mcp per the [Claude Desktop guide](claude-desktop.md) or [Obsidian Everywhere](obsidian-everywhere.md) for the full multi-device setup. This guide assumes your vault is already connected to Claude."
- Subsection `### Adding scholar-mcp (optional)`:
  - One paragraph: "Skip this section if you want to start with the baseline. You can add scholar-mcp later without changing any of the workflows in this guide."
  - Claude.ai: one sentence pointer to "connectors" add flow, linking to the [scholar-mcp repo](https://github.com/pvliesdonk/scholar-mcp).
  - Claude Desktop: show a JSON snippet adding scholar-mcp as a second entry in `mcpServers`:
    ```json
    {
      "mcpServers": {
        "vault": {
          "command": "uvx",
          "args": ["--from", "markdown-vault-mcp", "markdown-vault-mcp", "serve"]
        },
        "scholar": {
          "command": "uvx",
          "args": ["--from", "pvliesdonk-scholar-mcp", "scholar-mcp", "serve"],
          "env": {
            "SCHOLAR_MCP_S2_API_KEY": "your-key-optional"
          }
        }
      }
    }
    ```
  - Claude Code: one sentence pointer to MCP settings + same structural pattern.
  - Note: "scholar-mcp's [own documentation](https://pvliesdonk.github.io/scholar-mcp/) covers API keys, OIDC auth, transport options, and tool details. This guide only references the tools by name."
- Web-search caveat: "The baseline flows assume Claude has web-search tools available. Claude.ai and Claude Desktop do by default; other clients vary. Without web search, fall back to scholar-mcp for the grounding phase or skip straight to manual source collection."

**Section 3 — The five phases (~300 lines, ~60 lines each)**

Each phase is a `## Phase N: <Name>` section with this internal structure:

```markdown
## Phase N: <Name>

**Intent.** <one sentence>

### Baseline flow

<One paragraph describing the flow. Include a concrete example of a prose ask to Claude and which markdown-vault-mcp tools the model composes. Show one short code block illustrating the end state (e.g., a sample note body with the claim-tracking markers, or a wikilink being added).>

### With scholar-mcp

<One-two paragraphs describing what changes when scholar-mcp is available. Name specific scholar-mcp tools. Describe what's materially better — not just "faster" but what correctness/rigour gain you get.>

### Common failure

<One paragraph naming the thing that typically goes wrong in this phase and how to catch it. Not a list of edge cases — one crisp failure mode per phase.>
```

Phase-by-phase content:

**Phase 1: Seeding notes**
- Intent: get a new note into the vault quickly without worrying about shape yet.
- Baseline: pointer to existing patterns — `para-capture-chats` prompt for conversation capture (Claude.ai only), the `research` builtin prompt for a single-note synthesis of a topic, or just prose: "Create a note at `research/<slug>.md` with this thesis: <paragraph>. Tag it `draft`." Show example frontmatter + body.
- With scholar-mcp: not meaningfully different here; scholar-mcp doesn't participate in seed capture. One line noting this.
- Common failure: waiting to polish before committing a note; the seed should land rough and be revised over multiple sessions.

**Phase 2: Literature grounding**
- Intent: back a claim in a note with real sources; prevent fabricated citations.
- Baseline: Claude + web search. Example ask: "For each claim in `research/consent-dynamics.md` marked `[citation needed]`, search the web for supporting or refuting papers and update the marker with a linked source." Introduce the claim-tracking convention here:
  - Inline markers: `[citation needed]` in prose. Searchable via `grep`. Familiar from other research contexts.
  - Note-level aggregation: a `## Open claims` or `## Evidence gaps` section where outstanding items accumulate.
  - The convention is a recommendation, not a requirement. Pick markers that you'll remember.
- With scholar-mcp: use `search_papers` to get real paper IDs and metadata (no hallucinated DOIs), `get_references` to walk what a seminal paper cites (backward), `get_citations` to find who cites it (forward). Example: "Ground the claim in line 47 using scholar-mcp: search for papers on <topic>, then for the top 3 results fetch their references to find foundational work." Mention BibTeX generation via `get_paper` — writes a Markdown citation block the model can attach to the literature note's frontmatter.
- Common failure: trusting the first plausible-sounding paper without verifying. Fabricated citations look like real ones. Always verify the DOI resolves, the authors exist, the title matches.

**Phase 3: Absorbing sources**
- Intent: turn a retrieved source into a durable literature note; decide when a summary suffices vs when the full text is needed.
- Baseline: Claude reads the abstract (and any web snippets it can get), writes a literature note. Show the shape (referring readers to `examples/zettelkasten/templates/literature.md` for a starting frontmatter):
  ```markdown
  ---
  title: "<paper title>"
  type: literature
  tags: [literature]
  source: "<DOI or URL>"
  created: 2026-04-19
  ---

  # <paper title>

  **Authors:** <names> · <year>
  **Source:** <DOI>

  ## Key Ideas

  <two-three bullet points>

  ## Relevance

  <one paragraph on how this connects to the vault's current research>

  ## Open Questions

  <if any>
  ```
- With scholar-mcp: `convert_pdf_to_markdown` pulls the full paper into the vault as a Markdown companion (for open-access papers); you write the literature note against the full text rather than the abstract. `get_paper` gives structured metadata (authors, venue, year) for the frontmatter without guessing.
- Common failure: reflexively fetching the full PDF. Most of the time the abstract + the author's argument is enough to decide relevance; saving the PDF conversion for when you actually need to dive deep keeps the vault from drowning in attachments.

**Phase 4: Interconnection**
- Intent: surface existing vault notes semantically close to the one you're working on; find work you'd forgotten.
- Baseline: ask Claude to run `get_similar(path=<current note>)`, inspect the candidates, and pick the meaningful ones. For a vault-wide sweep, invoke [`propose-links`](../prompts.md#propose-links) from the `+` menu. Example: "I'm working on `research/incentive-misalignment.md`. Run `get_similar` and tell me which of the top 5 candidates should be linked."
- With scholar-mcp: the scholar-mcp-specific angle here is cross-referencing papers. `get_paper(doi=<...>)` followed by `get_references` / `get_citations` sometimes reveals that two apparently unrelated strands in the vault share a seminal paper. Worth describing as "your literature graph is richer than your note graph; scholar-mcp exposes it."
- Common failure: missing an existing note because you'd forgotten what you called it. `get_similar` and `propose-links` catch these; left to memory alone, you end up rewriting notes that already exist.

**Phase 5: Writing a paper from notes**
- Intent: draft a paper from the vault, fact-check as you go, and let the paper and the source notes co-evolve.
- Baseline: start a `Draft/<paper-slug>.md` note, pull paragraphs from existing research notes (Claude can compose them), ask Claude to flag unsourced claims against `[citation needed]` markers, run the grounding loop from Phase 2, push citations back into both the paper draft and the originating source notes. Tangents encountered during writing that don't fit this paper's scope — don't discard, triage them into the vault (PARA Inbox, Zettelkasten Fleeting, or just a standalone note).
- With scholar-mcp: generate the references file in the author's preferred format (BibTeX via `get_paper`, or CSL-JSON for tools like Pandoc). "For every literature note linked from this draft, generate a BibTeX entry and append to `Draft/<paper-slug>.bib`."
- Common failure: letting tangents dilute the paper. The fix is to capture them separately at the moment they appear, not "I'll come back to that later" — that later rarely comes.

**Section 4 — Pitfalls (~60 lines)**

- Heading: `## Pitfalls`
- Opening: "Research with Claude is fast. Fluency is not evidence of correctness. Most of what follows is how to catch the model before it convinces both of you that an incorrect answer is settled."
- Subsection `### False confidence`:
  - One paragraph: Claude writes confidently, even when wrong. The confidence is in the prose, not the content.
  - Real example (one paragraph): "A session produced a confident assertion backed by a plausible-sounding paper. The title read like something that should exist, the DOI was formatted correctly, the authors were prominent in the field. The paper did not exist. Only a targeted follow-up — search for the paper, verify authors, check the DOI — caught it."
  - What to do: treat every citation as unverified until you've resolved the DOI or found the paper via scholar-mcp / web search.
- Subsection `### Make uncertainty explicit`:
  - One paragraph: AI-written text looks like a finished deliverable even when it's a first draft. Colleagues who read the note assume it's been verified. Future Claude sessions read the note back and treat confident prose as settled fact.
  - The fix: mark uncertainty in the note, not just in your head. `[citation needed]`, `[verify]`, `[my guess]` — any convention you'll actually use.
- Subsection `### Verify before polishing`:
  - Short paragraph: if a note reads like a deliverable but its claims aren't verified, it's premature polish. The verification loop is the workflow, not a step to skip when you're in flow.

No moralising. Three short subsections, each landing a specific lesson, grounded in the fabricated-citation example.

**Section 5 — A worked example (~40 lines)**

- Heading: `## A worked example`
- Opening: "Here's how the five phases thread together in a real session." One paragraph framing.
- The narrative (3-4 paragraphs): a generic scenario — do NOT use security-economics or any specific domain. Pick something like "understanding how software dependencies get abandoned" or "what makes a refactor feel risky" — open enough to apply to any reader, concrete enough to be useful. The narrative should:
  - Seed: a note captures the thesis ("abandoned dependencies usually show warning signs 6-12 months before the final commit").
  - Grounding: the claim about 6-12 months is flagged as `[citation needed]`. The user asks Claude to find literature.
  - Absorbing: Claude + web search turns up three relevant papers. Two get short summary notes; one (the central methodological paper) gets a full literature note.
  - Interconnection: `get_similar` surfaces a forgotten note from six months ago on maintainer burnout — highly related, worth cross-linking.
  - Producing: the user writes a short blog draft pulling from all the notes; one tangent (how package managers signal abandonment) becomes a new standalone note instead of diluting the blog.
- Keep it tight. No code blocks. Prose only. The reader should finish it thinking "I can do this."

**Section 6 — Next steps (~20 lines)**

- Heading: `## Next steps`
- Bullet list, four to six items:
  ```markdown
  - [Zettelkasten](zettelkasten.md) — idea-centric organisation; pairs well with literature notes and atomic permanent notes.
  - [PARA](para.md) — action-oriented organisation; research fits under Resources (reference material) and Projects (active research outputs).
  - [Obsidian Everywhere](obsidian-everywhere.md) — multi-device setup for phone-first capture between sessions.
  - [MCP Prompts reference](../prompts.md) — especially the [ambient patterns](../prompts.md#ambient-patterns-without-prompts) and [`propose-links`](../prompts.md#propose-links).
  - [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) — when citation rigour matters: citation graphs, BibTeX, full-text PDF conversion.
  ```

### How to build

- [ ] **Step 1: Read reference files**

Open and skim:
- `docs/guides/zettelkasten.md` (for structural pattern)
- `docs/guides/para.md` (for "baseline + variant" framing pattern)
- `docs/superpowers/specs/2026-04-19-research-workflows-guide-design.md` (authoritative design)
- `examples/zettelkasten/templates/literature.md` (the literature template this guide references)

- [ ] **Step 2: Draft the guide**

Create `docs/guides/research-workflows.md`. Follow the six-section structure above. Target: ~450-550 lines. Match the tone of the Zettelkasten guide: direct, second-person, short paragraphs, no marketing language.

For the worked example in Section 5, use the "abandoned software dependencies" scenario described above. Do NOT use security-economics or any domain that could identify the user's actual research.

Content accuracy requirements (verify against the actual tool signatures before committing):
- `markdown-vault-mcp` tools referenced: `get_similar`, `get_context`, `get_outlinks`, `get_backlinks`, `read`, `write`, `edit`, `list_documents`, `search`. No trailing slashes on folder arguments.
- scholar-mcp tools referenced: `search_papers`, `get_paper`, `get_references`, `get_citations`, `convert_pdf_to_markdown`. (These are names from the scholar-mcp README at `github.com/pvliesdonk/scholar-mcp`; verify at implementation time by fetching the latest README if signatures might have changed.)
- Reference `examples/zettelkasten/templates/literature.md` for the literature note shape, but don't copy its content verbatim — adapt it to the research-specific shape shown in Phase 3.
- Use `[[wikilinks]]` for internal vault references in examples.
- Use relative paths for docs cross-links (`zettelkasten.md`, `para.md`, `obsidian-everywhere.md`, `../prompts.md`). Do not use absolute GitHub Pages URLs inside the guide.

- [ ] **Step 3: Verify MkDocs builds the page**

Run:
```bash
uv run mkdocs build --strict 2>&1 | tail -20
```

Expected: build completes. Because the guide isn't in nav yet (Task 2 wires it), you'll see one `INFO` line like `The following pages exist in the docs directory, but are not included in the "nav" configuration: - guides/research-workflows.md`. That's expected at this task boundary.

If `--strict` fails on any other warning (broken internal link, missing anchor, malformed table), fix it in the guide before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/guides/research-workflows.md
git commit -m "docs: add research workflows guide"
```

---

## Task 2: Wire the guide into navigation

**Files:**
- Modify: `mkdocs.yml` (two nav blocks)
- Modify: `docs/guides/index.md` (new table row)

- [ ] **Step 1: Read `mkdocs.yml` to orient**

```bash
grep -n "guides/" mkdocs.yml | head -30
```

You'll see both the llmstxt `Guides:` block (around line 60-72) and the visible `nav: - Guides:` block (around line 125-140). Both need the new entry in the same relative position.

- [ ] **Step 2: Add research-workflows to the llmstxt Guides section in `mkdocs.yml`**

Find the llmstxt block. After the `guides/obsidian-everywhere.md:` line and before the `guides/zettelkasten.md:` line, insert:

```yaml
          - guides/research-workflows.md: Research workflows (seed → ground → absorb → interconnect → produce, with optional scholar-mcp integration)
```

The surrounding context should become:

```yaml
          - guides/obsidian-everywhere.md: Reference architecture for desktop + mobile + Claude on one vault
          - guides/research-workflows.md: Research workflows (seed → ground → absorb → interconnect → produce, with optional scholar-mcp integration)
          - guides/zettelkasten.md: Zettelkasten workflow (fleeting → literature → permanent notes, MOCs, graph navigation)
```

- [ ] **Step 3: Add research-workflows to the visible `nav:` Guides block in `mkdocs.yml`**

Find the visible nav block. After `- Obsidian Everywhere: guides/obsidian-everywhere.md` and before `- Zettelkasten: guides/zettelkasten.md`, insert:

```yaml
      - Research Workflows: guides/research-workflows.md
```

Updated context:

```yaml
      - Obsidian Everywhere: guides/obsidian-everywhere.md
      - Research Workflows: guides/research-workflows.md
      - Zettelkasten: guides/zettelkasten.md
```

- [ ] **Step 4: Add research-workflows row to `docs/guides/index.md` table**

In `docs/guides/index.md`, find the "Which guide do I need?" table. Insert a new row after the Obsidian Everywhere row and before the Zettelkasten / PARA rows:

```markdown
| Do research (literature grounding, interconnected notes, paper drafting) | [Research workflows](research-workflows.md) |
```

The surrounding context:

```markdown
| Access my vault from desktop, mobile, AND Claude | [Obsidian Everywhere](obsidian-everywhere.md) |
| Do research (literature grounding, interconnected notes, paper drafting) | [Research workflows](research-workflows.md) |
| Use FastEmbed for local embeddings | [Embeddings](embeddings.md#fastembed) |
```

- [ ] **Step 5: Verify MkDocs `--strict` is clean**

```bash
uv run mkdocs build --strict 2>&1 | tail -15
```

Expected: the "pages exist in docs directory but not in nav" notice for `research-workflows.md` is now GONE (Steps 2-3 wired the nav). All other INFO notices (`design.md` excluded, `examples/` relative links in zettelkasten/para, `oidc-providers.md` anchor) remain as pre-existing. No new warnings.

- [ ] **Step 6: Commit**

```bash
git add mkdocs.yml docs/guides/index.md
git commit -m "docs: wire research-workflows guide into nav and Guides index"
```

---

## Task 3: Update the "What you can do with it" sections in README and docs/index.md

**Files:**
- Modify: `README.md` (one bullet in the existing section)
- Modify: `docs/index.md` (one bullet in the existing section)

The existing "Research a topic into your vault" bullets in both files should link to the new guide. This is a one-line edit per file.

- [ ] **Step 1: Update the Research bullet in `README.md`**

Find the existing bullet (around line 32). It currently reads:

```markdown
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes — one per regulation, plus a map-of-content." — Claude composes web-search tools (client-side) + `write` with wikilinks.
```

Change to:

```markdown
- **Research a topic into your vault.** "Research product security regulations, compare them, and create a set of interlinked notes — one per regulation, plus a map-of-content." — Claude composes web-search tools (client-side) + `write` with wikilinks. See the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/research-workflows/) for the full loop.
```

Note: README uses absolute `https://pvliesdonk.github.io/...` URLs consistently (the README renders on GitHub and PyPI where relative doc links would break). Stay consistent with that convention.

- [ ] **Step 2: Update the Research bullet in `docs/index.md`**

Find the parallel bullet (around line 28). It currently reads:

```markdown
- **"Research <topic> and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks.
```

Change to:

```markdown
- **"Research <topic> and create a set of interlinked notes."** Claude composes web tools + `write` with wikilinks. See the [Research workflows guide](guides/research-workflows.md) for the full loop.
```

Note: `docs/index.md` uses relative paths (renders inside the docs tree). Stay consistent.

- [ ] **Step 3: Verify MkDocs build is clean**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no new warnings. The relative link in `docs/index.md` resolves to the new guide.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/index.md
git commit -m "docs: link 'Research a topic' bullet to the new research-workflows guide"
```

---

## Task 4: Add Next Steps cross-links from Zettelkasten and PARA guides

**Files:**
- Modify: `docs/guides/zettelkasten.md`
- Modify: `docs/guides/para.md`

- [ ] **Step 1: Add a Next Steps bullet to `docs/guides/zettelkasten.md`**

Find the Next Steps section at the end. It currently has a few bullets including "Ambient patterns" (added in PR #388). Append this new bullet as the last item:

```markdown
- **Research workflows**: [research-workflows.md](research-workflows.md) — literature grounding, fact-checking, and writing papers from notes
```

- [ ] **Step 2: Add a Next Steps bullet to `docs/guides/para.md`**

Find the Next Steps section at the end of `docs/guides/para.md`. Append this bullet as the last item:

```markdown
- **Research workflows**: [research-workflows.md](research-workflows.md) — literature grounding, fact-checking, and writing papers from notes
```

- [ ] **Step 3: Verify MkDocs build is clean**

```bash
uv run mkdocs build --strict 2>&1 | tail -10
```

Expected: no new warnings. The relative link `research-workflows.md` resolves.

- [ ] **Step 4: Commit**

```bash
git add docs/guides/zettelkasten.md docs/guides/para.md
git commit -m "docs: cross-link Zettelkasten and PARA guides to research-workflows"
```

---

## Task 5: End-to-end verification

**Files:** (none modified directly)

- [ ] **Step 1: Run full pre-commit**

```bash
uv run pre-commit run --all-files
```

Expected: all hooks pass (ruff, ruff format, mypy, vendored SPA, trailing whitespace, EOF, YAML, large files, JSON). Most will skip since no Python changed; the markdown/YAML/whitespace hooks will run and pass.

- [ ] **Step 2: Run the full test suite as a regression check**

```bash
uv run pytest -x -q
```

Expected: all tests pass. No code changed, but run the suite to confirm nothing in the docs build broke a test (e.g., a docstring-link test if any).

- [ ] **Step 3: Run MkDocs `--strict`**

```bash
uv run mkdocs build --strict 2>&1 | tail -20
```

Expected: clean build. The only remaining INFO notices should be the pre-existing ones (`design.md` excluded, `examples/` relative links in zettelkasten and para guides, `oidc-providers.md` anchor).

Sanity-check the generated output:

```bash
ls site/guides/research-workflows/ && ls site/guides/index.html > /dev/null && echo 'guides index ok'
```

Expected: `site/guides/research-workflows/index.html` exists; the guides index builds.

- [ ] **Step 4: Link-resolve audit**

Inside the new guide, verify every internal link resolves by running:

```bash
for link in $(grep -oE '\]\(\.\./[^)]+\)' docs/guides/research-workflows.md); do
  target=$(echo "$link" | sed -E 's/^\]\(//; s/\)$//')
  if [[ "$target" == *#* ]]; then
    file="${target%%#*}"
  else
    file="$target"
  fi
  resolved="docs/${file#../}"
  if [[ -f "$resolved" ]]; then
    echo "OK:  $link"
  else
    echo "BROKEN: $link (resolved to $resolved)"
  fi
done
```

Expected: all links print `OK`. Any `BROKEN` entry must be fixed.

- [ ] **Step 5: Review the branch commit history**

```bash
git log --oneline main..HEAD
```

Expected: four commits from Tasks 1-4 (verification task has no commit).

- [ ] **Step 6: No commit for this task**

If any step failed, diagnose, fix in the relevant file, amend the appropriate task's commit or create a fix-up commit, and re-run the verification sequence from Step 1.

---

## Out of Scope (Future Work — do not implement now)

- A new example pack under `examples/research/`. The guide references the existing Zettelkasten `literature.md` template; a research-specific template pack is only justified if real usage shows demand.
- Vault-to-paper authoring tutorials (LaTeX/Markdown-to-PDF pipelines, reference manager sync). Out of scope; would be a separate guide.
- Server-side integration between markdown-vault-mcp and scholar-mcp (e.g., a tool that auto-creates a literature note from a scholar-mcp paper lookup). Not a design concern for this PR.
- Claim-tracking automation (e.g., a prompt that scans the vault for `[citation needed]` markers and queues them for verification). If the convention catches on, consider a `verify-claims` prompt as a follow-up.
- A follow-up PR to link back from the blog to the guide once (if) the blog is published.
