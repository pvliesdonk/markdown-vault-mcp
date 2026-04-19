# Research workflows with markdown-vault-mcp

Research in a vault treats Claude as a persistent sparring partner. The vault is the durable workspace that outlives any single session — notes, links, literature, drafts — and Claude is the reasoning agent you rent by the conversation. Claude's memory doesn't persist; the vault's does. The workflow is to keep pushing what matters into notes, so the next session starts where the previous one stopped.

This guide describes a five-phase research loop: **seed → ground → absorb → interconnect → produce**. It is a loop, not a pipeline — most sessions touch two or three phases and cycle. You seed a note, ground one of its claims against the literature, bounce back to interconnection when Claude notices a similar note you'd forgotten, and end up back in seeding because a new question fell out of what you read. The phases give you a shared vocabulary; they do not prescribe a fixed order.

This guide's baseline works with just Claude, markdown-vault-mcp, and web search. [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) is a separate MCP server that adds rigour — citation graphs, BibTeX export, PDF-to-Markdown conversion — optional but recommended when you need real paper IDs and don't trust the model's memory for citations. Every phase below shows the baseline flow first; the scholar-mcp callouts describe the correctness gain, not a different workflow.

!!! note
    This guide is organising-scheme-agnostic. It works with [Zettelkasten](zettelkasten.md), [PARA](para.md), or no opinionated structure at all. The phase model in this guide composes the vault's generic search, linking, and write tools; it doesn't assume a particular folder layout.

## Setup

Install and configure markdown-vault-mcp per the [Claude Desktop guide](claude-desktop.md) or [Obsidian Everywhere](obsidian-everywhere.md) for the full multi-device setup. This guide assumes your vault is already connected to Claude and that you can invoke vault tools (`search`, `read`, `write`, `get_similar`, `get_context`) from a conversation.

### Adding scholar-mcp (optional)

Skip this section for the baseline. Every flow below works without scholar-mcp; you can add it later once you've decided the power-ups are worth the setup.

**Claude.ai:** add scholar-mcp as a connector from the settings page. See the [scholar-mcp repository](https://github.com/pvliesdonk/scholar-mcp) for the connector URL and auth details.

**Claude Desktop:** add a second entry to `mcpServers` in your config. The vault entry stays as it was; scholar-mcp lives alongside:

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

**Claude Code:** add scholar-mcp via `claude mcp add` or in the MCP settings UI, following the same pattern — a second server entry alongside the vault.

scholar-mcp's own documentation covers API keys, OIDC auth, transport options, and tool details. This guide only references the tools by name. See <https://pvliesdonk.github.io/scholar-mcp/> for setup specifics.

**Web-search caveat.** The baseline flows assume Claude has web-search tools available. Claude.ai and Claude Desktop do by default; other clients vary. Without web search, fall back to scholar-mcp for the grounding phase or skip straight to manual source collection — paste a URL into the conversation and let Claude extract from there.

## How the phases compose

The five phases are not a linear pipeline. A typical session starts in one phase, spends most of its time in another, and ends in a third. Some common session shapes:

- **Seed + ground.** You have a new idea. You write the seed, mark the load-bearing claims, and start grounding one or two of them. The session ends with the seed half-grounded; remaining markers wait for next time.
- **Absorb + interconnect.** You read a paper and want to capture it. You write the literature note, then immediately run `get_similar` against it to see which existing research notes it connects to. You add links in both directions before closing the session.
- **Produce + all four predecessors.** You're writing a draft. Every paragraph touches an earlier phase — you reseed when a new question appears, ground when a claim gets challenged, absorb when you realise a source is underdeveloped, interconnect when Claude surfaces a forgotten note.

The phases are vocabulary for the shape of what you're doing in the moment. When stuck, ask: which phase am I in, and which phase should I be in? The answer usually shifts the next ten minutes of work productively.

## Phase 1: Seeding notes

**Intent.** Get a new note into the vault quickly without worrying about its final shape.

### Baseline flow

Seeding covers three common entry points, each corresponding to a different origin story for a research idea:

- **From a conversation.** If your client exposes chat history (Claude.ai has `conversation_search` and `recent_chats`), the [`para-capture-chats`](para.md#para-capture-chats) prompt distils recent chats into Inbox-shaped notes ready for later triage. The vault doesn't need to be a PARA vault — the prompt produces plain notes with titles and bodies; you choose where they land.
- **From a research question.** The [`research`](../prompts.md#research) builtin prompt takes a topic and produces a single synthesis note with a topic slug. Good for "I want to know what's out there on X" before you have a thesis.
- **From a raw thesis.** Just write it. Prose to Claude: "Create a note at `research/consent-dynamics.md` with this thesis: *Informed consent in platform terms of service is structurally impossible because…* Tag it `draft`."

All three paths end at the same place: a new note in the vault with a title, a rough body, and at least one `[citation needed]` or `[verify]` marker on anything that isn't yet grounded. You are not trying to produce polished prose. You are trying to produce something you can come back to.

Claude composes `write(path="research/consent-dynamics.md", content=..., frontmatter={"tags": ["draft"], "created": "2026-04-19"})` and you have a seed. A reasonable seed looks like this:

```markdown
---
title: "Consent dynamics in platform terms of service"
tags: [draft]
created: 2026-04-19
---

# Consent dynamics in platform terms of service

Informed consent in platform terms of service is structurally impossible:
users face a take-it-or-leave-it offer, the text is unreadable at scale,
and the cost of reading is asymmetric with the benefit. [citation needed]

## Open claims

- Cost asymmetry claim above
- Are there empirical studies on the "unreadable at scale" assertion?
```

### With scholar-mcp

Not meaningfully different. scholar-mcp doesn't participate in capture — it enters the picture during grounding, when you need to verify what's true, not when you need to get a thought into the vault. The seeding phase is the same with or without it.

### Common failure

Waiting to polish before committing a note. The seed should land rough and be revised across multiple sessions. If you find yourself editing the seed for twenty minutes before saving, you've skipped phase 1 and jumped to phase 5 without the evidence.

The reverse failure mode is also common: committing too many seeds and never returning to any of them. If your `research/` folder is accumulating seeds faster than you're grounding them, that's a signal to stop seeding and start phase 2 on what you already have. Seeding is cheap; that's its strength and its trap.

Write the shape; leave the `[citation needed]` markers. Come back.

!!! tip "Signals you're in phase 1"
    - You're writing prose you don't fully believe yet.
    - Claude is proposing structure; you're pushing back on detail.
    - The only verifiable thing in the note is the frontmatter.

    All three are fine. None are signals to polish.

The `## Open claims` section at the bottom is the secondary tracker. Inline markers flag a specific sentence; the bottom list captures claims too big to fit in a sentence — methodological questions, unresolved scope, "I need to decide whether this is about platforms-as-a-category or just the large ones." Both forms compose; neither is mandatory. Pick what you'll revisit.

A final point on frontmatter: keep it minimal at the seeding stage. A seed doesn't need a `type` field, a `status` field, or a rigid tag taxonomy. It needs a title, a `draft` tag, and a `created` date. If your vault follows a scheme like [Zettelkasten](zettelkasten.md) or [PARA](para.md), the triage step — not the seed step — is where a note acquires its typed frontmatter.

**Finding seeds later.** Seeds are easy to lose track of. A simple convention is to tag all seeds `draft` and periodically run `list_documents()`, then filter on `frontmatter.tags` containing `"draft"`, to see what's still unresolved. (The keyword-search path won't work for pure tag filtering — `search` requires a non-empty query string; `list_documents` returns the full tag-bearing metadata without requiring a query.) Anything that's been a draft for more than a month is either worth grounding or worth deleting; the middle path — a permanent pile of unresolved seeds — is where vault rot starts.

## Phase 2: Literature grounding

**Intent.** Back a claim in a note with real sources; prevent fabricated citations.

### Baseline flow

Grounding converts a seed's confident-but-unsourced claims into claims with sources attached — or flags them clearly as unresolved. Claude + web search does most of the work when web search is available.

Ask template:

> For each claim in `research/consent-dynamics.md` marked `[citation needed]`, search the web for supporting or refuting literature. Update the marker with a linked source and a one-line summary of what the source says. If you can't find anything solid, leave the marker in place and add a line explaining what you looked for.

Claude reads the note, runs web searches, and updates the body via `edit(path=..., old_text="...", new_text="...")`. You review the diff — don't skip this; see [Pitfalls](#pitfalls) — and keep or reject each update. Rejecting is just as valuable as accepting: a claim that web search couldn't ground should stay marked, not be quietly deleted.

The claim-tracking convention. Both forms are recommendations, not requirements:

- **Inline markers** — `[citation needed]` in prose, searchable by grep and familiar from other research contexts. Variants like `[verify]`, `[my guess]`, or `[check]` are fine; pick what you'll actually use.
- **Note-level aggregation** — a `## Open claims` or `## Evidence gaps` section where outstanding items accumulate. As literature fills in, you move items out of this section and into the body as sourced prose.

You can combine them — inline markers for claims that live inside paragraphs, a bottom section for claims that are too big for a single sentence. The section becomes a checklist the next session can pick up without re-reading the whole note.

The discipline is what matters, not the syntax. Name every unverified claim so you can come back to it. A note without markers is either fully sourced (rare early on) or hiding fabrication (common). Pick a convention you'll remember and stick to it; switching midway through a project fragments the vault's grep-ability.

### With scholar-mcp

`search_papers` returns real paper IDs and metadata — no hallucinated DOIs, no confidently wrong authors. You ask Claude to search for literature on a topic; scholar-mcp returns titles, authors, years, and stable identifiers you can cite. `get_references` walks what a seminal paper cites (backward through the literature graph); `get_citations` finds who cites a paper (forward through the graph). Together they let you traverse the argument a field has had with itself rather than guess at it.

`get_paper(doi=...)` returns structured metadata including BibTeX, so the literature notes you produce in the next phase start with a frontmatter source that resolves. When citation rigour matters — a paper, a grant proposal, a blog post that people will check — scholar-mcp is where it earns its keep.

A typical scholar-mcp grounding flow: Claude runs `search_papers(query="adhesion contracts platform terms of service", limit=10)` for each marked claim, filters to the most relevant results based on titles and venues, calls `get_paper(doi=...)` on the shortlist to get structured metadata, and updates the note with links to the real DOIs. You still review — fabrication risk is lower but not zero when the model is summarising what a paper says — but the identifiers are now anchored to real objects.

### Common failure

Trusting the first plausible-sounding paper without verifying. Always verify the DOI resolves, the authors exist, and the title matches what Claude reports. Claude sometimes writes a confident paragraph citing a paper that does not exist; see the [Pitfalls](#pitfalls) section. Web search reduces this but does not eliminate it — Claude can still summarise a search result incorrectly or mis-attribute a claim to the wrong paper from the same page of results.

A secondary failure: grounding stops at the first source that supports the claim. One paper is a lead, not evidence. Push for at least one corroborating source and at least a quick check for dissent. Claude will happily fetch a second paper if you ask; it rarely volunteers one.

!!! tip "Grounding checklist"
    Before you remove a `[citation needed]` marker:

    - The source exists (DOI resolves, paper is findable).
    - The source actually supports the claim, not a tangentially related claim.
    - You have at least one corroborating source or have actively looked for dissent.
    - The source is linked from the note (URL or DOI in frontmatter or body).

## Phase 3: Absorbing sources

**Intent.** Turn a retrieved source into a durable literature note; decide when a summary suffices versus full text.

### Baseline flow

Once a source is identified, you write a literature note against it. The shape follows the existing Zettelkasten [`literature.md` template](../../examples/zettelkasten/) (under `templates/`), adapted to the research-specific concerns of relevance and open questions:

```markdown
---
title: "Platform terms of service as adhesion contracts"
type: literature
tags: [literature]
source: "https://doi.org/10.XXXX/example"
created: 2026-04-19
---

# Platform terms of service as adhesion contracts

**Authors:** Smith, Jones · 2023
**Source:** https://doi.org/10.XXXX/example

## Key Ideas

- Terms of service operate as contracts of adhesion: one party drafts, the
  other accepts without negotiation.
- Empirically, over 90% of users accept ToS without reading any portion.
- The authors argue adhesion-contract doctrine has evolved to tolerate
  asymmetric drafting as long as the terms are not "unconscionable."

## Relevance

Supports the "structural impossibility" framing in
[[consent-dynamics]]. The 90% statistic is the number I was after.

## Open Questions

- Is the 90% figure replicated in subsequent studies?
- Does the adhesion-contract framing predate the platform era, or was it
  reshaped by it?
```

Claude composes this from the abstract, web snippets, and whatever you've told it about the paper. The output is a vault note linked from the originating research note via `[[wikilink]]`.

The "Relevance" section is the part you cannot skip. A literature note without a relevance paragraph is a bookmark, not a note — it captures what the paper says but not why you care. Six months later, when `get_similar` surfaces the note against a new research question, the relevance paragraph is what tells future-you whether to open it.

### With scholar-mcp

For open-access papers, `convert_pdf_to_markdown` returns the full paper as Markdown text; Claude then `write`s it into the vault at a path like `research/lit/full/smith-2023.md`, creating a Markdown companion to the literature note you're composing. You then write the literature note against the full text rather than just the abstract — the "Key Ideas" section gets the actual argument, not a press-release summary. `get_paper(doi=...)` returns structured metadata (authors, venue, year, BibTeX) for the frontmatter, so you don't guess at how a name is spelled or what year the paper came out.

When the paper is central to your research and you're going to cite it multiple times, converting the full text pays for itself — the vault becomes searchable against the paper's actual claims, and `get_similar` can surface it against future notes. For tangential papers, skip the conversion.

A middle path: convert the PDF to markdown but keep it outside the main note tree, linked from the literature note. The literature note stays short (summary + relevance + open questions); the full text lives as a companion under `research/lit/full/` and is there when you need to verify a direct quote or check a footnote. `read(path="research/lit/full/smith-2023.md")` pulls the full text when you need it; otherwise the lighter literature note is what surfaces in search results.

### Common failure

Reflexively fetching the full PDF. Most of the time the abstract plus the argument you're trying to support is enough, and the literature note stays short. Saving PDF conversion for when you need to dive deep keeps the vault from drowning in attachments and keeps your own reading time honest. Rule of thumb: convert the full text when the paper is the spine of an argument; summarise from the abstract when it's a supporting reference.

A related failure is producing a literature note that is a disguised abstract — same content, same voice, no synthesis. If the note could be generated by the abstract alone, it doesn't belong in the vault; bookmark the paper and move on. The literature notes worth keeping have at least one sentence that the abstract doesn't: a connection to your own thinking, a methodological caveat, a question the paper raises. That sentence is the reason the note exists.

!!! tip "When to convert full text"
    | Signal | Convert? |
    |--------|----------|
    | Paper is the central source for an argument | Yes |
    | You've quoted from it twice | Yes |
    | You need to verify a specific claim's context | Yes |
    | Paper is a supporting reference only | No (abstract suffices) |
    | You already have 3+ notes about the paper | Yes (it's spine, not support) |
    | The abstract answers the question you had | No |

## Phase 4: Interconnection

**Intent.** Surface existing vault notes semantically close to the one you're working on; find forgotten work.

### Baseline flow

Your vault has notes you've forgotten you wrote. The tangent you chased six months ago is probably already a note — named something you'd never think to search for. `get_similar` catches these.

Ask template:

> I'm working on `research/incentive-misalignment.md`. Run `get_similar(path="research/incentive-misalignment.md")` and tell me which of the top 5 candidates should be linked. For each one you think is a link, read it briefly and explain why it connects.

Claude runs the tool, reads the candidates, and proposes links with one-line rationales. You confirm; Claude adds wikilinks via `edit`. The rationale is the important part — a raw `get_similar` score tells you two notes share vocabulary, not that linking them serves the reader. Ask Claude to name the connection in a sentence; reject candidates where the best available rationale is "both notes talk about X."

For a vault-wide sweep — "find every note that should probably be linked but isn't" — invoke [`propose-links`](../prompts.md#propose-links) from Claude.ai's `+` menu (or as a prompt in any MCP client). It walks recent notes, runs `get_similar` per note, filters out pairs that are already linked via `get_outlinks`, applies LLM judgment on the remainder, and produces a batch preview. You approve in bulk; Claude applies the edits.

For examining a specific note's neighborhood without committing to edits, ask Claude to run `get_context(path="research/incentive-misalignment.md")`. It returns backlinks, outlinks, similar notes, folder peers, and tags in one call — the full dossier for deciding what to link and what to write next. Use it before starting a writing session on an existing note: it is cheap, it is comprehensive, and it often surfaces a link you were about to miss.

A useful ancillary is `get_backlinks(path=<note>)` on its own when you specifically want to know "who cites this?" without the rest of `get_context`'s payload. Backlinks are the measure of how load-bearing a note is in the rest of the vault; a literature note with many backlinks is spine for multiple arguments and deserves care. `get_most_linked` surfaces these globally if you want to find the vault's hubs.

### With scholar-mcp

Cross-referencing papers. Your literature notes have DOIs. `get_paper(doi=...)` followed by `get_references` or `get_citations` sometimes reveals that two strands you've been researching separately share a seminal paper — one literature note cites it directly, another cites a paper that cites it. Your literature graph is richer than your note graph; scholar-mcp exposes it.

This is how you notice that a 2018 paper on platform governance and a 2022 paper on AI alignment both build on the same 1997 essay. The vault wouldn't have surfaced this — you hadn't written a note on the 1997 essay. scholar-mcp walked the citation graph and found the common ancestor.

The workflow: for each literature note's DOI, ask Claude to pull `get_references(doi=...)` and compare against the DOIs in other literature notes. Overlap above a threshold is a signal that the notes are closer than their vault topology suggests. You then decide whether to write a note on the shared ancestor, link the two notes directly, or both.

`get_citations` (forward direction) is the complementary tool. When a paper in your vault has been cited a lot since you wrote the literature note, some of those citations may have replied to the original argument. Running `get_citations(doi=<paper>)` a few months after absorbing a paper sometimes surfaces a newer paper that moved the argument forward — grounds for an updated literature note or a new one.

### Common failure

Missing an existing note because you'd forgotten what you called it. Memory alone fails: you remember you had a thought about consent and autonomy, but you titled the note "user-agency-and-dark-patterns" and haven't looked at it since. `get_similar` catches these; `propose-links` catches them at scale. The failure mode isn't "I don't have the note" — it's "I have the note and can't recall the search term that would surface it."

A second failure: the opposite — over-linking noise. `get_similar` will return five candidates every time, and at least one is usually a stretch. Keep the rationale bar high. A link that exists only because both notes mention "platforms" dilutes the link graph; three weeks later when you're running `get_context` to orient yourself, that spurious link is a false signal you have to read past. Link when the connection is specific; reject when the connection is vocabulary.

!!! tip "Link rationale heuristics"
    Accept a proposed link when at least one of these holds:

    - The linked note would change what you write in the current note.
    - The current note answers a question the linked note raises.
    - The two notes share a source, mechanism, or methodological concern.

    Reject when:

    - The rationale is "both discuss X" with no further specificity.
    - The linked note is a hub note you'd link to by default anyway.
    - The connection is a category label, not a substantive claim.

## Phase 5: Writing a paper from notes

**Intent.** Draft a paper from the vault, fact-check as you go, and let the paper and source notes co-evolve.

### Baseline flow

Start a `Draft/consent-essay.md` note. Ask Claude to pull paragraphs from the research notes you've built up — "Draft section 2 by composing from `[[consent-dynamics]]`, `[[platform-adhesion-contracts]]`, and `[[unreadable-at-scale]]`." Claude reads each note, composes prose, and writes the draft.

This is also a good moment to run `get_outlinks(path="Draft/consent-essay.md")` after the first compose, to see which source notes the draft depends on. Any source note that appears here without a prior link *from* the draft's earlier sections is a potential gap — either the new section needs to be linked from the intro, or the source note needs to be foregrounded earlier.

The `list_documents(folder="research/lit")` call also helps at this stage: it enumerates every literature note, and you can ask Claude "which of these isn't linked from the draft yet, and should be?" The answer is sometimes "none, you've used everything relevant"; sometimes it's "these three, and their absence is a gap in section 4."

Then run the grounding loop in reverse. Ask Claude to flag every unsourced or weakly-sourced claim in the draft against `[citation needed]` markers, and for each marker, check whether the originating source note has a citation that fills the gap. If it does, pull the citation into both the draft and the source note (so future readers of the source note see the same grounded version). If it doesn't, you've discovered a gap in the underlying research — flag it, decide whether to go find literature or to soften the claim, and repeat.

Tangents will appear. A paragraph you're writing for the paper wants to veer into a related question that isn't central to the paper's argument. Don't discard it and don't let it dilute the draft. Triage it: capture the tangent as a new note in the vault (PARA Inbox, Zettelkasten Fleeting, or just a standalone file) at the moment it appears, then return to the paper. The note is cheap; the diluted paragraph is expensive.

Claude is good at this triage in flow. Prose to Claude: "That paragraph on signalling is a tangent. Pull it into a new note at `research/signalling-in-platform-design.md` with a link back to this draft, then continue section 3." The draft stays tight; the tangent lives on. You don't have to stop writing to manage the split.

### With scholar-mcp

Generate the references file in the author's preferred format. For every literature note linked from the draft, `get_paper(doi=...)` produces a BibTeX entry; Claude composes these into a single file via `write(path="Draft/consent-essay.bib", content=...)`. Ask template:

> For every literature note linked from `Draft/consent-essay.md`, fetch the paper's BibTeX via scholar-mcp and append to `Draft/consent-essay.bib`. Skip any that are already in the file.

For CSL-JSON (Pandoc, Zotero) or other formats, the same pattern applies — scholar-mcp returns structured metadata; Claude writes the format you specify. This eliminates the hand-curation step that usually accumulates errors at submission time.

An end-to-end scholar-mcp-assisted flow for a paper submission:

1. Literature notes accumulate throughout research, each with a verified DOI in frontmatter (`source: "https://doi.org/..."`).
2. Draft composes via wikilinks to literature notes.
3. `get_outlinks(path="Draft/paper.md")` enumerates the draft's literature dependencies.
4. For each outlink with a literature note, Claude calls `get_paper(doi=<source>)` and appends BibTeX to `Draft/paper.bib`.
5. Pandoc (or your build chain) renders the final paper with `--bibliography=Draft/paper.bib`.

The human step is reviewing the BibTeX — not typing it.

### Common failure

Letting tangents dilute the paper. The failure mode is believing that you'll come back to the tangent later if you just keep it in the draft. You won't. The tangent is interesting precisely because it isn't the paper's core argument; keeping it in the draft means the core argument gets less attention. Fix: capture the tangent as a separate note *at the moment it appears*, not at the end of the session. The interrupt is two minutes; the cleanup is an hour.

A related failure is the paper and its source notes drifting out of sync. You strengthen a claim in the draft based on something Claude re-read during composition, but you don't push that strengthening back into the source note. Two months later the source note still says what it said before. Fix: at the end of every writing session, ask Claude to diff the draft against the source notes — "For each note linked from the draft, has anything in the draft added to what the source note says? If so, propose the additions back into the source." Co-evolution only works if both sides evolve.

!!! tip "End-of-session checklist for a draft"
    - Every `[citation needed]` either resolved or explicitly carried forward.
    - Tangent notes captured (and linked from the draft's `## Related tangents` section, if you keep one).
    - Source notes updated with anything the draft discovered that they didn't have.
    - `get_outlinks(path=<draft>)` reviewed; every linked source has a reciprocal backlink (or is an intentional one-way pointer).

## Pitfalls

Research with Claude is fast. Fluency is not evidence of correctness. Most of what follows is how to catch the model before it convinces both of you that an incorrect answer is settled.

### False confidence

Claude writes confidently, even when wrong. The confidence is in the prose, not the content — the same sentence structure that makes a verified claim read clearly makes a fabricated claim read clearly.

A session produced a confident assertion backed by a plausible-sounding paper. The title read like something that should exist, the DOI was formatted correctly, the authors were prominent in the field. The paper did not exist. Only a targeted follow-up — search for the paper, verify authors, check the DOI — caught it. The paragraph around the citation had been convincing enough that the first read didn't flag anything.

Treat every citation as unverified until you've resolved the DOI or found the paper via scholar-mcp or web search. Verification is cheap; unrolling a paper draft that cites a non-existent source six weeks later is not.

A minimal verification protocol:

1. Copy the DOI (or canonical identifier) into a browser or into `get_paper` via scholar-mcp.
2. Confirm the paper resolves and the title matches what Claude reported.
3. Confirm the authors exist and are plausibly the authors of that paper (look for the same names on a citation database).
4. Spot-check one claim the note attributes to the paper against the abstract.

If any step fails, the citation is fabricated — or, more charitably, confused with a different paper. Either way the note is lying to you until you fix it.

### Make uncertainty explicit

AI-written text looks like a finished deliverable even when it's a first draft. Colleagues who read the note assume it's been verified. Future Claude sessions read the note back and treat confident prose as settled fact. The same fluency that makes Claude useful for drafting makes Claude dangerous as a reader of its own prior work.

Fix: mark uncertainty in the note, not just in your head. `[citation needed]`, `[verify]`, `[my guess]` — any convention you'll actually use. The marker is a signal to both future-you and future-Claude that the surrounding prose has not cleared the verification bar yet. Without the marker, the note reads as done. With the marker, the note reads as in-progress, which is the truth.

Claude reads its own output as authoritative when it surfaces again in a future session. This is the mechanism by which confidence compounds: session 1 writes a confident-but-unverified claim, session 2 reads the same claim back through `read` or `get_similar`, treats it as prior work, and cites it in a new note. The fabrication propagates without anyone noticing. Markers break this chain — a future Claude session seeing `[citation needed]` knows the claim is not yet load-bearing.

### Verify before polishing

If a note reads like a deliverable but its claims aren't verified, it's premature polish. The verification loop is the workflow, not a step to skip when you're in flow. The temptation to keep writing while the momentum is there is real; the cost of skipping verification is that the polish compounds the confidence problem. A rough note with markers is safer than a polished note without them.

Order of operations matters. Do the grounding pass first; polish second. If you polish first, the prose reads as finished to both you and to future Claude, and the grounding pass becomes a formality — you tick boxes instead of actually reading sources. If you ground first, the prose is ugly until it's right, which keeps you honest about what still needs work.

A practical rule: a note is only allowed to lose its `[citation needed]` markers when the claim is backed by a specific source you have actually checked. Not "Claude said this paper supports it" — actually checked. The verification step is the workflow's load-bearing element. Every other step is optional; this one is not.

If you're short on time and can't verify a claim in the current session, the honest move is to leave the marker in place and write a brief note next to it explaining what you looked at and what was inconclusive. A marker with context is more useful in the next session than a marker with no trail; a note that deletes the marker without verification is actively misleading.

## A worked example

Here's how the five phases thread together across a month of intermittent work. The scenario: abandoned software dependencies — open-source libraries that stop receiving maintenance while still being depended on by live systems. The goal: a short blog post summarising early-warning signals.

*Seed.* You start with a thesis: abandoned dependencies usually show warning signs 6–12 months before the final commit — slowing commit cadence, unanswered issues, a shrinking maintainer pool. You write it as a seed note at `research/abandoned-deps.md` with the thesis in the body and a `[citation needed]` marker on the 6–12 month claim. Tags: `[draft, supply-chain]`. Five minutes, total.

*Grounding.* A week later you come back. The 6–12 month claim is still marked. You ask Claude to run web searches for empirical work on maintainer attrition and dependency abandonment, with specific attention to timing. Claude returns three candidates: a 2021 paper on open-source project mortality, a 2023 blog post with ecosystem-wide metrics, and a 2020 empirical study on commit cadence as a leading indicator. You skim the blog; one of the papers has a DOI you can verify. You tell Claude to go ahead and write literature notes for the two papers and to drop the blog (interesting, but not a citable source for the claim you're making).

*Absorbing.* Claude produces two literature notes. The 2020 empirical study becomes `research/lit/xie-2020-commit-cadence.md` — full key-ideas section, a relevance paragraph tying it to the seed note, and open questions about sample selection. The 2021 project-mortality paper becomes a shorter summary note: one paragraph of key ideas, one of relevance, no open questions because the paper is peripheral. You don't convert either PDF; the abstracts plus what Claude retrieved are enough.

At the end of the absorbing session, you ask Claude to add reciprocal links from the seed note into each literature note, using `edit` to insert `[[xie-2020-commit-cadence]]` and `[[fritz-2021-project-mortality]]` into the relevant paragraphs. The vault now has a small interconnected cluster where a week ago there was one seed.

*Interconnection.* You ask Claude to run `get_similar` on the seed note. The top result is a note from six months ago titled `maintainer-burnout-signals.md` — you'd completely forgotten about it. You open it; it's directly relevant. Claude adds `[[maintainer-burnout-signals]]` to the seed note and a reciprocal link in the burnout note. The vault graph just got richer without you having to remember anything.

A second `get_similar` candidate is a note on supply-chain attacks from last year; the rationale Claude offers is "both concern open-source library lifecycles," which is vocabulary-level, not substantive. You reject it. Over-linking would have dragged the supply-chain note into every future `get_context` call on this cluster, for no gain.

*Producing.* A month later you write a short blog draft on the topic. You ask Claude to compose from the seed note, the two literature notes, and the burnout note. The draft has one tangent — a paragraph on how package managers could surface abandonment signals programmatically — that doesn't fit the blog's scope. Instead of deleting it, you capture it as a new standalone note, `research/package-manager-abandonment-signals.md`, and return to the blog. The blog ships tight; the tangent lives in the vault, ready for the next session to pick up.

The whole loop took four sessions across a month: seed (5 minutes), ground (20 minutes), absorb (30 minutes), interconnect (10 minutes), produce (90 minutes). Each session picked up where the previous one left off. Claude didn't remember any of it — the vault did. That's the point.

## Next steps

- [Zettelkasten](zettelkasten.md) — idea-centric organisation; pairs well with literature notes and atomic permanent notes.
- [PARA](para.md) — action-oriented organisation; research fits under Resources (reference material) and Projects (active research outputs).
- [Obsidian Everywhere](obsidian-everywhere.md) — multi-device setup for phone-first capture between sessions.
- [MCP Prompts reference](../prompts.md) — especially the [ambient patterns](../prompts.md#ambient-patterns-without-prompts) and [`propose-links`](../prompts.md#propose-links).
- [scholar-mcp](https://github.com/pvliesdonk/scholar-mcp) — when citation rigour matters: citation graphs, BibTeX, full-text PDF conversion.
