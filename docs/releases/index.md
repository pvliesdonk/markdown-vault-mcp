---
description: "Release notes for each minor series, with the steps to upgrade."
kind: how-to
---

# Release Notes

Each minor release series gets one page here telling its story: what
changed, why it matters, and how to adopt it. Patch releases add a versioned
section to their series page, so a series stays one linkable document.
The commit-level record lives in the repository's `CHANGELOG.md`; each
GitHub release links back to its page here. A page's Upgrading section answers
three questions on fixed lines, **Clients**, **State** and **Security
posture**; the [Upgrade](../upgrade/index.md) page says what each answers.

<!-- RELEASE-PAGES-START: newest series first; one list entry per page.
     The first real entry replaces the placeholder line below. -->
- [5.0](5.0.md)
- [4.2](4.2.md)
- [4.1](4.1.md)
- [4.0](4.0.md)
- [3.1](3.1.md). Backfilled.
- [3.0](3.0.md). Backfilled.
- [1.x](1.x.md), the whole 1.x line on one page. Backfilled, and closed: the 1.x line receives no further releases.
<!-- RELEASE-PAGES-END -->

Pages marked backfilled were reconstructed after the fact from commit
history and the issues of the time
([#1058](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1058)), and
each carries a banner saying so. They are weaker evidence than a page
written at release time, especially for upgrade guidance.
