#!/usr/bin/env python3
"""Vendor CDN dependencies into static/app.html for offline use.

Downloads pinned library versions and inlines them into the SPA HTML,
eliminating runtime CDN dependencies.  The source template is
``static/app.src.html`` (human-editable, with CDN ``<script src>`` tags and
an ``ext-apps`` ES-module import); this script produces ``static/app.html``
(self-contained, committed).

This script is template-owned and ships byte-identical to every project, so
it discovers ``src/<module>/static/app.src.html`` at runtime rather than
hard-coding a package name.

Usage::

    python scripts/vendor_spa.py              # Generate app.html
    python scripts/vendor_spa.py --check      # Verify app.html is up-to-date (offline)
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Vendored dependency versions — bump here when upgrading
# ---------------------------------------------------------------------------

VENDORED_VERSIONS: dict[str, dict[str, str]] = {
    # The MCP Apps frontend SDK — required by every MCP Apps UI.  Template-owned;
    # bump the version + sha256 here to roll the whole fleet at once.
    "ext-apps": {
        "version": "1.3.1",
        "url": "https://unpkg.com/@modelcontextprotocol/ext-apps@1.3.1/app-with-deps",
        "sha256": "36495489aa8939e4eb7421c8a03c220b9f502d79e87895f88599eb6c02377fdd",
        "type": "module",
        "import_specifier": "@modelcontextprotocol/ext-apps",
    },
    # DOMAIN-VENDOR-LIBS-START — add project-specific CDN libs here; kept across copier update.
    # Example (UMD script tag in app.src.html: <script src="https://unpkg.com/marked@.../marked.umd.js"></script>):
    #   "marked": {
    #       "version": "17.0.5",
    #       "url": "https://unpkg.com/marked@17.0.5/lib/marked.umd.js",
    #       "sha256": "<sha256-of-the-download>",
    #       "type": "script",
    #   },
    # DOMAIN-VENDOR-LIBS-END
}

# Marker embedded in generated output for offline --check validation
_SOURCE_HASH_MARKER = "<!-- vendor-spa-source-sha256:{hash} -->"
_SOURCE_HASH_RE = re.compile(r"<!-- vendor-spa-source-sha256:([0-9a-f]{64}) -->")


def _discover_static_dir() -> Path:
    """Locate the single ``src/<module>/static`` dir containing app.src.html.

    Called lazily from main() (never at import) so the module stays importable
    for unit tests without a project tree present.
    """
    src_root = Path(__file__).resolve().parent.parent / "src"
    candidates = sorted(src_root.glob("*/static/app.src.html"))
    if not candidates:
        raise SystemExit(f"ERROR: no src/*/static/app.src.html found under {src_root}")
    if len(candidates) > 1:
        found = ", ".join(str(c) for c in candidates)
        raise SystemExit(f"ERROR: multiple app.src.html found: {found}")
    return candidates[0].parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _download(url: str) -> bytes:
    """Download *url* and return its raw bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "vendor-spa/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"ERROR: failed to download {url}: {exc}") from exc


def _source_hash(src_html: str) -> str:
    """SHA-256 of source template + all vendored config fields."""
    h = hashlib.sha256(src_html.encode("utf-8"))
    for name in sorted(VENDORED_VERSIONS):
        cfg = VENDORED_VERSIONS[name]
        h.update(f"{name}={json.dumps(cfg, sort_keys=True)}".encode())
    return h.hexdigest()


def _inline_script(html: str, name: str, cfg: dict[str, str], js: str) -> str:
    """Replace ``<script src="…{name}…"></script>`` with an inline block."""
    pattern = re.compile(
        rf"<script\s+src=\"[^\"]*{re.escape(name)}[^\"]*\">\s*</script>",
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if not match:
        raise ValueError(f"No <script src> tag matched for '{name}'")
    # Escape </script> inside JS to prevent premature tag closure.
    js_safe = re.sub(r"</script>", r"<\/script>", js, flags=re.IGNORECASE)
    tag = f"<script>/* {name}@{cfg['version']} (vendored) */\n{js_safe}</script>"
    return html[: match.start()] + tag + html[match.end() :]


def _inline_module(html: str, _name: str, cfg: dict[str, str], js: str) -> str:
    """Replace an ES module CDN import with an import-map + data-URI."""
    specifier = cfg.get("import_specifier")
    if not specifier:
        raise ValueError(
            f"Module-type dependency '{_name}' requires an 'import_specifier' key"
        )
    if '<script type="module">' not in html:
        raise ValueError(
            'No <script type="module"> tag found — cannot insert import map'
        )
    b64 = base64.b64encode(js.encode()).decode("ascii")
    data_uri = f"data:text/javascript;base64,{b64}"

    import_map_obj = {"imports": {specifier: data_uri}}
    import_map = f'<script type="importmap">\n{json.dumps(import_map_obj)}\n</script>\n'

    # Insert the import map immediately before <script type="module">
    html = html.replace(
        '<script type="module">', import_map + '<script type="module">', 1
    )

    # Rewrite the import URL → bare specifier (derive pattern from cfg["url"])
    cdn_url = re.escape(cfg["url"])
    import_pattern = rf'from\s+"{cdn_url}"'
    new_html = re.sub(import_pattern, f'from "{specifier}"', html)
    if new_html == html:
        raise ValueError(
            f"Import rewrite failed: no 'from \"{cfg['url']}\"' found in HTML"
        )
    return new_html


def _verify_no_cdn_urls(html: str) -> None:
    """Verify no CDN URLs remain in the generated output."""
    for name, cfg in VENDORED_VERSIONS.items():
        url = cfg["url"]
        if url in html:
            raise ValueError(f"CDN URL for '{name}' still present in output: {url}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point.  Returns 0 on success, 1 on failure."""
    check_mode = "--check" in sys.argv

    static_dir = _discover_static_dir()
    src_html_path = static_dir / "app.src.html"
    out_html_path = static_dir / "app.html"

    if not src_html_path.exists():
        print(f"ERROR: source template not found: {src_html_path}", file=sys.stderr)
        return 1

    src_text = src_html_path.read_text(encoding="utf-8")

    # --check: offline validation via embedded source hash
    if check_mode:
        if not out_html_path.exists():
            print(
                f"ERROR: {out_html_path} does not exist — "
                "run  python scripts/vendor_spa.py  to generate it.",
                file=sys.stderr,
            )
            return 1
        current = out_html_path.read_text(encoding="utf-8")
        m = _SOURCE_HASH_RE.search(current)
        if not m:
            print(
                "ERROR: app.html missing source hash marker — "
                "run  python scripts/vendor_spa.py  to regenerate.",
                file=sys.stderr,
            )
            return 1
        expected = _source_hash(src_text)
        if m.group(1) == expected:
            print("OK: app.html is up-to-date.")
            return 0
        print(
            "ERROR: app.html is out of date — "
            "run  python scripts/vendor_spa.py  to regenerate.",
            file=sys.stderr,
        )
        return 1

    # Generate mode: download, verify integrity, and inline
    html = src_text
    for name, cfg in VENDORED_VERSIONS.items():
        print(f"  Downloading {name}@{cfg['version']} …")
        raw = _download(cfg["url"])
        sha = hashlib.sha256(raw).hexdigest()
        print(f"    {len(raw):,} bytes  SHA-256: {sha[:16]}…")

        expected_sha = cfg["sha256"]
        if sha != expected_sha:
            print(
                f"ERROR: SHA-256 mismatch for {name}@{cfg['version']}\n"
                f"  expected: {expected_sha}\n"
                f"  got:      {sha}",
                file=sys.stderr,
            )
            return 1

        try:
            js = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(
                f"ERROR: {name} download is not valid UTF-8: {exc}"
            ) from exc

        try:
            if cfg["type"] == "script":
                html = _inline_script(html, name, cfg, js)
            elif cfg["type"] == "module":
                html = _inline_module(html, name, cfg, js)
            else:
                raise ValueError(
                    f"Unknown dependency type '{cfg['type']}' for '{name}'"
                )
        except ValueError as exc:
            # Inlining failures are usually a malformed DOMAIN-VENDOR-LIBS entry
            # (e.g. a <script src> the HTML doesn't reference); report cleanly
            # rather than dumping a traceback.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        _verify_no_cdn_urls(html)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Embed source hash for offline --check (rfind targets the real </head>,
    # not occurrences inside inlined JS).
    marker = _SOURCE_HASH_MARKER.format(hash=_source_hash(src_text))
    idx = html.rfind("</head>")
    if idx == -1:
        raise ValueError("No </head> tag found in generated HTML")
    html = html[:idx] + marker + "\n" + html[idx:]

    if not html.endswith("\n"):
        html += "\n"

    # Atomic write: a SIGKILL/disk-full mid-write would otherwise leave a
    # truncated app.html that --check reports as "missing marker" rather than
    # "corrupt". write-then-rename is atomic on POSIX/NTFS.
    tmp_path = out_html_path.with_suffix(".html.tmp")
    tmp_path.write_text(html, encoding="utf-8")
    tmp_path.replace(out_html_path)
    print(f"\nWrote {out_html_path} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
