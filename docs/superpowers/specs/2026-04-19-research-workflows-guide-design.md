# Research Workflows Guide — Design

**Date:** 2026-04-19
**Status:** Approved design; plan pending
**Audience:** Contributors writing a new user-facing guide that describes LLM-augmented research workflows in a markdown vault

## Summary

Add `docs/guides/research-workflows.md`: a practical guide to using Claude + markdown-vault-mcp to do interlinked research in a vault, optionally augmented with [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) for rigorous literature handling.

The guide is **organising-scheme-agnostic** (works with Zettelkasten, PARA, or neither) and **scholar-mcp-optional** (baseline is Claude + vault + web search; scholar-mcp is a power-up for citation graphs, BibTeX, and full-text PDF conversion).

## Goals

1. Codify the phased research loop observed in practice — seed → ground → absorb → interconnect → produce — as a sequence of composable, prose-invocable workflows.
2. Capture the claim-tracking discipline and the false-confidence gate as load-bearing advice. Both come from real practitioner experience and prevent the main failure mode of LLM-assisted research (fluent-but-wrong output).
3. Position scholar-mcp as an enhancement, not a prerequisite. Each phase shows the baseline flow (Claude + vault + web search), then adds a "With scholar-mcp" callout describing what's gained.
4. Tie the guide into the existing docs graph — referenced from Zettelkasten and PARA Next Steps, indexed in the Guides table, linked to from the README's "what you can do with it" section.

## Non-Goals

- A scholar-mcp setup tutorial. Scholar-mcp's own docs cover installation. This guide gives only the one-line "install via Claude plugin" or "add as a connector on Claude.ai" pointer.
- A deep tutorial on vault-to-paper authoring (LaTeX integration, reference manager sync). That's a separate workflow; this guide stops at "you can write a paper from notes."
- Replicating the companion blog post. The blog won't be published; the guide absorbs the load-bearing material from the blog directly (the fabricated-citation story, the claim-tracking convention, the practitioner-vs-literature framing). The guide doesn't reference the blog.
- A new example pack under `examples/`. The guide references the existing Zettelkasten `literature.md` template rather than introducing a parallel research pack.
- Any server code changes. Guide-only PR.

## Design Decisions

### 1. Baseline + power-up framing

Each workflow phase is presented in two tiers:

- **Baseline** — Claude + markdown-vault-mcp + web search. Works for everyone. No extra install. Good enough for 80% of research work.
- **With scholar-mcp** — adds citation graph traversal, BibTeX/CSL-JSON/RIS export, full-text PDF → Markdown conversion, and real paper IDs (reduces hallucination risk). Scoped callouts; skippable.

Rationale: most readers won't set up scholar-mcp initially. Making it mandatory suppresses adoption. Baseline-first also matches the existing "prompts only when they add value" framing — the guide teaches a workflow that composes existing primitives, and scholar-mcp is documented as the case where codification (via a second MCP server) pays off.

### 2. Phase-oriented structure

Five phases, each a named workflow stage:

1. **Seeding notes** — research starts (from a conversation, a research question, a raw opinion). Mostly pointers to existing capture patterns.
2. **Literature grounding** — backing a claim with real sources. Where web search and scholar-mcp earn their keep. Introduces the claim-tracking convention.
3. **Absorbing sources** — creating literature notes from retrieved sources; when to summarise vs full-PDF. References the Zettelkasten literature template.
4. **Interconnection** — `get_similar`, `get_context`, `propose-links` surface tangentially-related existing work.
5. **Writing a paper from notes** — the co-evolution loop: draft from notes → find claims needing citations → update both the paper and the source notes. Tangents during writing become standalone notes.

Each phase has an "Intent" sentence, a "Baseline flow" with a concrete Claude ask, a "With scholar-mcp" callout, and a "Common failure" note.

### 3. Claim-tracking convention

The guide recommends a simple, plaintext convention without introducing new markup:

- **Inline markers** — `[citation needed]` in prose. Searchable with a literal `grep`. Familiar from other research contexts.
- **Note-level aggregation** — a `## Open claims` or `## Evidence gaps` section in notes where outstanding items accumulate. Gets cleared as literature fills in.

The convention is a recommendation, not a requirement. Users can adopt their own markers. The guide's value is naming the discipline, not prescribing syntax.

### 4. False-confidence gate

Dedicated "Pitfalls" section at the end, codifying the single most load-bearing piece of practitioner wisdom from the blog:

- Claude writes confidently — even when wrong. Fluency is not evidence of correctness.
- Real example: a session produced a plausible citation to a paper that didn't exist. The paragraph read well, the DOI was plausible, the reference was fabricated. Only a targeted follow-up (search for the paper, verify authors, check the DOI) caught it.
- Make uncertainty explicit in the notes, not just in your head. AI-written text looks like a finished deliverable even when it's a first draft; colleagues and *future Claude sessions* treat it as established fact.
- Verification is the workflow, not an afterthought. The claim-tracking loop isn't optional.

### 5. Integration with existing docs

- Guide is organising-scheme-agnostic. Cross-linked from both Zettelkasten and PARA Next Steps, not scoped to either.
- Referenced from the README's existing "What you can do with it" section — update the "Research a topic into your vault" bullet to link to this guide.
- MkDocs nav entry positioned after `obsidian-everywhere` (which covers the multi-device setup this guide assumes) and before `zettelkasten` / `para` (which this guide references).
- `docs/guides/index.md` gets a new table row.

## Guide Structure

Target ~500 lines. The file lives at `docs/guides/research-workflows.md`.

### Section 1 — Intro (~30 lines)

- One paragraph: research in a vault with Claude as a sparring partner; the vault is the persistent workspace across sessions, Claude is the reasoning agent.
- One paragraph: the five phases as a non-linear loop, not a pipeline. Most research sessions touch 2-3 of the phases.
- One admonition: "This guide is organising-scheme-agnostic. It works with Zettelkasten, PARA, or no opinionated structure at all."
- One paragraph framing scholar-mcp as optional.

### Section 2 — Setup (~40 lines)

- Install markdown-vault-mcp per existing guides (one-line pointer to installation docs; no duplication).
- Add scholar-mcp as a second MCP server when you want the power-ups:
  - Claude.ai: add as a connector (one sentence).
  - Claude Desktop: second entry in `mcpServers` (one code block).
  - Claude Code: second entry in MCP settings (one code block).
- Note: scholar-mcp has its own setup docs for API keys and options; this guide only covers what's needed to have both servers visible in your client.

### Section 3 — The five phases (~300 lines, ~60 lines each)

Each phase is a `##` subsection with the same internal structure:

- **Intent** — one sentence describing what this phase accomplishes.
- **Baseline flow** — a concrete example Claude ask, the tools the model composes, and what lands in the vault. Shows this working without scholar-mcp.
- **With scholar-mcp** — what changes when scholar-mcp is available. Reference specific scholar-mcp tools by name (`search_papers`, `get_references`, `convert_pdf_to_markdown`, `get_paper` for BibTeX).
- **Common failure** — the thing that typically goes wrong in this phase; how to catch it.

Concrete phase outlines:

**3.1 Seeding notes** — capture a note from a conversation (pointer to `para-capture-chats` and the ambient-patterns section of `docs/prompts.md`), from a research question (`research` builtin prompt or prose intent), or from a raw thesis/opinion (just write it; triage later). Load-bearing idea: "capture first, classify later." Common failure: waiting until a note is polished before committing it.

**3.2 Literature grounding** — back a claim in a note with sources. Baseline: `search` + web search (Claude runs web queries), write literature notes with `write`. Ask template: "For each claim in this note marked `[citation needed]`, search for supporting or refuting literature and update the claim with a source link." With scholar-mcp: `search_papers` returns real paper IDs and metadata (no fabrication), `get_references` walks the citation graph, `get_paper` generates BibTeX for a separate references file. Introduce the claim-tracking convention here.

**3.3 Absorbing sources** — once literature is identified, decide per source whether a summary suffices or you need the full text. Baseline: Claude summarises from abstract + web search; write a literature note using the existing Zettelkasten `literature.md` template shape (summary + relevance + source link). With scholar-mcp: `convert_pdf_to_markdown` pulls the full paper into the vault as an attachment + extracts a Markdown companion; citation generation preserves DOI and venue info in the literature note's frontmatter. Common failure: fetching the PDF reflexively when the abstract answers the question.

**3.4 Interconnection** — surface existing vault notes related to new research. Baseline: ask Claude to run `get_similar` on the note under development, and inspect the candidates. For a whole-vault sweep, invoke `propose-links`. Idea: the tangent you chased three months ago is probably already a note — find it. With scholar-mcp: cross-reference papers in your literature notes — `get_paper`'s reference/citation graph sometimes reveals that two things you've been researching separately share a canonical paper. Common failure: missing an existing note because you forgot what you called it; `get_similar` catches these.

**3.5 Writing a paper from notes** — the co-evolution loop. Baseline: start a `Draft/{paper-slug}.md` note, pull paragraphs from existing research notes, ask Claude to flag unsourced claims, run the grounding loop, push citations back into both the paper and the source notes. Tangents encountered while writing that don't fit this paper's scope → standalone notes, triaged normally (PARA Inbox / Zettelkasten Fleeting / etc.). With scholar-mcp: generate the references file in the author's preferred format (BibTeX, CSL-JSON, RIS) from the literature notes' DOIs. Common failure: letting tangents dilute the paper draft because capturing them separately feels like overhead.

### Section 4 — Pitfalls (~60 lines)

- False confidence is the real risk. One paragraph framing.
- Real example: the fabricated-citation story from the blog, condensed and generalised (don't expose the original domain). Something like: "A session produced a confident assertion backed by a plausible-sounding paper. The title read like something that should exist, the DOI was formatted correctly, the authors were prominent in the field. The paper did not exist. Only a targeted follow-up — search for the paper, verify authors, check the DOI — caught it."
- Make uncertainty explicit *in the notes*, not just in your head. Future Claude sessions read the note back and treat confident prose as settled fact.
- The `[citation needed]` marker is not a nice-to-have. It's how you keep the workflow honest.
- Verify before the note looks like a deliverable. Polish is premature if claims aren't checked.
- Don't let the guide sound preachy — one short section, emphasis on the lesson, not the finger-wagging.

### Section 5 — A worked example (~40 lines)

One generic scenario (not security-economics, not tied to a specific field) that threads all five phases in 3-4 paragraphs:

- Seed: a note capturing a half-formed opinion on some topic.
- Grounding: find supporting literature, mark unsourced claims.
- Absorbing: one detailed literature note for the central source, short summaries for two others.
- Interconnection: `get_similar` surfaces a forgotten note from six months ago that covers an adjacent angle.
- Producing: a short draft uses the material; one tangent becomes a new standalone note for later.

Written as narrative, not a step-by-step. Shows the loop in action. ~40 lines.

### Section 6 — Next steps (~20 lines)

Cross-links:

- [Zettelkasten guide](zettelkasten.md) — idea-centric organisation; pairs well with literature notes.
- [PARA guide](para.md) — action-oriented organisation; research fits under Resources (reference material) and Projects (active research outputs).
- [Obsidian Everywhere](obsidian-everywhere.md) — multi-device setup for phone-first capture.
- [MCP Prompts reference](../prompts.md) — especially the ambient-patterns section and `propose-links`.
- [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) — when citation rigour matters.

## Integration Touchpoints

### New files

- `docs/guides/research-workflows.md` — the guide itself.

### Updated files

- `mkdocs.yml` — two nav entries:
  - llmstxt Guides section (~line 70): `- guides/research-workflows.md: Research workflows (seed → ground → absorb → interconnect → produce, with optional scholar-mcp integration)`. Positioned after `guides/obsidian-everywhere.md` (the multi-device setup dependency) and before `guides/zettelkasten.md` (since those guides reference research-workflows from Next Steps).
  - Visible nav block (~line 135): `- Research workflows: guides/research-workflows.md`, in the same position.
- `docs/guides/index.md` — add a table row: `| Do research (literature grounding, interconnected notes, paper drafting) | [Research workflows](research-workflows.md) |`. Positioned after Obsidian Everywhere and before Zettelkasten.
- `docs/guides/zettelkasten.md` — one bullet added to Next Steps: `- **Research workflows**: [docs/guides/research-workflows.md](research-workflows.md) — literature grounding, fact-checking, and writing papers from notes`.
- `docs/guides/para.md` — one bullet added to Next Steps: same wording as above.
- `README.md` — update the existing "Research a topic into your vault" bullet in the "What you can do with it" section to link to the new guide: `- **Research a topic into your vault.** "Research product security regulations..." [...] — see the [Research workflows guide](https://pvliesdonk.github.io/markdown-vault-mcp/guides/research-workflows/) for the full loop.`
- `docs/index.md` — update the parallel bullet in its mirror section to link to the new guide using a relative path: `[Research workflows guide](guides/research-workflows.md)`.

### Zero changes

- `src/` — no code changes.
- `tests/` — no new tests. Pure content PR.
- `examples/` — no new pack or template additions. Guide references the existing Zettelkasten `literature.md` template.
- No new env vars, no new prompts, no new tools.

## Testing Strategy

Content-only PR. Testing:

1. **MkDocs strict build** — `uv run mkdocs build --strict` passes. Catches broken internal links, missing nav entries, unresolved anchors.
2. **Link audit** — every internal link in the new guide resolves. Specifically:
   - `../prompts.md#ambient-patterns-without-prompts` (added in PR #388)
   - `../prompts.md#propose-links` (same PR)
   - `zettelkasten.md` and `para.md` as siblings
   - `obsidian-everywhere.md` for the setup reference
   - The Zettelkasten `literature.md` template path from `../../examples/zettelkasten/templates/literature.md`
3. **Pre-commit** — all hooks pass (trailing whitespace, EOF, YAML, large files). No Python changes, so ruff/mypy skip cleanly.
4. **Manual read-through** — the guide flows from intro → phases → pitfalls → example → next steps without redundancy. Each phase's Baseline/With scholar-mcp/Common failure structure is consistent.

No new unit or integration tests.

## Risks / Tradeoffs

- **scholar-mcp callouts may drift as scholar-mcp evolves.** The guide names specific scholar-mcp tools (`search_papers`, `get_references`, `convert_pdf_to_markdown`, `get_paper`). If scholar-mcp renames or removes any of these, the callouts become inaccurate. Mitigation: the SYNC.md file or a similar cross-repo tracker gets an entry noting the guide's scholar-mcp tool references; when scholar-mcp's tool surface changes, the guide gets updated. Low-risk; scholar-mcp tool names are stable.
- **Web-search capabilities vary by client.** The baseline assumes Claude has web-search tools. Claude Desktop does; Claude.ai does; Claude Code depends on the configured tools. The guide calls this out in a short note so readers on a web-search-less client know to add web search or fall back to scholar-mcp.
- **Worked example risks feeling contrived.** A generic scenario lacks the texture of a real session. Mitigation: keep the example short (3-4 paragraphs), name the phases explicitly, don't over-invent. If it feels thin, the reader still has the phase sections.
- **Overlap with Zettelkasten and PARA guides.** Both guides mention research in passing. The new guide is the canonical home; the older guides stay as-is (their existing content is scheme-specific, not research-specific) and get a Next Steps pointer. No content is moved; no duplication is removed. Low risk of drift.
- **The false-confidence warning may read as preachy.** One short section, not a sermon. The real example (fabricated citation) grounds it. Avoid moralising.

## Future Work

- **Vault-to-paper authoring workflows.** A follow-up guide could cover LaTeX/Markdown-to-PDF pipelines, citation manager integration, and collaborative review. Out of scope here.
- **A research template pack.** If usage shows the Zettelkasten `literature.md` template is widely adopted for research and users ask for a research-specific template with a `relevance` field or a claims section, a small `examples/research/templates/` pack is justified. Not needed now.
- **Integration with the blog, if it's ever published.** The blog could link back to the guide as the practical companion. Not a design concern for this PR.

## Acceptance Criteria

- `docs/guides/research-workflows.md` exists, ~500 lines, follows the section structure in §Guide Structure.
- The five phases each have the Intent / Baseline flow / With scholar-mcp / Common failure structure.
- The Pitfalls section includes the fabricated-citation story (absorbed from the blog) and codifies the claim-tracking convention.
- scholar-mcp is positioned as optional throughout; every baseline flow works without it.
- MkDocs nav has the new entry in both the llmstxt and visible blocks.
- `docs/guides/index.md` has a new table row.
- Zettelkasten and PARA guides each have a Next Steps bullet linking to the new guide.
- README and `docs/index.md` update their existing "Research a topic" bullets to link to the new guide.
- `uv run mkdocs build --strict` passes with no new warnings.
- No `src/`, `tests/`, or `examples/` changes.
