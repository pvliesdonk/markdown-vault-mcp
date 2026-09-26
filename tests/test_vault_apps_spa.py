"""Tests for the vault SPA behind the MCP Apps scaffold: view partials reach the
served shell, the assembled source is current, and the declared CSP origins
match what the page loads."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import markdown_vault_mcp._server_apps as apps
import markdown_vault_mcp._vault_apps as vault_apps


def test_all_view_modules_reach_served_html() -> None:
    """Every view partial's ``/*@@FILE:views/*.js@@*/`` marker must survive
    assembly into the served app.html.

    A dropped marker is invisible to the build/vendor ``--check`` gates (the
    committed app.src.html and a fresh assembly would *both* lack the view), so
    assert a banner string unique to each view module is present. Each phrase
    below appears only in its own ``views/*.js`` partial."""
    html = apps._SPA_SHELL_HTML
    for banner in (
        "Context Card View",
        "Graph Explorer View",
        "Vault Browser View",
        "Note Preview View",
    ):
        assert banner in html, f"view module missing from served HTML: {banner}"


def test_build_check_is_clean() -> None:
    """The committed app.src.html matches the spa/ partials (offline --check)."""
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "build_spa.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr


def _spa_external_resource_origins(html: str) -> set[str]:
    """Origins the served HTML loads sub-resources from.

    Only resource-loading attributes count: ``href`` / ``src`` on ``link``,
    ``script`` and ``img``, plus CSS ``url()`` references. Bare URLs in
    vendored-library comments and attribution banners are not requests and
    need no CSP grant, so matching them would make the assertion below
    demand grants for origins the page never contacts.
    """
    refs = re.findall(
        r"""<(?:link|script|img)\b[^>]*?\b(?:href|src)\s*=\s*["'](https?://[^"']+)""",
        html,
        re.IGNORECASE,
    )
    refs += re.findall(r"""url\(\s*["']?(https?://[^)"']+)""", html, re.IGNORECASE)
    # Stop the origin at the first "/", "?" or "#": a URL whose query or
    # fragment follows the host with no path ("https://host?x=y") would
    # otherwise carry it into the origin and never match the declaration.
    return {match.group(0) for r in refs if (match := re.match(r"https?://[^/?#]+", r))}


def test_declared_csp_origins_match_what_the_spa_actually_loads() -> None:
    """The declared ``resourceDomains`` and the served HTML cannot drift (#1181).

    The defect this guards was a silent one in both directions: the SPA
    fetched Google Fonts while the declaration was empty, so a host building
    its iframe policy from ``resourceDomains`` had no origin permitting the
    stylesheet — and a blocked font request produces no error, just fallback
    type. Asserting equality rather than containment also catches the reverse,
    a stale grant left behind after an asset is vendored away.
    """
    requested = _spa_external_resource_origins(apps._SPA_SHELL_HTML)
    declared = set(vault_apps._CDN_RESOURCE_DOMAINS)

    assert requested == declared, (
        f"SPA requests {sorted(requested - declared)} without a CSP grant; "
        f"declares {sorted(declared - requested)} it never requests"
    )


def test_declared_csp_origins_reach_the_app_resource() -> None:
    """The constant is what register_apps actually hands to the host.

    The test above compares the SPA against ``_vault_apps``; this one pins the
    ``AppConfig`` that ``register_apps``' resource block takes from
    ``vault_app_resource_config``, so building the CSP from a different
    constant cannot pass unnoticed.

    Asserted as whole-list equality rather than membership: an
    ``"https://…" in <list>`` check is exact in Python, but it is
    indistinguishable from URL substring matching to a static analyzer, and
    equality pins every entry instead of one.
    """
    config = vault_apps.vault_app_resource_config()
    assert config.csp is not None
    domains = config.csp.resource_domains
    assert domains == vault_apps._CDN_RESOURCE_DOMAINS
    assert sorted(domains) == [
        "https://fonts.googleapis.com",
        "https://fonts.gstatic.com",
    ]
