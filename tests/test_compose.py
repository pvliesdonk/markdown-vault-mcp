"""Contract tests for the shipped ``compose.yml``.

Template-owned and generic: nothing here reads a project-specific value that
is not already in the file. ``compose.yml`` is re-rendered on every ``copier
update`` and is the first file an operator opens, so the invariants that make
it *work out of the box* are asserted rather than assumed — no test covered it
between the commit that created it and the one that added this file, which is
how it came to publish no ports, declare no health check, and route a reverse
proxy off the server's bind address (template #551 / #552).

Deliberately not asserted: anything needing a Docker daemon. ``docker compose
config`` runs in CI's container job (which already builds and runs the image),
not in this unit lane.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "compose.yml"

SERVICE_NAME = "markdown-vault-mcp"

#: Every domain seam the file carries, as ``NAME`` (the ``-START`` / ``-END``
#: suffixes are appended below). A project's own content goes inside these and
#: survives ``copier update``; anything outside them is template-owned and will
#: conflict. Adding a seam here without adding it to ``compose.yml.jinja``
#: fails, and vice versa.
DOMAIN_SEAMS = (
    "DOMAIN-COMPOSE-VOLUMES",
    "DOMAIN-COMPOSE-ENVIRONMENT",
    "DOMAIN-COMPOSE-SERVICES",
    "DOMAIN-COMPOSE-VOLUME-NAMES",
)


@pytest.fixture(scope="module")
def raw() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose(raw: str) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(raw))


@pytest.fixture(scope="module")
def template_owned(raw: str) -> str:
    """``compose.yml`` with every seam body removed.

    Assertions about what the *template* put in the file must not fire on
    what a project put in its own seams — that content is the whole point of
    the seams, and this test ships into every project, not just this one.
    """
    text = raw
    for seam in DOMAIN_SEAMS:
        head, _, rest = text.partition(f"# {seam}-START")
        _body, sep, tail = rest.partition(f"# {seam}-END")
        if not sep:
            continue
        text = f"{head}# {seam}-START\n{sep}{tail}"
    return text


@pytest.fixture(scope="module")
def service(compose: dict[str, Any]) -> dict[str, Any]:
    services = compose["services"]
    assert SERVICE_NAME in services, (
        f"compose.yml declares {sorted(services)}, not {SERVICE_NAME!r} — the "
        "service key is the project name, and the Traefik/proxy examples in "
        "docs/deployment/ reference it by that name."
    )
    return cast("dict[str, Any]", services[SERVICE_NAME])


def test_compose_is_valid_yaml(compose: dict[str, Any]) -> None:
    assert isinstance(compose, dict)
    assert "services" in compose


def test_image_is_pulled_not_built(service: dict[str, Any]) -> None:
    """``build:`` beside ``image:`` makes ``docker compose up -d`` build from
    the local checkout on any host that does not already have the image,
    instead of pulling the published one."""
    assert "image" in service
    assert "build" not in service, (
        "compose.yml must not carry `build:` — the quick start pulls the "
        "published image. Build explicitly with `docker build` instead."
    )


def test_publishes_the_port_the_image_serves(service: dict[str, Any]) -> None:
    """Without a published port the file only works behind a proxy that shares
    its network, which compose.yml deliberately does not assume."""
    published = [str(entry) for entry in service.get("ports", [])]
    assert any(entry.endswith(":8000") for entry in published), (
        f"expected a mapping onto container port 8000, found {published!r}"
    )


def test_env_file_is_optional(service: dict[str, Any]) -> None:
    """A named ``env_file`` that does not exist is a hard error, not a skipped
    entry — so without ``required: false`` a freshly cloned project (which has
    ``.env.example`` but no ``.env``) cannot start at all."""
    entries = service["env_file"]
    assert isinstance(entries, list), (
        f"env_file must use the long form so `required:` can be set; found {entries!r}"
    )
    dotenv = [e for e in entries if isinstance(e, dict) and e.get("path") == ".env"]
    assert dotenv, f"expected an entry for .env, found {entries!r}"
    assert dotenv[0].get("required") is False, (
        "the .env entry must set `required: false` so a checkout without a "
        ".env still starts on defaults"
    )


def test_environment_holds_only_what_compose_determines(
    service: dict[str, Any],
) -> None:
    """``environment:`` overrides ``env_file:``, so anything listed here wins
    over the operator's ``.env`` silently. It is reserved for values this file
    itself determines — paths on the volumes it mounts."""
    environment = service.get("environment") or {}
    assert environment.get("FASTMCP_HOME") == "/data/state/fastmcp", (
        "FASTMCP_HOME must point at the state volume this file mounts; "
        f"found {environment.get('FASTMCP_HOME')!r}"
    )


def test_declares_a_liveness_healthcheck(service: dict[str, Any]) -> None:
    """The probe is a ``GET`` of the liveness route on the image's port.

    ``/health`` and not ``/health/ready``: readiness fails on an unreachable
    backing store, which no restart fixes and which would hold up every
    ``service_healthy`` dependent. The path is the one the default ``/mcp``
    mount publishes — a deployment that moves the mount must move the probe.
    """
    test = service["healthcheck"]["test"]
    assert test[0] == "CMD", f"expected an exec-form healthcheck, found {test!r}"
    command = " ".join(str(part) for part in test)
    assert "127.0.0.1:8000/health" in command, (
        f"expected the probe to GET /health on port 8000, found {command!r}"
    )
    assert "/health/ready" not in command, (
        "the compose probe is liveness; readiness belongs to the load balancer"
    )


def test_every_named_volume_mounted_is_also_declared(
    compose: dict[str, Any], service: dict[str, Any]
) -> None:
    """A named volume needs two entries — the mount on the service and the
    top-level declaration. That is why the file carries a separate
    DOMAIN-COMPOSE-VOLUME-NAMES seam, and this is the invariant it exists for:
    a domain volume added in only one place makes compose fail to start.
    """
    declared = set(compose.get("volumes") or {})
    for mount in service.get("volumes", []):
        if isinstance(mount, dict):
            # Long syntax. Only `type: volume` names a top-level volume;
            # bind/tmpfs sources are host paths and declare nothing.
            if mount.get("type") != "volume":
                continue
            source = str(mount.get("source", ""))
        else:
            source = str(mount).split(":", 1)[0]
        if not source or source.startswith(("/", ".", "~", "$")):
            continue  # a bind mount (or an anonymous volume) declares nothing
        assert source in declared, (
            f"named volume {source!r} is mounted but not declared under the "
            f"top-level `volumes:` key (declared: {sorted(declared)})"
        )


def test_no_prefixed_env_var_is_interpolated(template_owned: str) -> None:
    """``compose.yml`` used to interpolate ``MARKDOWN_VAULT_MCP_HOST`` into a
    Traefik router rule, where it means a public hostname, while the server
    reads that same variable as the address it binds to (template #551).
    Since ``env_file: .env`` also pushes the value into the container, one
    ``.env`` line drove two incompatible meanings.

    Compose interpolates from the same ``.env``, so any ``${MARKDOWN_VAULT_MCP_*}``
    reference re-creates that coupling. Values the server reads belong in
    ``.env`` alone; a value this file needs belongs in a name of its own.
    """
    found = re.findall(
        r"\$\{?" + re.escape("MARKDOWN_VAULT_MCP") + r"_[A-Z0-9_]+",
        template_owned,
    )
    assert not found, (
        f"compose.yml interpolates {sorted(set(found))} — server configuration "
        "reaches the container through env_file, not through interpolation."
    )


@pytest.mark.parametrize("seam", DOMAIN_SEAMS)
def test_domain_seam_is_present_exactly_once(raw: str, seam: str) -> None:
    """Domain content outside these blocks is overwritten on ``copier
    update``; a duplicated marker makes the block ambiguous."""
    for suffix in ("START", "END"):
        marker = f"# {seam}-{suffix}"
        assert raw.count(marker) == 1, (
            f"expected exactly 1 {marker!r} in compose.yml, found {raw.count(marker)}"
        )
