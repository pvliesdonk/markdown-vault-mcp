---
name: writing-documentation
description: >-
  Use before adding, moving or substantially changing a page in docs/ or README.md, or a DOMAIN block in a general docs page: decides whether the knowledge is domain or general, which existing page or designated place takes it, and what the change must link to.
---

# Writing documentation

`docs/contribute/docs-structure.md` is the contract: domain versus general knowledge, the places documentation may go, one topic per page, and the security-model link. This skill is the order in which you apply it. When the two appear to disagree, the page wins; don't restate it here.

## 1. Domain or general?

Decide what the knowledge is before deciding where it goes. The test: would it be true of any MCP server deployed the same way? Then it's general, and it goes on the general page for its topic (Docker, reverse proxies, authentication, contributing), not into a page about this server's features.

If you can't decide, say so in the change or the issue with both readings. Don't guess silently.

## 2. Does a page for this topic already exist?

Search `docs/` and `README.md` for the topic before writing. If a page answers the question, change that page. A second page on the same topic is the most common way documentation starts contradicting itself.

## 3. Which place?

For domain knowledge, use the table in the contract page:

- adding to a general topic (a mount, an environment file, a client config for this server): the `DOMAIN-*` block on the general page for that topic;
- a fact the code already holds (a config field, a default, a tool's parameters or what it returns): its source, so the generator carries it. Never hand-edit a `GENERATED-*` region or a generated page under `docs/reference/`; after changing a tool, resource, prompt or CLI option, run `uv run python scripts/gen_reference.py`;
- a worked example for one tool or prompt: its `DOMAIN-EXAMPLE-<name>` slot on the generated page;
- a how-to or explanation of this server's own features: a page in `docs/use/`;
- API reference for a library the project ships: `docs/reference/api/`;
- release narrative: `docs/releases/`;
- internal design: `docs/design/`.

A new page also needs a nav entry: in the `PROJECT-NAV-<SECTION>` block of the section whose reader it serves (the contract page's Sections table), never outside those blocks. Moving one of this project's pages means a redirects entry from the old path to the new one.

If none fits, say so in the pull request or an issue, as the contract page's "Where documentation goes" says; don't pick a place silently.

## 4. Front matter and links the change owes

- Every page carries `description:` (one sentence on what it's for; it's the page's line in `llms.txt`) and `kind:` (`tutorial`, `how-to`, `reference` or `explanation`, per the contract page's Sections table).

- A feature that widens what the server can reach or change links to `security-model.md`, and the security model's domain block says what the feature adds.
- A page that needs another topic links to that topic's page instead of summarising it.

## 5. Examples a reader will paste

- Commands run as shown on macOS's default shell: quote package extras (`"pkg[extra]"`). `tests/test_published_examples.py` fails an unquoted one.
- A configuration example says what it configures, and does it. Tag it ```` ```json { .config data-expect="read_only=True" } ```` (a dotenv-shaped shell block takes the same tag) and the test loads it and checks the claim.
- Code examples run as written against a fresh setup, including any build or init step they depend on. Tag a runnable Python block ```` ```python { .run data-expect="results" } ````; placeholder paths are swapped by the `docs_example_substitutions` fixture in `tests/conftest.py`. A block that is only a snippet gets ```` ```python { .fragment } ````; one with neither tag is W4 debt in the structure check.

## 6. Before you finish

- Run `uv run python scripts/check_docs_structure.py`: an error is a broken page to fix now; a warning is debt, and your change shouldn't add any.
- Build the site with `uv run mkdocs build --strict` and run Vale on the changed pages.
- An entry still under Unsorted at the end of `nav:` is unfinished: sort it into its section.
- Name the reader the change serves, and what they can now do, in the pull request description.
