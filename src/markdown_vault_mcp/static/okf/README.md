# Bundled Open Knowledge Format specification

`SPEC.md` is the Open Knowledge Format (OKF) specification, Version 0.2, that
this server implements. The `okf://spec` resource serves it, so a client that
cannot open a web page can still read the format it is asked to follow.

- Source: `okf/SPEC.md` in
  [GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog)
  at commit `62432a095456147ee71e70ac6e4dc0d2dea3ac30` (2026-08-21, the v0.2
  text with the timestamp amendment).
- License: Apache License, Version 2.0. `LICENSE` in this directory is the text of that
  repository's `LICENSE.md` at the same commit; the repository carries no
  `NOTICE` file.
- Changes: trailing whitespace removed from lines 34, 35, 45 and 46, which the
  `trailing-whitespace` pre-commit hook would strip anyway. Nothing else
  differs from the source.

The rest of this package is MIT-licensed; these two files stay under
Apache-2.0.

## Replacing the file

Take the new text from a pinned commit, never a branch. Then update, in the
same change:

1. the commit in `_OKF_SPEC_COMMIT` and the wording of `_OKF_SPEC_NOTICE` in
   `resources.py`;
2. the commit and digest in `tests/test_server.py`;
3. this page;
4. `docs/design/reference/okf-v0.2.md`, which records what that revision
   requires and must describe the text shipped here.
