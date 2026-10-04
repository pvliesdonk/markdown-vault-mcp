---
description: "Where each piece of documentation belongs, and who owns it."
kind: explanation
---

# Documentation structure

This page states where each piece of this project's documentation belongs and who owns it. It applies to every page in `docs/` and to `README.md`. Read it before adding or moving a page; agents run the `writing-documentation` skill, which applies it step by step.

## The ownership rule

The project is generated from a template, and the template's owner ruled:

> Template owned the designated areas.
> Documentation about domain knowledge can only exist in places designated by the template for [domain] knowledge.
> Documentation about non-domain knowledge is *always* part of the template.
> No exceptions.

**Domain knowledge** belongs to this server alone. It covers the tools, resources and prompts the server offers and how they behave. Its configuration fields and any bundled library count as well. **Non-domain knowledge** is what would be true of any server generated from the template: installing and running it, Docker, systemd, reverse proxies, authentication and identity providers, the security model's frame, configuration mechanics, setting up MCP clients in general, logging, releases, repository protection, template updates and how to contribute.

## Sections

The site is organised by what its reader is trying to do. The template owns this frame, including the order of the sections and the template pages in each; every section except the first two has a slot where this project lists its own pages.

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

Each section's template pages live in that section's directory (`get-started/`, `deploy/`, `use/`, `reference/`, `upgrade/`, `contribute/`; the Overview and the security model at the top). Moving a template page is a template change, and the template's redirects keep its old URL working.

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

| Place | Owner | Holds |
|---|---|---|
| A template page, outside its sentinel blocks | template | non-domain knowledge only |
| A `DOMAIN-<TOPIC>-<KIND>` block inside a template page (such as the Docker page's extra-notes block) | this project | only what this server adds to that non-domain topic |
| The `DOMAIN-README-*` blocks of `README.md` (badges, pitch, fit, extras, design decisions) | this project | the front door's project-specific text; everything else on the README is the template's frame |
| A generated region (`GENERATED-*` markers, such as the configuration tables) | the generator | facts drawn from the code; change the source, never the page |
| A generated page under `docs/reference/` (it starts with the generator's marker comment) | `scripts/gen_reference.py` | tools, resources, prompts and the command line, read from the code; prose only in its `DOMAIN-INTRO` and `DOMAIN-EXAMPLE-<name>` slots |
| `docs/use/` | this project; the template renders only its `index.md` | this server's how-tos and explanations |
| `docs/reference/api/` | this project | API reference, when the project ships a library |
| A section's slot in `nav:` (its `PROJECT-NAV-<SECTION>` block) | this project | entries for this project's pages in that section |
| `docs/releases/` | this project | the per-release notes |
| `docs/design/`, apart from the pages the template renders there | this project | internal design notes, unpublished |

A fix to a template page, outside its sentinel blocks, goes to `pvliesdonk/fastmcp-server-template`. The page's source there is at the same path with `.jinja` appended, such as `docs/deploy/docker.md.jinja` for `docs/deploy/docker.md`; a page without a `.jinja` source, such as this one, is copied as it stands. A generated region or page changes at its source in this project's code, never on the page.

When this project moves one of its own pages, it adds the old and new paths to the redirects map in `mkdocs.yml`, so the published URL keeps working. An entry left under Unsorted at the end of `nav:` hasn't found its section yet.

A sentinel designates a place, not the knowledge in it. Non-domain text inside a `DOMAIN-*` block still belongs to the template.

When no designated place fits domain knowledge, the gap is the template's. File a template issue describing what the content is and who reads it, instead of writing the page somewhere the template doesn't designate. When knowledge is non-domain and the template has no page for it, that also goes to the template as an issue, not into a project page.

A place that departs from this table counts as a decision only when an issue, a pull request or a design note records why. Otherwise it's debt, however settled the file makes it look.

## Generated reference

The Reference section is written from the code. `scripts/gen_reference.py` builds the server once and reads what it registers: every tool with its title, annotations, parameter schema and docstring; every resource and prompt; and the command line's commands and options. It writes `docs/reference/tools/<group>.md` (one page per group), `docs/reference/tools/index.md` (the jump table), `docs/reference/resources.md`, `docs/reference/prompts.md` and `docs/reference/cli.md`, and keeps the `GENERATED-NAV-TOOLS` region of `nav:` in step. The configuration pages are generated by `scripts/gen_config_surface.py` as before.

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
| E3 | error | a template section-entry page that has lost its link to the security model; a page that no longer exists is skipped |
| W1 | warning | a page outside the places the table above designates |
| W2 | warning | a page without `description:` and a valid `kind:` front matter |
| W3 | warning | entries still under Unsorted in `nav:` |
| W4 | warning | a Python block that carries neither `.run` nor `.fragment` |

The check reads `exclude_docs` in the forms this file uses: `dir/**`, a plain glob, or a bare name that matches any path component. Errors are broken for a reader now, so they fail from the first run. Warnings are documentation debt: they print without failing until this project sets `strict = true` under `[tool.docs-structure]` in `pyproject.toml`, after which new debt fails too. Whether a page's knowledge is domain or non-domain, and whether it reads well, is for review to judge, not the check.

## The security model

The [security model](../security-model.md) is the one page that says what the server can reach, what it changes and who gets in. When a page documents a feature that widens that surface, it links there rather than describing the boundary again.

Report a vulnerability as `SECURITY.md` describes, never in a public issue.
