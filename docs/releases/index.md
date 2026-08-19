# Release notes

Human-readable notes for each minor release: what changed, who is affected,
and what to do about it, with every claim linked to the issue or pull request
behind it.

## Available pages

<!-- RELEASE-PAGES-START -->
- [4.0](4.0.md) (released August 19, 2026).
- [3.1](3.1.md) (released July 8, 2026). Backfilled.
- [3.0](3.0.md) (released June 17, 2026, with patch releases through v3.0.4).
  Backfilled.
- [1.x](1.x.md) (March 9 to May 3, 2026; the whole 1.x line on one page).
  Backfilled, and closed: the 1.x line receives no further releases, so that
  page is final. The pages above it stay open and gain a section whenever their
  minor gets a patch release.
<!-- RELEASE-PAGES-END -->

## How these pages work

**One page per minor version.** Each minor release gets a single page, named
for the minor (`3.1`, `3.2`, and so on). This matches how the documentation site is
deployed: each minor is published as its own site version, so the notes for a
release always travel with the documentation that describes it.

**Patch releases append to the minor's page.** A patch release adds a dated
section to its minor's page rather than getting a page of its own, so the full
story of a minor stays in one linkable document.

**The GitHub release links here.** The release body on GitHub carries a short
summary and a link to the matching page on this site. These pages are the
canonical narrative; the release body is the pointer.

**`CHANGELOG.md` stays machine-generated.** The
[commit-level audit trail](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/CHANGELOG.md)
records what landed, commit by commit; these pages explain whether to upgrade
and what to check first. The two are generated separately and neither replaces
the other.

## Coverage

Pages marked **backfilled** were reconstructed after the fact from commit
history and whatever issues existed at the time
([#1058](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1058)), and
each carries a banner saying so. They are weaker evidence than a page written
at release time, especially for upgrade guidance. The 1.x line is deliberately
one page rather than 27: the record behind it is too thin to support 27, and
the value of per-release detail decays with age.

Pages for future releases are drafted by a generation workflow maintained
upstream in the project template
([fastmcp-server-template#347](https://github.com/pvliesdonk/fastmcp-server-template/issues/347)),
reviewed as an ordinary pull request before publication.
