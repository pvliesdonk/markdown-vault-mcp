"""
mkdocs hooks — applied before the build pipeline starts.

Pygments 2.20.0 introduced a regression: HtmlFormatter crashes when
``filename=None`` is passed (html.escape(None) raises AttributeError).
pymdownx.highlight passes ``filename=title`` where title can be None for
untitled code blocks.  This hook normalises None→"" before the formatter
sees it, making the build compatible with both 2.19.x and 2.20.x.

Tracked upstream as Pygments PR #3078.
"""

from __future__ import annotations

from pygments.formatters import html as _pygments_html

_orig_hf_init = _pygments_html.HtmlFormatter.__init__


def _patched_hf_init(self: _pygments_html.HtmlFormatter, **options: object) -> None:
    if options.get("filename") is None:
        options["filename"] = ""
    _orig_hf_init(self, **options)


_pygments_html.HtmlFormatter.__init__ = _patched_hf_init  # type: ignore[method-assign]
