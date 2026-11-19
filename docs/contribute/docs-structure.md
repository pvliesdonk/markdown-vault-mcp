---
description: "Where each piece of documentation belongs, and who owns it."
kind: explanation
---

# Documentation structure

This page states where each piece of this project's documentation belongs and who owns it. It applies to every page in `docs/` and to `README.md`. Read it before adding or moving a page; agents run the `writing-documentation` skill, which applies it step by step.

## Domain and general knowledge

Every page here belongs to this project. The project started from `fastmcp-server-template` and no longer tracks it (#2240). It keeps the structure it started with, because that structure gives each topic one page, until #2260 settles how the documentation is organised now.

**Domain knowledge** belongs to this server alone. It covers the tools, resources and prompts the server offers and how they behave. Its configuration fields and any bundled library count as well. **General knowledge** would be true of any MCP server deployed the same way: installing and running it, Docker, systemd, reverse proxies, authentication and identity providers, the security model's frame, configuration mechanics, setting up MCP clients in general, logging, releases, repository protection and how to contribute. General knowledge lives on the general page for its topic. Domain knowledge lives in the places the table below names, which include the `DOMAIN-*` blocks on general pages.

## Sections

The site is organised by what its reader is trying to do. The frame sets the order of the sections and the general pages in each. Every section except the first two has a slot that lists this server's own pages.

| Section | Reader | Kind of page |
|---|---|---|
| Overview | someone deciding whether this server fits | explanation |
| Security model | anyone asking what the server can reach, change or let in | explanation |
| Get started | a newcomer after a first success | tutorial; the Installation page is a how-to |
| Deploy | an operator running it for real | how-to |
| Use | someone who runs it and wants more out of it | how-to or explanation |
| Reference | anyone looking a fact up | reference |
| Upgrade | an operator moving to a new release | how-to |
| Contribute | someone changing the project | how-to or explanation |

Each section's general pages live in that section's directory (`get-started/`, `deploy/`, `use/`, `reference/`, `upgrade/`, `contribute/`; the Overview and the security model at the top). Moving any page adds a redirects entry, as described below.

## Front matter and llms.txt

Every page opens with front matter naming its kind and saying what it's for:

```yaml
---
description: "One sentence on what the page is for."
kind: how-to
---
```

`kind` is `tutorial`, `how-to`, `reference` or `explanation`, following the Sections table. The site's `llms.txt`, the index language-model clients read, is built from `nav:` when the site builds, with one section per top-level nav entry and each page's `description` beside it. A published page outside the nav appears in the section of a nav page in the same directory. No second list needs keeping in step: a page reaches `llms.txt` by being in the nav or next to a page that is, and its line there is only as good as its `description`.

## Where documentation goes

| Place | Changed by | Holds |
|---|---|---|
| A general page, outside its sentinel blocks | this project | general knowledge only |
| A `DOMAIN-<TOPIC>-<KIND>` block inside a general page (such as the Docker page's extra-notes block) | this project | only what this server adds to that general topic |
| The `DOMAIN-README-*` blocks of `README.md` (badges, pitch, fit, extras, design decisions) | this project | the front door's project-specific text; the rest of the README is the general frame |
| A generated region (`GENERATED-*` markers, such as the configuration tables) | the generator | facts drawn from the code; change the source, never the page |
| A generated page under `docs/reference/` (it starts with the generator's marker comment) | `scripts/gen_reference.py` | tools, resources, prompts and the command line, read from the code; prose only in its `DOMAIN-INTRO` and `DOMAIN-EXAMPLE-<name>` slots |
| `docs/use/` | this project | this server's how-tos and explanations |
| `docs/reference/api/` | this project | API reference, when the project ships a library |
| A section's slot in `nav:` (its `PROJECT-NAV-<SECTION>` block) | this project | entries for this project's pages in that section |
| `docs/releases/` | this project | the per-release notes |
| `docs/design/` | this project | internal design notes, unpublished |

A fix to any page lands in this repository. A generated region or page changes at its source in this project's code, never on the page.

When this project moves one of its own pages, it adds the old and new paths to the redirects map in `mkdocs.yml`, so the published URL keeps working. An entry left under Unsorted at the end of `nav:` hasn't found its section yet.

A sentinel designates a place, not the knowledge in it. General text inside a `DOMAIN-*` block belongs on the general page around it.

When no place in the table fits, say so in the pull request or an issue instead of choosing one silently; #2260 is where the places themselves get revisited. General knowledge never goes in a section inside a domain page.

A place that departs from this table counts as a decision only when an issue, a pull request or a design note records why. Otherwise it's debt, however settled the file makes it look.

## Generated reference

The Reference section is written from the code. `scripts/gen_reference.py` builds the server once and reads what it registers: every tool with its title, annotations, parameter schema and docstring; every resource and prompt; and the command line's commands and options, descending into each command group so a sub-command gets its own section under its full path. It writes `docs/reference/tools/<group>.md` (one page per group), `docs/reference/tools/index.md` (the jump table), `docs/reference/resources.md`, `docs/reference/prompts.md` and `docs/reference/cli.md`, and keeps the `GENERATED-NAV-TOOLS` region of `nav:` in step. The configuration pages are generated by `scripts/gen_config_surface.py` as before.

A fact on these pages changes at its source. The description a page shows is the description the server sends to clients, so the two cannot disagree; the `Returns:` and `Raises:` sections of a tool's docstring are published too, so they are held to site quality (Vale lints them). A tool's group is its `group:<slug>` tag when it has one, else the module that registers it, or that module's package when the module is called `register` (so the library's `_jobs.register` gives `jobs`). Each page has a `DOMAIN-INTRO` slot, and each tool and prompt a `DOMAIN-EXAMPLE-<name>` slot; those survive regeneration, and a slot whose tool no longer exists fails the run. Run `uv run python scripts/gen_reference.py` after changing a tool, resource, prompt or CLI option; pre-commit and CI fail while a page is stale.

## Examples

A reader copies a fenced block as written, so `tests/test_published_examples.py` checks each one against its claim. Tags use the `{ .class key="value" }` form after the language, the one form the Markdown renderer keeps as a fence:

| Block | Tag | What the test does |
|---|---|---|
| Python a reader runs | ```` ```python { .run data-expect="results" } ```` | runs it as written, after the substitutions `docs_example_substitutions` in `tests/conftest.py` supplies (a placeholder path for a fixture, say); `results` must be truthy afterwards |
| Python that is only a snippet | ```` ```python { .fragment } ```` | nothing; the tag records the decision |
| A configuration that claims something | ```` ```json { .config data-expect="read_only=True" } ```` on an MCP client configuration, or on a dotenv-shaped shell block | loads the project's configuration from the block's variables and checks each `field=literal` |
| Any shell block | none | an unquoted `pkg[extra]` fails; zsh expands it as a glob, so write `"pkg[extra]"` |

A Python block with neither tag is debt: the structure check reports it as W4 until it carries one.

## Each topic has one page

Each topic has one page that answers it. Other pages link to that page and never restate it. A short "you need X; see Y" line is a link, not a copy. When two pages answer the same question, readers get two answers, and one of them goes stale.

Each page is also one kind of text. A tutorial teaches a first success, and a how-to walks through one task. Reference is consulted rather than read; an explanation says why. A page that mixes kinds serves none of its readers well.

## Checks

`scripts/check_docs_structure.py` checks this page's rules that a machine can decide. It runs as a pre-commit hook and in the docs workflow, before the site is built. Run it yourself with `uv run python scripts/check_docs_structure.py`.

| Code | Level | What it finds |
|---|---|---|
| E1 | error | a link on a published page to something the site doesn't serve, such as a page `exclude_docs` drops or a path outside `docs/` |
| E2 | error | a published page that neither the nav nor `llms.txt` reaches: not in `nav:`, and no nav page in its directory |
| E3 | error | a section-entry page that has lost its link to the security model; a page that no longer exists is skipped |
| W1 | warning | a page outside the places the table above designates |
| W2 | warning | a page without `description:` and a valid `kind:` front matter |
| W3 | warning | entries still under Unsorted in `nav:` |
| W4 | warning | a Python block that carries neither `.run` nor `.fragment` |

The check reads `exclude_docs` in the forms this file uses: `dir/**`, a plain glob, or a bare name that matches any path component. Errors are broken for a reader now, so they fail from the first run. Warnings are documentation debt: they print without failing until this project sets `strict = true` under `[tool.docs-structure]` in `pyproject.toml`, after which new debt fails too. Whether a page's knowledge is domain or non-domain, and whether it reads well, is for review to judge, not the check.

## The security model

The [security model](../security-model.md) is the one page that says what the server can reach, what it changes and who gets in. When a page documents a feature that widens that surface, it links there rather than describing the boundary again.

Report a vulnerability as `SECURITY.md` describes, never in a public issue.
