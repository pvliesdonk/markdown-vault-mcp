"""Browser smoke tests for the built config-wizard page.

Marked ``browser``; requires the ``browser`` dependency group and
``playwright install chromium``. Runs against the built ``site/`` directory, so
``mkdocs build`` must have run first.

Two kinds of tests live here, both template-owned and spec-agnostic:

* Framework tests drive the actual rendered page and assert only on invariants
  every spec shares (the ``deployment`` question exists; output carries the
  project identity from ``meta``).
* Generator unit tests import ``generators.js`` directly and feed it synthetic
  specs, so they exercise generator behaviour (``dockerVolume`` / ``dockerPath``
  mapping, ``validateSpec`` rejection of malformed specs) without depending on
  this project's questions.

Domain-specific assertions belong in ``test_config_wizard_domain.py``.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import threading
import typing
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

if typing.TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

SITE = Path(__file__).resolve().parent.parent / "site"

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def site_url() -> typing.Iterator[str]:
    if not (SITE / "configuration-generator" / "index.html").exists():
        pytest.skip("site/ not built -- run `uv run mkdocs build` first")
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(SITE)
    )
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            httpd.shutdown()


@pytest.fixture(scope="module")
def browser(site_url: str) -> typing.Iterator[Browser]:  # noqa: ARG001 — depended on only for fixture ordering
    # Depend on site_url so its "site not built" skip runs BEFORE we try to
    # launch Chromium. In the main CI test lane (no built site, no installed
    # browser) this makes the smoke tests skip cleanly instead of erroring.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser, site_url: str) -> typing.Iterator[Page]:
    # Fresh page per test: the wizard keeps answer state in a module-level JS
    # object, so each test must start from a clean load to stay order-independent.
    pg = browser.new_page()
    pg.goto(f"{site_url}/configuration-generator/")
    pg.wait_for_selector("#cfg-wizard select")
    yield pg
    pg.close()


# --- Framework tests: drive the real page, assert only on universal invariants.


def test_local_emits_claude_config_with_project_name(page: Page) -> None:
    page.select_option('[data-qid="deployment"] select', "local")
    text = page.inner_text(".cfg-output")
    # meta.projectName renders into the Claude config server key + command.
    assert "markdown-vault-mcp" in text


def test_server_emits_docker_image_from_meta(page: Page) -> None:
    page.select_option('[data-qid="deployment"] select', "server")
    text = page.inner_text("#cfg-wizard")
    assert "ghcr.io/pvliesdonk/markdown-vault-mcp:latest" in text


# --- Generator unit tests: import generators.js with synthetic specs.

_GEN_IMPORT = "/javascripts/config-wizard/generators.js"


def _eval_generators(page: Page, body: str) -> typing.Any:
    """Run `body` (an arrow function source) with generators.js imported.

    `body` is JS source of the form `(g) => { ...; return ...; }`; it receives
    the imported generators module as `g`.
    """
    script = (
        "async () => { const g = await import('"
        + _GEN_IMPORT
        + "'); return ("
        + body
        + ")(g); }"
    )
    return page.evaluate(script)


def test_validate_spec_accepts_complete_spec(page: Page) -> None:
    err = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'deployment', label: 'W', type: 'select' }],
            guards: [] };
          try { g.validateSpec(spec); return null; } catch (e) { return e.message; }
        }""",
    )
    assert err is None


def test_validate_spec_rejects_missing_questions(page: Page) -> None:
    err = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' } };
          try { g.validateSpec(spec); return null; } catch (e) { return e.message; }
        }""",
    )
    assert err is not None
    assert "missing questions array" in err


def test_validate_spec_rejects_missing_meta(page: Page) -> None:
    err = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1, questions: [{ id: 'deployment', label: 'W', type: 'select' }] };
          try { g.validateSpec(spec); return null; } catch (e) { return e.message; }
        }""",
    )
    assert err is not None
    assert "missing meta block" in err


def test_validate_spec_rejects_empty_meta(page: Page) -> None:
    # meta is an object but lacks the fields the generators dereference: the
    # exact gap the old `typeof meta === 'object'` guard let through.
    err = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1, meta: {},
            questions: [{ id: 'deployment', label: 'W', type: 'select' }] };
          try { g.validateSpec(spec); return null; } catch (e) { return e.message; }
        }""",
    )
    assert err is not None
    assert "meta.projectName missing or empty" in err


def test_validate_spec_rejects_empty_meta_field(page: Page) -> None:
    err = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: '', envPrefix: 'DEMO' },
            questions: [{ id: 'deployment', label: 'W', type: 'select' }] };
          try { g.validateSpec(spec); return null; } catch (e) { return e.message; }
        }""",
    )
    assert err is not None
    assert "meta.dockerImage missing or empty" in err


def test_docker_volume_adds_mount_and_fixes_container_path(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'data_dir', label: 'D', type: 'text', var: 'DEMO_DATA_DIR', dockerVolume: '/data/app' }],
            guards: [] };
          const answers = { deployment: 'server', data_dir: '/host/data' };
          const map = g.buildEnvMap(spec, answers);
          return g.generateDockerRun(spec, answers, map);
        }""",
    )
    assert "-v /host/data:/data/app" in result
    # env var is fixed to the container path, NOT the host path.
    assert "DEMO_DATA_DIR=/data/app" in result
    assert "DEMO_DATA_DIR=/host/data" not in result


def test_docker_volume_empty_answer_uses_placeholder(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'data_dir', label: 'D', type: 'text', var: 'DEMO_DATA_DIR', dockerVolume: '/data/app' }],
            guards: [] };
          const answers = { deployment: 'server' };
          const map = g.buildEnvMap(spec, answers);
          return g.generateDockerRun(spec, answers, map);
        }""",
    )
    assert "-v /path/to/data_dir:/data/app" in result


def test_docker_path_fixes_present_var_without_mount(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'index_path', label: 'I', type: 'text', var: 'DEMO_INDEX_PATH', dockerPath: '/data/state/index.db' }],
            guards: [] };
          const answers = { deployment: 'server', index_path: '/host/index.db' };
          const map = g.buildEnvMap(spec, answers);
          return { docker: g.generateDockerRun(spec, answers, map) };
        }""",
    )
    assert "DEMO_INDEX_PATH=/data/state/index.db" in result["docker"]
    # dockerPath never adds a -v mount.
    assert "/data/state/index.db:" not in result["docker"]
    assert "-v /host/index.db" not in result["docker"]


def test_docker_path_absent_var_stays_absent(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'index_path', label: 'I', type: 'text', var: 'DEMO_INDEX_PATH', dockerPath: '/data/state/index.db' }],
            guards: [] };
          const answers = { deployment: 'server' };
          const map = g.buildEnvMap(spec, answers);
          return g.generateDockerRun(spec, answers, map);
        }""",
    )
    # User left it blank → dockerPath does not inject it (persistence not opted in).
    assert "DEMO_INDEX_PATH" not in result


def test_systemd_uses_raw_host_path_not_container(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [{ id: 'data_dir', label: 'D', type: 'text', var: 'DEMO_DATA_DIR', dockerVolume: '/data/app' }],
            guards: [] };
          const answers = { deployment: 'server', data_dir: '/host/data' };
          const map = g.buildEnvMap(spec, answers);
          return g.generateSystemd(spec.meta, map);
        }""",
    )
    # systemd is a host-path context: no container-path rewrite.
    assert "DEMO_DATA_DIR=/host/data" in result
    assert "/data/app" not in result


def test_compose_quotes_yaml_typed_env_values(page: Page) -> None:
    result = _eval_generators(
        page,
        """(g) => {
          const spec = { version: 1,
            meta: { projectName: 'demo', dockerImage: 'img:latest', envPrefix: 'DEMO' },
            secretKeys: [],
            questions: [
              { id: 'flag', label: 'F', type: 'text', var: 'DEMO_FLAG' },
              { id: 'port', label: 'P', type: 'text', var: 'DEMO_PORT' },
            ],
            guards: [] };
          const answers = { deployment: 'server', flag: 'true', port: '8080' };
          const map = g.buildEnvMap(spec, answers);
          return g.generateCompose(spec, answers, map);
        }""",
    )
    # YAML 1.1 parsers coerce bare true/8080 to bool/int; env values are
    # strings, so compose must emit them quoted.
    assert 'DEMO_FLAG: "true"' in result
    assert "DEMO_FLAG: true\n" not in result
    assert 'DEMO_PORT: "8080"' in result
