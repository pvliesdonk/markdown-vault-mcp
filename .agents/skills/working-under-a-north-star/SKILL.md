---
name: working-under-a-north-star
description: >-
  Use before planning, changing or reviewing a subsystem listed in
  docs/design/north-stars.md (OKF today), and whenever such a change needs a
  decision and it is unclear whether the subsystem's north star settles it,
  says nothing about it, or rules it out.
---

# Working under a north star

A governed subsystem's adopted north star is its direction for the whole
lifetime of the project. Every change is made against it, and only the
account holder changes it, by adopting a revision as a whole.

## 1. Read before planning

1. Find the subsystem in `docs/design/north-stars.md`, and the documents the
   registry lists under its north star.
2. Read the north star in full, every time; never work from memory of it.
3. Name the passages that govern the change in the issue and in the pull
   request.

## 2. Classify every decision the change needs

| The north star | Observable test | Do |
|---|---|---|
| **settles it** | A passage, or a decision recorded on its reopening issue, states the rule. | Follow it and cite the passage. |
| **is silent** | No passage or recorded decision covers the question, and no option you would pick is ruled out by one. | Decide it yourself as an implementation choice. Pick the option most consistent with the north star and the external spec. In the pull request, write "The north star is silent on X; I chose Y because Z", and continue. Review checks the choice. |
| **rules it out, or is wrong or outdated** | The change needs behaviour a passage rules out; or a passage no longer matches the spec it cites, the code it describes, or a later decision. | Stop the change and reopen the north star (section 3). |

Before calling a question silent, map it onto what the north star already
settles. Asking a settled question in other terms counts as ruling it out,
not as silence: for example, splitting OKF by reading versus writing, where
the north star splits it by managed versus unmanaged. Where the external
spec states a rule, the spec decides, and a departure from it is a bug.

Silence never stops the work. A reviewer's "pick something sensible" never
overrides a passage that rules the choice out.

## 3. Reopen the north star

Follow the method of
[#2459](https://github.com/pvliesdonk/markdown-vault-mcp/issues/2459):

1. **Open a research issue** with the Research form and an appetite, under
   epic #2460. Block the changes that depend on it.
2. **Audit.** Run several independent auditors, each on one slice of the
   north star. Each sees only the documents and the code, never the issue or
   your expectations. For every finding they give:
   - a verbatim north-star quote;
   - a `path:line`;
   - one class: outdated, departs, silent or conflicts.

   They also list the commitments they checked and found consistent.
3. **Verify** every cited locator yourself. Then post the auditors' reports
   and a summary on the issue.
4. **Bring the questions only the account holder can settle.** For each, give
   the options and what the spec and the north star say about each.
   Recommend only where the account holder's recorded words support it.
5. **Record each decision on the issue,** quoting the account holder's words.
6. **Propose the revision** in a draft pull request. Where a question is
   still open, mark it in the text. The account holder adopts the revision by
   merging it.
7. **After adoption:**
   - bring the documents under the north star into line;
   - file every departure as a bug or feature;
   - update the roadmap index;
   - close the research issue with a verdict.

## Common mistakes

| Mistake | Instead |
|---|---|
| Stopping because the north star does not mention the question | Decide it, and state the choice in the PR |
| Treating a reviewer's go-ahead as settling a ruled-out choice | Reopen; only the account holder changes the north star |
| Recording your own proposal as the account holder's decision | Quote their words; mark the rest as proposal |
| Amending one line of the north star inside an unrelated pull request | Reopen and revise it as a whole |
