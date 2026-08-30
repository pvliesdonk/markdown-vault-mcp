"""Tests for the OKF enforced-write layer (#964, design §6).

Covers the four pieces of the frontmatter-enforcement phase:

- The pure transforms in ``okf.py`` (:func:`apply_okf_write_stamp`,
  :func:`append_okf_verification`).
- The runtime seam in ``_okf_write.py`` (actor resolution, the enrichment
  builder, and the suppress contextvar).
- Config cross-validation (``OKF_WRITE`` requires ``OKF_MODE != off``).
- The invalidation matrix end-to-end through a writable, OKF-active vault
  (body write / body edit / frontmatter-only edit clear ``verified``; rename
  preserves it) and the ``okf_verify`` tool under both an authed and a
  no-auth server.
"""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, Any

import frontmatter as fm
import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from markdown_vault_mcp import _okf_write
from markdown_vault_mcp._okf_write import (
    OkfWriteIntent,
    build_okf_write_enrich,
    okf_write_intent,
    okf_write_suppressed,
    resolve_human_subject,
    tool_actor,
)
from markdown_vault_mcp.okf import (
    OkfDetector,
    append_okf_verification,
    apply_okf_write_stamp,
)
from markdown_vault_mcp.server import make_server
from markdown_vault_mcp.vault import Vault
from tests.conftest import (
    _CLEAR_VARS,
    _parse_tool_data,
    wait_for_mcp_writer_drain,
    wait_for_writer_drain,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TODAY = _dt.date(2026, 8, 9)
_ISO = _TODAY.isoformat()


# ---------------------------------------------------------------------------
# Pure transforms: apply_okf_write_stamp
# ---------------------------------------------------------------------------


class TestApplyOkfWriteStamp:
    def test_stamps_generated_on_a_note_without_frontmatter(self) -> None:
        out = apply_okf_write_stamp("# Note\n\nBody.\n", actor="a/1", today=_TODAY)
        post = fm.loads(out)
        assert post.metadata["generated"] == {"by": "a/1", "at": _ISO}
        assert "Body." in post.content

    def test_clears_verified_on_content_change(self) -> None:
        text = (
            "---\n"
            "verified:\n"
            "  - by: human:peter\n"
            "    at: 2026-01-01\n"
            "title: T\n"
            "---\n"
            "# T\n"
        )
        out = apply_okf_write_stamp(text, actor="human:x", today=_TODAY)
        meta = fm.loads(out).metadata
        assert "verified" not in meta
        assert meta["generated"] == {"by": "human:x", "at": _ISO}
        # Unrelated fields survive.
        assert meta["title"] == "T"

    def test_preserves_sources_and_other_fields(self) -> None:
        text = (
            "---\n"
            "type: Playbook\n"
            "sources:\n"
            "  - resource: https://example.com/a\n"
            "    id: a\n"
            "---\n"
            "# P\n"
        )
        meta = fm.loads(apply_okf_write_stamp(text, actor="t/1", today=_TODAY)).metadata
        assert meta["type"] == "Playbook"
        assert meta["sources"] == [{"resource": "https://example.com/a", "id": "a"}]

    def test_same_actor_same_day_re_stamp_is_idempotent(self) -> None:
        # Once a note carries this exact actor/date stamp and no verified,
        # re-stamping is a byte-for-byte no-op — its YAML formatting is frozen.
        once = apply_okf_write_stamp("# T\n\nBody.\n", actor="t/1", today=_TODAY)
        assert apply_okf_write_stamp(once, actor="t/1", today=_TODAY) == once

    def test_human_actor_is_recorded_verbatim(self) -> None:
        out = apply_okf_write_stamp("body\n", actor="human:peter", today=_TODAY)
        assert fm.loads(out).metadata["generated"]["by"] == "human:peter"


# ---------------------------------------------------------------------------
# Pure transforms: append_okf_verification
# ---------------------------------------------------------------------------


class TestAppendOkfVerification:
    def test_creates_verified_list_when_absent(self) -> None:
        out = append_okf_verification("# N\n", subject="peter", today=_TODAY)
        meta = fm.loads(out).metadata
        assert meta["verified"] == [{"by": "human:peter", "at": _ISO}]

    def test_appends_to_existing_list(self) -> None:
        text = "---\nverified:\n  - by: human:alice\n    at: 2026-01-01\n---\n# N\n"
        meta = fm.loads(
            append_okf_verification(text, subject="peter", today=_TODAY)
        ).metadata
        # The pre-existing unquoted YAML date round-trips as a date object; the
        # appended entry carries the ISO string we wrote.
        assert meta["verified"] == [
            {"by": "human:alice", "at": _dt.date(2026, 1, 1)},
            {"by": "human:peter", "at": _ISO},
        ]

    def test_preserves_body_and_other_fields(self) -> None:
        text = "---\ntitle: T\n---\n# T\n\nBody line.\n"
        out = append_okf_verification(text, subject="peter", today=_TODAY)
        post = fm.loads(out)
        assert post.metadata["title"] == "T"
        assert "Body line." in post.content

    def test_non_list_verified_is_replaced(self) -> None:
        # A malformed scalar verified is treated as empty, not crashed on.
        text = "---\nverified: nonsense\n---\n# N\n"
        meta = fm.loads(
            append_okf_verification(text, subject="peter", today=_TODAY)
        ).metadata
        assert meta["verified"] == [{"by": "human:peter", "at": _ISO}]


# ---------------------------------------------------------------------------
# Actor resolution (_okf_write)
# ---------------------------------------------------------------------------


class TestActorResolution:
    def test_tool_actor_format(self) -> None:
        assert tool_actor("1.2.3") == "markdown-vault-mcp/1.2.3"

    def test_resolve_human_subject_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        assert resolve_human_subject() == "peter"

    @pytest.mark.parametrize("value", [None, "local"])
    def test_resolve_human_subject_unattributable(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: value)
        assert resolve_human_subject() is None


# ---------------------------------------------------------------------------
# Enrichment builder (_okf_write.build_okf_write_enrich)
# ---------------------------------------------------------------------------


def _active_detector(tmp_path: Path) -> OkfDetector:
    """An OKF detector forced active via mode='on'."""
    return OkfDetector(tmp_path, mode="on")


def _inactive_detector(tmp_path: Path) -> OkfDetector:
    """An auto-mode detector over a vault with no declaration (inactive)."""
    return OkfDetector(tmp_path, mode="auto")


class TestBuildOkfWriteEnrich:
    def test_returns_none_when_disabled(self, tmp_path: Path) -> None:
        assert (
            build_okf_write_enrich(
                okf_write=False, detector=_active_detector(tmp_path), version="1"
            )
            is None
        )

    def test_stamps_on_active_write(self, tmp_path: Path) -> None:
        enrich = build_okf_write_enrich(
            okf_write=True, detector=_active_detector(tmp_path), version="9.9"
        )
        assert enrich is not None
        meta = fm.loads(enrich("# N\n", "write")).metadata
        # No request intent bound → default tool actor.
        assert meta["generated"]["by"] == "markdown-vault-mcp/9.9"

    def test_noop_on_delete_and_rename_ops(self, tmp_path: Path) -> None:
        enrich = build_okf_write_enrich(
            okf_write=True, detector=_active_detector(tmp_path), version="1"
        )
        assert enrich is not None
        assert enrich("# N\n", "delete") == "# N\n"
        assert enrich("# N\n", "rename") == "# N\n"

    def test_noop_when_inactive(self, tmp_path: Path) -> None:
        enrich = build_okf_write_enrich(
            okf_write=True, detector=_inactive_detector(tmp_path), version="1"
        )
        assert enrich is not None
        assert enrich("# N\n", "write") == "# N\n"

    def test_suppress_intent_makes_it_a_noop(self, tmp_path: Path) -> None:
        enrich = build_okf_write_enrich(
            okf_write=True, detector=_active_detector(tmp_path), version="1"
        )
        assert enrich is not None
        with okf_write_suppressed():
            assert enrich("# N\n", "write") == "# N\n"

    def test_bound_intent_actor_is_used(self, tmp_path: Path) -> None:
        enrich = build_okf_write_enrich(
            okf_write=True, detector=_active_detector(tmp_path), version="1"
        )
        assert enrich is not None
        with okf_write_intent(OkfWriteIntent(actor="human:peter")):
            meta = fm.loads(enrich("# N\n", "write")).metadata
        assert meta["generated"]["by"] == "human:peter"


class TestIntentContextvar:
    def test_current_intent_defaults_none(self) -> None:
        assert _okf_write.current_okf_intent() is None

    def test_intent_reset_after_block(self) -> None:
        with okf_write_intent(OkfWriteIntent(actor="a")):
            assert _okf_write.current_okf_intent() == OkfWriteIntent(actor="a")
        assert _okf_write.current_okf_intent() is None

    def test_suppress_sets_suppress_flag(self) -> None:
        with okf_write_suppressed():
            intent = _okf_write.current_okf_intent()
            assert intent is not None and intent.suppress is True


# ---------------------------------------------------------------------------
# Config cross-validation
# ---------------------------------------------------------------------------


class TestConfigCrossValidation:
    def test_okf_write_requires_non_off_mode(self) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="okf_write"):
            ContentConfig(okf_write=True, okf_mode="off")

    @pytest.mark.parametrize("mode", ["auto", "on"])
    def test_okf_write_allowed_with_active_modes(self, mode: str) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig

        cfg = ContentConfig(okf_write=True, okf_mode=mode)
        assert cfg.okf_write is True

    @pytest.mark.parametrize("bad", ["nope", "ELICIT", "auth", ""])
    def test_okf_verify_rejects_unknown_value(self, bad: str) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="okf_verify must be one of"):
            ContentConfig(okf_write=True, okf_verify=bad)

    @pytest.mark.parametrize("mode", ["off", "trust-auth"])
    def test_okf_verify_non_default_requires_okf_write(self, mode: str) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig
        from markdown_vault_mcp.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="only meaningful when okf_write"):
            ContentConfig(okf_write=False, okf_verify=mode)

    def test_okf_verify_default_allowed_without_okf_write(self) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig

        # The default value must not trip the OKF_WRITE-off interaction guard.
        assert ContentConfig(okf_write=False).okf_verify == "elicit"

    @pytest.mark.parametrize("mode", ["elicit", "off", "trust-auth"])
    def test_okf_verify_allowed_with_okf_write(self, mode: str) -> None:
        from markdown_vault_mcp.config_sections.content import ContentConfig

        cfg = ContentConfig(okf_write=True, okf_verify=mode)
        assert cfg.okf_verify == mode


# ---------------------------------------------------------------------------
# Invalidation matrix — end to end through a writable, OKF-active vault
# ---------------------------------------------------------------------------

_ROOT_INDEX = '---\nokf_version: "0.2"\ntitle: Root\n---\n# Root\n'

_VERIFIED_NOTE = (
    "---\n"
    "title: Playbook\n"
    "verified:\n"
    "  - by: human:peter\n"
    "    at: 2026-01-01\n"
    "generated:\n"
    "  by: process:enrich\n"
    "---\n"
    "# Playbook\n\nBody.\n"
)


def _meta(vault: Vault, path: str) -> dict[str, Any]:
    note = vault.reader.read(path)
    assert note is not None
    return dict(note.frontmatter)


@pytest.fixture
def enforced_vault(tmp_path: Path) -> Iterator[Vault]:
    """A writable vault that declares OKF and has OKF_WRITE enabled."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    col = Vault(source_dir=root, read_only=False, okf_mode="on", okf_write=True)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


class TestInvalidationMatrix:
    def test_body_write_clears_verified_and_stamps(self, enforced_vault: Vault) -> None:
        enforced_vault.writer.write("note.md", _VERIFIED_NOTE)
        wait_for_writer_drain(enforced_vault)
        meta = _meta(enforced_vault, "note.md")
        assert "verified" not in meta
        assert meta["generated"]["by"].startswith("markdown-vault-mcp/")
        assert meta["generated"]["at"] == _dt.date.today().isoformat()

    def test_body_edit_clears_verified(self, enforced_vault: Vault) -> None:
        enforced_vault.writer.write("note.md", _VERIFIED_NOTE)
        wait_for_writer_drain(enforced_vault)
        # Re-establish a verified attestation without going through the enricher.
        (enforced_vault._source_dir / "note.md").write_text(
            _VERIFIED_NOTE, encoding="utf-8"
        )
        enforced_vault.writer.edit("note.md", old_text="Body.", new_text="Changed.")
        wait_for_writer_drain(enforced_vault)
        meta = _meta(enforced_vault, "note.md")
        assert "verified" not in meta
        assert meta["generated"]["by"].startswith("markdown-vault-mcp/")

    def test_body_append_clears_verified(self, enforced_vault: Vault) -> None:
        (enforced_vault._source_dir / "note.md").write_text(
            _VERIFIED_NOTE, encoding="utf-8"
        )
        enforced_vault.index.build_index()
        enforced_vault.writer.append("note.md", "Appended line.\n")
        wait_for_writer_drain(enforced_vault)
        meta = _meta(enforced_vault, "note.md")
        assert "verified" not in meta
        assert meta["generated"]["by"].startswith("markdown-vault-mcp/")

    def test_frontmatter_only_edit_clears_verified(self, enforced_vault: Vault) -> None:
        (enforced_vault._source_dir / "note.md").write_text(
            _VERIFIED_NOTE, encoding="utf-8"
        )
        enforced_vault.index.build_index()
        # Edit a frontmatter line only — still a content change, still invalidates.
        enforced_vault.writer.edit(
            "note.md", old_text="title: Playbook", new_text="title: Renamed Playbook"
        )
        wait_for_writer_drain(enforced_vault)
        meta = _meta(enforced_vault, "note.md")
        assert "verified" not in meta
        assert meta["title"] == "Renamed Playbook"

    def test_rename_preserves_verified(self, enforced_vault: Vault) -> None:
        (enforced_vault._source_dir / "note.md").write_text(
            _VERIFIED_NOTE, encoding="utf-8"
        )
        enforced_vault.index.build_index()
        enforced_vault.writer.rename("note.md", "renamed.md")
        wait_for_writer_drain(enforced_vault)
        meta = _meta(enforced_vault, "renamed.md")
        assert meta["verified"] == [{"by": "human:peter", "at": _dt.date(2026, 1, 1)}]
        # Rename bypasses the enricher: generated is left untouched.
        assert meta["generated"] == {"by": "process:enrich"}


class TestOffModeIdentity:
    def test_write_is_byte_identical_when_okf_write_off(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        root.mkdir()
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = Vault(source_dir=root, read_only=False, okf_mode="on", okf_write=False)
        try:
            col.index.build_index()
            col.writer.write("note.md", _VERIFIED_NOTE)
            wait_for_writer_drain(col)
            on_disk = (root / "note.md").read_text(encoding="utf-8")
        finally:
            col.close()
        # No enricher wired → the note lands exactly as written.
        assert on_disk == _VERIFIED_NOTE


# ---------------------------------------------------------------------------
# End to end through the MCP server (actor threading + okf_verify tool)
# ---------------------------------------------------------------------------

_PLAIN_NOTE = "# Plain\n\nSee [[playbook]].\n"


@pytest.fixture
def enforced_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Env for a writable, OKF-declared vault with the enforced-write layer on."""
    vault = tmp_path / "vault"
    (vault / "guides").mkdir(parents=True)
    (vault / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    (vault / "guides" / "playbook.md").write_text(
        "---\ntitle: Playbook\n---\n# Playbook\n", encoding="utf-8"
    )
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    return vault


@pytest.fixture
def trust_auth_env(enforced_env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Enforced-write vault with okf_verify pinned to the no-confirmation mode."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
    return enforced_env


async def _accept_review(
    _message: str, _response_type: Any, _params: Any, _context: Any
) -> bool:
    """Elicitation handler that affirmatively confirms the review."""
    return True


async def _accept_negative(
    _message: str, _response_type: Any, _params: Any, _context: Any
) -> bool:
    """Elicitation handler that accepts the form but answers 'no'."""
    return False


async def _decline_review(
    _message: str, _response_type: Any, _params: Any, _context: Any
) -> ElicitResult:
    """Elicitation handler that declines the elicitation outright."""
    return ElicitResult(action="decline")


def _verified_meta(path: Path) -> Any:
    """Return the ``verified`` frontmatter list for *path* (or None)."""
    return fm.loads(path.read_text(encoding="utf-8")).metadata.get("verified")


@pytest.mark.usefixtures("enforced_env")
class TestWriteToolThreadsActor:
    async def test_write_stamps_tool_actor_and_clears_verified(
        self, enforced_env: Path
    ) -> None:
        # No auth configured → get_subject() is "local" → tool actor, not human.
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(
                "write", {"path": "note.md", "content": _VERIFIED_NOTE}
            )
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads((enforced_env / "note.md").read_text(encoding="utf-8")).metadata
        assert "verified" not in meta
        assert meta["generated"]["by"].startswith("markdown-vault-mcp/")

    async def test_write_stamps_human_actor_when_authed(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(
                "write", {"path": "note.md", "content": "# N\n\nBody.\n"}
            )
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads((enforced_env / "note.md").read_text(encoding="utf-8")).metadata
        assert meta["generated"]["by"] == "human:peter"

    async def test_convert_links_migration_does_not_restamp(
        self, enforced_env: Path
    ) -> None:
        # A migration transform is wrapped in okf_write_suppressed: converting a
        # wikilink must not clear a note's human verification nor stamp it.
        (enforced_env / "guides" / "plain.md").write_text(
            "---\nverified:\n  - by: human:peter\n    at: 2026-01-01\n---\n"
            + _PLAIN_NOTE,
            encoding="utf-8",
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool("okf_convert_links", {})
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads(
            (enforced_env / "guides" / "plain.md").read_text(encoding="utf-8")
        ).metadata
        assert meta["verified"] == [{"by": "human:peter", "at": _dt.date(2026, 1, 1)}]
        assert "generated" not in meta


@pytest.mark.usefixtures("enforced_env")
class TestFetchThreadsActor:
    async def test_fetch_stamps_human_actor_when_authed(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from fastmcp_pvl_core import FetchResult

        # fetch writes .md notes through DocumentManager.write, so the enricher
        # fires; the provenance actor must be the authenticated caller (#964).
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        # The download is incidental here — only the actor threading on the
        # write path is under test — so fetch_url (a black-box library
        # boundary, #1028) is stubbed with a canned result.
        monkeypatch.setattr(
            "markdown_vault_mcp._server_tools.writer.fetch_url",
            AsyncMock(
                return_value=FetchResult(
                    body=b"# Fetched\n\nBody.\n",
                    content_type="text/plain",
                    final_url="https://example.com/report.md",
                )
            ),
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(
                "fetch",
                {"url": "https://example.com/report.md", "path": "notes/report.md"},
            )
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads(
            (enforced_env / "notes" / "report.md").read_text(encoding="utf-8")
        ).metadata
        assert meta["generated"]["by"] == "human:peter"


@pytest.mark.usefixtures("trust_auth_env")
class TestOkfVerifyTrustAuth:
    """okf_verify under ``OKF_VERIFY=trust-auth`` — attribute to the auth subject."""

    async def test_refuses_without_auth(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="authenticated identity"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})

    async def test_appends_verification_when_authed(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == 1
        # The append survives — okf_verify suppresses the enricher so its own
        # write does not clear the verification it just added.
        meta = fm.loads(
            (trust_auth_env / "guides" / "playbook.md").read_text(encoding="utf-8")
        ).metadata
        assert meta["verified"] == [
            {"by": "human:peter", "at": _dt.date.today().isoformat()}
        ]

    async def test_missing_note_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="not found"):
                await client.call_tool("okf_verify", {"path": "nope.md"})

    async def test_aborts_on_concurrent_modification(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # okf_verify reads then writes with if_match=note.etag. Simulate a
        # concurrent write landing in that window by mutating the file from
        # inside the transform (which runs after the read); the etag no longer
        # matches, so the write is refused rather than clobbering the change.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        import markdown_vault_mcp._server_tools.writer as writer_mod

        real_append = writer_mod.append_okf_verification

        def racing_append(text: str, *, subject: str, today: Any) -> str:
            (trust_auth_env / "guides" / "playbook.md").write_text(
                "---\ntitle: Raced\n---\n# Raced\n", encoding="utf-8"
            )
            return real_append(text, subject=subject, today=today)

        monkeypatch.setattr(writer_mod, "append_okf_verification", racing_append)
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="changed since it was read"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})


@pytest.mark.usefixtures("enforced_env")
class TestOkfVerifyElicit:
    """okf_verify under the default ``OKF_VERIFY=elicit`` — human elicitation gate."""

    async def test_writes_on_affirmative_elicitation(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An affirmative elicitation reply attributes to the authenticated
        # subject and records the verification.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server(), elicitation_handler=_accept_review) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == 1
        assert _verified_meta(enforced_env / "guides" / "playbook.md") == [
            {"by": "human:peter", "at": _dt.date.today().isoformat()}
        ]

    async def test_no_auth_stamps_local_after_confirmation(self) -> None:
        # With no auth the elicitation — not the token — proves a human is
        # present, so the entry is attributed to the local sentinel.
        async with Client(make_server(), elicitation_handler=_accept_review) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        assert _parse_tool_data(result)["verifier"] == "human:local"

    async def test_declined_elicitation_writes_nothing(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server(), elicitation_handler=_decline_review) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(enforced_env / "guides" / "playbook.md") is None

    async def test_negative_reply_writes_nothing(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Accepting the form but answering 'no' is not an affirmation.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(
            make_server(), elicitation_handler=_accept_negative
        ) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(enforced_env / "guides" / "playbook.md") is None

    async def test_unsupported_client_fails_closed(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No elicitation_handler → the client cannot elicit → fail closed.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="does not support elicitation"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(enforced_env / "guides" / "playbook.md") is None


@pytest.mark.usefixtures("enforced_env")
class TestOkfVerifyOff:
    """okf_verify under ``OKF_VERIFY=off`` — the tool is hidden entirely."""

    async def test_tool_absent_from_listing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "off")
        async with Client(make_server()) as client:
            tools = await client.list_tools()
        assert "okf_verify" not in [t.name for t in tools]

    async def test_present_by_default(self) -> None:
        # Sanity check the complement: with the default mode the tool is listed.
        async with Client(make_server()) as client:
            tools = await client.list_tools()
        assert "okf_verify" in [t.name for t in tools]
