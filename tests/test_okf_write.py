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
import re
from typing import TYPE_CHECKING, Any

import frontmatter as fm
import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from markdown_vault_mcp import _okf_write
from markdown_vault_mcp._okf_write import (
    OkfWriteIntent,
    build_okf_write_enrich,
    okf_write_intent,
    okf_write_suppressed,
    resolve_human_subject,
    tool_actor,
)
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import (
    OkfDetector,
    append_okf_verification,
    apply_okf_write_stamp,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import (
    _CLEAR_VARS,
    _parse_tool_data,
    wait_for_mcp_writer_drain,
    wait_for_writer_drain,
)
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

_NOW = _dt.datetime(2026, 8, 9, 14, 30, 5, tzinfo=_dt.UTC)
#: The stamp the spec's examples show: UTC, ``Z``, seconds (#1372).
_ISO = "2026-08-09T14:30:05Z"
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _assert_recent_utc_stamp(value: object) -> None:
    """A live stamp is a UTC instant in the spec's form, written just now."""
    assert isinstance(value, str) and _STAMP_RE.match(value), value
    written = _dt.datetime.fromisoformat(value)
    assert abs(_dt.datetime.now(_dt.UTC) - written) < _dt.timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Pure transforms: apply_okf_write_stamp
# ---------------------------------------------------------------------------


class TestApplyOkfWriteStamp:
    @pytest.mark.parametrize(
        "slot", [None, "", "metadata: null\n", "metadata: wrong\n", "metadata: []\n"]
    )
    def test_stamps_generated_on_a_note_without_frontmatter(
        self, slot: str | None
    ) -> None:
        text = (
            "# Note\n\nBody.\n"
            if slot is None
            else "---\nname: demo\ntype: Literal\n" + slot + "---\nBody.\n"
        )
        out = apply_okf_write_stamp(
            text, actor="a/1", now=_NOW, path=None if slot is None else "SKILL.md"
        )
        post = fm.loads(out)
        authority = post.metadata if slot is None else post.metadata["metadata"]
        assert authority["generated"] == {"by": "a/1", "at": _ISO}
        assert "Body." in post.content
        if slot is not None:
            assert post.metadata["name"] == "demo"
            assert post.metadata["type"] == "Literal"
            assert set(authority) == {"generated"}

    @pytest.mark.parametrize("path", [None, "ordinary.md", "skill.md", "SKILL.md"])
    def test_clears_verified_on_content_change(self, path: str | None) -> None:
        text = (
            "---\nverified: [{by: 'human:peter', at: 2026-01-01}]\ntitle: T\ntype: Literal\n"
            "metadata:\n  custom: keep\n  generated: {by: 'human:original', custom: retained}\n"
            "  verified: {by: 'human:previous'}\n---\n# T\n"
        )
        meta = fm.loads(
            apply_okf_write_stamp(text, actor="human:x", now=_NOW, path=path)
        ).metadata
        slot = meta["metadata"] if path == "SKILL.md" else meta
        assert "verified" not in slot
        assert slot["generated"] == {"by": "human:x", "at": _ISO}
        assert meta["title"] == "T" and meta["type"] == "Literal"
        assert meta["metadata"]["custom"] == "keep"
        if path == "SKILL.md":
            assert meta["verified"] == [
                {"by": "human:peter", "at": _dt.date(2026, 1, 1)}
            ]

    def test_preserves_sources_and_other_fields(self) -> None:
        text = (
            "---\n"
            "type: Playbook\n"
            "sources:\n"
            "  - resource: https://example.com/a\n"
            "    id: a\n"
            "x_custom: 1\n"
            "---\n"
            "# P\n"
        )
        meta = fm.loads(apply_okf_write_stamp(text, actor="t/1", now=_NOW)).metadata
        assert meta["type"] == "Playbook"
        assert meta["sources"] == [{"resource": "https://example.com/a", "id": "a"}]
        # A producer-defined key survives the round trip (OKF v0.2 §4.1).
        assert meta["x_custom"] == 1

    def test_same_actor_same_instant_re_stamp_is_idempotent(self) -> None:
        # Once a note carries this exact actor/instant stamp and no verified,
        # re-stamping is a byte-for-byte no-op — its YAML formatting is frozen.
        once = apply_okf_write_stamp("# T\n\nBody.\n", actor="t/1", now=_NOW)
        assert apply_okf_write_stamp(once, actor="t/1", now=_NOW) == once

    def test_a_local_offset_instant_is_written_as_utc(self) -> None:
        # 16:30:05 at +02:00 is 14:30:05Z; the stamp carries one spelling
        # for every deployment (OKF v0.2 §5 as amended 2026-08-21; #1372).
        local = _dt.datetime(
            2026, 8, 9, 16, 30, 5, tzinfo=_dt.timezone(_dt.timedelta(hours=2))
        )
        out = apply_okf_write_stamp("# Note\n", actor="a/1", now=local)
        assert fm.loads(out).metadata["generated"]["at"] == _ISO

    def test_a_naive_instant_is_refused(self) -> None:
        # A naive value would be read as the host's local time and silently
        # become an offset the caller never stated — the class of ambiguity
        # the amendment exists to remove. Callers pass an aware instant.
        with pytest.raises(ValueError, match="aware"):
            apply_okf_write_stamp(
                "# Note\n", actor="a/1", now=_dt.datetime(2026, 8, 9, 14, 30, 5)
            )

    def test_the_stamp_is_a_quoted_string_not_a_yaml_timestamp(self) -> None:
        # A datetime value would be re-dumped as ``2026-08-09 14:30:05+00:00``,
        # the corruption the amendment names; a string stays as written.
        out = apply_okf_write_stamp("# Note\n", actor="a/1", now=_NOW)
        assert "at: '2026-08-09T14:30:05Z'" in out
        assert isinstance(fm.loads(out).metadata["generated"]["at"], str)

    def test_human_actor_is_recorded_verbatim(self) -> None:
        out = apply_okf_write_stamp("body\n", actor="human:peter", now=_NOW)
        assert fm.loads(out).metadata["generated"]["by"] == "human:peter"


# ---------------------------------------------------------------------------
# Pure transforms: append_okf_verification
# ---------------------------------------------------------------------------


class TestAppendOkfVerification:
    def test_creates_verified_list_when_absent(self) -> None:
        out = append_okf_verification("# N\n", subject="peter", now=_NOW)
        meta = fm.loads(out).metadata
        assert meta["verified"] == [{"by": "human:peter", "at": _ISO}]

    def test_appends_to_existing_list(self) -> None:
        text = "---\nverified:\n  - by: human:alice\n    at: 2026-01-01\n---\n# N\n"
        meta = fm.loads(
            append_okf_verification(text, subject="peter", now=_NOW)
        ).metadata
        # The pre-existing unquoted YAML date round-trips as a date object; the
        # appended entry carries the ISO string we wrote.
        assert meta["verified"] == [
            {"by": "human:alice", "at": _dt.date(2026, 1, 1)},
            {"by": "human:peter", "at": _ISO},
        ]

    def test_preserves_body_and_other_fields(self) -> None:
        text = "---\ntitle: T\n---\n# T\n\nBody line.\n"
        out = append_okf_verification(text, subject="peter", now=_NOW)
        post = fm.loads(out)
        assert post.metadata["title"] == "T"
        assert "Body line." in post.content

    @pytest.mark.parametrize("path", [None, "ordinary.md", "skill.md", "SKILL.md"])
    def test_a_bare_mapping_is_kept_as_the_first_entry(self, path: str | None) -> None:
        """The selected shorthand entry survives, with the new attestation appended."""
        text = (
            "---\ntype: Literal\nverified: {by: 'human:alice', at: 2026-01-01}\n"
            "metadata:\n  custom: keep\n  generated: {by: 'human:original', custom: retained}\n"
            "  verified: {by: 'human:previous'}\n---\n# N\n"
        )
        meta = fm.loads(
            append_okf_verification(text, subject="peter", now=_NOW, path=path)
        ).metadata
        nested = path == "SKILL.md"
        entries = (meta["metadata"] if nested else meta)["verified"]
        assert entries == [
            {"by": "human:previous"}
            if nested
            else {"by": "human:alice", "at": _dt.date(2026, 1, 1)},
            {"by": "human:peter", "at": _ISO},
        ]
        assert meta["type"] == "Literal" and meta["metadata"]["custom"] == "keep"
        if nested:
            assert meta["verified"] == {"by": "human:alice", "at": _dt.date(2026, 1, 1)}

    def test_scalar_verified_is_replaced(self) -> None:
        # A malformed scalar verified is treated as empty, not crashed on.
        text = "---\nverified: nonsense\n---\n# N\n"
        meta = fm.loads(
            append_okf_verification(text, subject="peter", now=_NOW)
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
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
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
    col = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
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
        _assert_recent_utc_stamp(meta["generated"]["at"])

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


class TestUnparseableFrontmatterRefusal:
    """#1454: the refusal is right; it has to say so in the caller's terms."""

    MALFORMED_JSON = "{\n  not json,\n}\n# body\n"

    def test_write_is_refused_with_the_path_and_the_reason(
        self, enforced_vault: Vault
    ) -> None:
        # The stamp cannot be applied to a block that does not parse, so the
        # write is refused — but it used to surface the parser's own exception,
        # naming no path and reading like a server fault.
        # The frontmatter is the caller's own content, so the caller must
        # change the request (#1608).
        with pytest.raises(InvalidRequestError, match=r"bad\.md") as excinfo:
            enforced_vault.writer.write("bad.md", self.MALFORMED_JSON)
        assert "frontmatter" in str(excinfo.value)

    def test_the_refused_write_leaves_no_file(self, enforced_vault: Vault) -> None:
        with pytest.raises(ValueError):
            enforced_vault.writer.write("bad.md", self.MALFORMED_JSON)
        assert not (enforced_vault.source_dir / "bad.md").exists()

    def test_malformed_yaml_is_refused_the_same_way(
        self, enforced_vault: Vault
    ) -> None:
        # The dialect is not what the refusal is about (#1408).
        with pytest.raises(InvalidRequestError, match=r"bad\.md"):
            enforced_vault.writer.write("bad.md", "---\ntitle: [unclosed\n---\n# b\n")

    def test_editing_an_already_malformed_note_names_the_operation(
        self, enforced_vault: Vault
    ) -> None:
        # `edit` matches on raw text, so it reaches the stamp with a block that
        # never parsed — the refusal has to read for that operation too.
        (enforced_vault.source_dir / "bad.md").write_text(
            self.MALFORMED_JSON, encoding="utf-8"
        )
        # The stored note's block is at fault here, not the caller's edit, so
        # this stays a plain ValueError: a server fault by the design (#1608).
        with pytest.raises(ValueError, match=r"Cannot edit bad\.md") as excinfo:
            enforced_vault.writer.edit("bad.md", old_text="# body", new_text="# other")
        assert not isinstance(excinfo.value, InvalidRequestError)

    def test_a_parseable_note_still_stamps(self, enforced_vault: Vault) -> None:
        enforced_vault.writer.write("ok.md", '{\n"title": "T"\n}\n# T\n')
        wait_for_writer_drain(enforced_vault)
        assert _meta(enforced_vault, "ok.md")["generated"]["by"].startswith(
            "markdown-vault-mcp/"
        )


class TestOffModeIdentity:
    def test_write_is_byte_identical_when_okf_write_off(self, tmp_path: Path) -> None:
        root = tmp_path / "vault"
        root.mkdir()
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = Vault(
            source_dir=root,
            settings=VaultSettings(read_only=False, okf_mode="on", okf_write=False),
        )
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
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
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
        # A migration transform suppresses the enricher on every write it
        # issues (#1401), so converting a wikilink must not clear a note's
        # human verification nor stamp it — through this tool or the facade.
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
class TestUnparseableFrontmatterRefusalTool:
    """The refusal as an MCP client reads it (#1454)."""

    async def test_unparseable_frontmatter_is_refused_in_the_client_s_terms(
        self, enforced_env: Path
    ) -> None:
        """The client is told what it did wrong, not what the parser hit.

        The enricher's own exception reached the client as
        ``Expecting property name enclosed in double quotes`` — no path, no
        cause, and logged server-side as an unexpected tool failure (#1454).
        """
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool(
                    "write", {"path": "bad.md", "content": "{\n  not json,\n}\n# b\n"}
                )
        message = str(excinfo.value)
        assert "bad.md" in message
        assert "frontmatter" in message
        assert not (enforced_env / "bad.md").exists()


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
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        # The download is incidental here — only the actor threading on the
        # write path is under test — so fetch_url (a black-box library
        # boundary, #1028) is stubbed with a canned result.
        monkeypatch.setattr(
            "markdown_vault_mcp._tools.writer.fetch_url",
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
            with pytest.raises(ToolError, match="no human identity"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})

    async def test_appends_verification_when_authed(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
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
        (entry,) = meta["verified"]
        assert entry["by"] == "human:peter"
        _assert_recent_utc_stamp(entry["at"])

    async def test_missing_note_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="Document not found"):
                await client.call_tool("okf_verify", {"path": "nope.md"})

    async def test_aborts_on_concurrent_modification(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # okf_verify reads then writes with if_match=note.etag. Simulate a
        # concurrent write landing in that window by mutating the file from
        # inside the transform (which runs after the read); the etag no longer
        # matches, so the write is refused rather than clobbering the change.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        import markdown_vault_mcp._tools.writer as writer_mod

        real_append = writer_mod.append_okf_verification

        def racing_append(
            text: str, *, subject: str, now: Any, path: str | None = None
        ) -> str:
            (trust_auth_env / "guides" / "playbook.md").write_text(
                "---\ntitle: Raced\n---\n# Raced\n", encoding="utf-8"
            )
            return real_append(text, subject=subject, now=now, path=path)

        monkeypatch.setattr(writer_mod, "append_okf_verification", racing_append)
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="changed while it was being verified"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})

    @pytest.mark.parametrize("conflict", [False, True])
    async def test_verify_alias_retarget_stays_bound_to_read_target(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch, conflict: bool
    ) -> None:
        """Same-byte alias retarget must not redirect the attestation after its read."""
        from markdown_vault_mcp.facets.reader import ReaderFacet
        from markdown_vault_mcp.facets.writer import WriterFacet

        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        original = trust_auth_env / "SKILL.md"
        other = trust_auth_env / "ordinary.md"
        before = fm.dumps(
            fm.Post("# Same zebra", type="Literal", metadata={"type": "Runbook"})
        )
        original.write_text(before)
        other.write_text(before)
        alias = trust_auth_env / "alias.md"
        alias.symlink_to(original)
        read = ReaderFacet.read
        write = WriterFacet.write
        observed = {}

        def read_and_retarget(reader, path: str, **kwargs: Any):
            note = read(reader, path, **kwargs)
            if path == "alias.md":
                observed["note"] = note
                alias.unlink()
                alias.symlink_to(other)
                if conflict:
                    original.write_text(before + "\nconcurrent change\n")
            return note

        def conditional_write(writer, path: str, content: str, **kwargs: Any):
            observed["path"], observed["etag"] = path, kwargs.get("if_match")
            return write(writer, path, content, **kwargs)

        monkeypatch.setattr(ReaderFacet, "read", read_and_retarget)
        monkeypatch.setattr(WriterFacet, "write", conditional_write)
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            if conflict:
                with pytest.raises(ToolError, match=r"alias\.md.*changed while"):
                    await client.call_tool("okf_verify", {"path": "alias.md"})
            else:
                result = _parse_tool_data(
                    await client.call_tool("okf_verify", {"path": "alias.md"})
                )
                assert result["path"] == "alias.md" and result["verified_count"] == 1
            await wait_for_mcp_writer_drain(client)
        note = observed["note"]
        assert observed["path"] == note.path == "SKILL.md"
        assert observed["etag"] == note.etag
        assert other.read_text() == before
        if conflict:
            assert original.read_text() == before + "\nconcurrent change\n"
        else:
            assert (
                fm.loads(original.read_text())["metadata"]["verified"][0]["by"]
                == "human:peter"
            )

    @pytest.mark.parametrize(
        "case",
        [
            ("guides/playbook.md", "guides/playbook.md", False),
            ("SKILL.md", "SKILL.md", True),
            ("target/SKILL.md", "alias.md", True),
            ("target/ordinary.md", "aliases/SKILL.md", False),
        ],
    )
    @pytest.mark.parametrize("has_prior", [True, False])
    async def test_counts_a_bare_mapping_as_one(
        self,
        trust_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        case: tuple[str, str, bool],
        has_prior: bool,
    ) -> None:
        """Verification counts and preserves entries in the resolved authority slot."""
        target, request_path, nested = case
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        prior = {"by": "human:alice", "at": _dt.date(2026, 1, 1)}
        literal = {"verified": ["literal", "conflict"], "custom": "keep"}
        raw: dict[str, Any] = {"type": "Literal", "metadata": dict(literal)}
        if nested:
            raw.update(literal)
            raw["metadata"] = {"type": "Runbook", "custom": "keep"}
        slot = raw["metadata"] if nested else raw
        if has_prior:
            slot["verified"] = prior
        destination = trust_auth_env / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(fm.dumps(fm.Post("# P\n", **raw)))
        if request_path != target:
            alias = trust_auth_env / request_path
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to(destination)
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("okf_verify", {"path": request_path})
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["path"] == request_path
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == (2 if has_prior else 1)
        persisted = fm.loads(destination.read_text()).metadata
        entries = (persisted["metadata"] if nested else persisted)["verified"]
        assert entries[:-1] == ([prior] if has_prior else [])
        assert entries[-1]["by"] == "human:peter"
        _assert_recent_utc_stamp(entries[-1]["at"])
        if nested:
            assert persisted["verified"] == literal["verified"]
            assert persisted["type"] == "Literal"
        else:
            assert persisted["metadata"] == literal


@pytest.mark.usefixtures("enforced_env")
class TestOkfVerifyElicit:
    """okf_verify under the default ``OKF_VERIFY=elicit`` — human elicitation gate."""

    async def test_legacy_connection_uses_server_initiated_elicitation(self) -> None:
        """Handshake-era clients retain server-initiated confirmation."""
        async with Client(
            make_server(), mode="legacy", elicitation_handler=_accept_review
        ) as client:
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        assert _parse_tool_data(result)["verifier"] == "human:local"

    async def test_writes_on_affirmative_elicitation(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An affirmative elicitation reply attributes to the authenticated
        # subject and records the verification.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        async with Client(make_server(), elicitation_handler=_accept_review) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == 1
        (entry,) = _verified_meta(enforced_env / "guides" / "playbook.md")
        assert entry["by"] == "human:peter"
        _assert_recent_utc_stamp(entry["at"])

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
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
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
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
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
        # On the modern protocol the client drives the returned input request;
        # with no handler it refuses before issuing a continuation call.
        monkeypatch.setattr("fastmcp_pvl_core.get_subject", lambda: "peter")
        monkeypatch.setattr("fastmcp_pvl_core.get_claims", lambda: {"sub": "peter"})
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(MCPError, match="Elicitation not supported"):
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


@pytest.fixture(params=["single", "mapped"])
async def service_token(
    request: pytest.FixtureRequest, enforced_env: Path
) -> AsyncIterator[None]:
    """Bind a real static verifier result, including mapped subject labels."""
    from fastmcp_pvl_core import ServerConfig, build_bearer_auth
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    tokens_file = enforced_env.parent / "tokens.toml"
    tokens_file.write_text(
        '[tokens]\n"test-only-1463" = "user:peter"\n', encoding="utf-8"
    )
    verifier = build_bearer_auth(
        ServerConfig(
            bearer_token="test-only-1463",
            bearer_tokens_file=tokens_file if request.param == "mapped" else None,
        )
    )
    assert verifier is not None
    token = await verifier.verify_token("test-only-1463")
    assert token is not None
    marker = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(marker)


@pytest.mark.usefixtures("service_token")
class TestServiceTokenOkf:
    async def test_write_uses_tool_provenance(self, enforced_env: Path) -> None:
        async with Client(make_server()) as client:
            await client.call_tool(
                "write", {"path": "service.md", "content": "# Service\n"}
            )
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads(
            (enforced_env / "service.md").read_text(encoding="utf-8")
        ).metadata
        assert meta["generated"]["by"] == tool_actor(_okf_write.package_version())

    async def test_trust_auth_refuses_without_changing_note(
        self, enforced_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
        path = enforced_env / "guides" / "playbook.md"
        before = path.read_bytes()
        async with Client(make_server()) as client:
            with pytest.raises(ToolError, match="no human identity"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert path.read_bytes() == before

    @pytest.mark.parametrize("legacy", [False, True])
    async def test_elicit_attributes_confirmed_review_to_local_human(
        self, enforced_env: Path, legacy: bool
    ) -> None:
        async with Client(
            make_server(),
            mode="legacy" if legacy else "auto",
            elicitation_handler=_accept_review,
        ) as client:
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        assert _parse_tool_data(result)["verifier"] == "human:local"
        (entry,) = _verified_meta(enforced_env / "guides" / "playbook.md")
        assert entry["by"] == "human:local"

    async def test_elicit_decline_does_not_verify(self, enforced_env: Path) -> None:
        async with Client(make_server(), elicitation_handler=_decline_review) as client:
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(enforced_env / "guides" / "playbook.md") is None


@pytest.mark.parametrize(
    "operation", ["body", "frontmatter", "append", "overwrite", "create"]
)
@pytest.mark.parametrize(
    ("target", "request_path"),
    [
        ("SKILL.md", "SKILL.md"),
        ("target/SKILL.md", "alias.md"),
        ("target/ordinary.md", "aliases/SKILL.md"),
    ],
)
def test_skill_content_edit_invalidates_nested_verification(
    tmp_path: Path, operation: str, target: str, request_path: str
) -> None:
    """Native mutations forward the resolved target to the two-argument enricher."""
    from tests.fixtures.okf_skills import SKILL

    destination = tmp_path / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(SKILL)
    if request_path != target:
        alias = tmp_path / request_path
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias.symlink_to(destination)
    (tmp_path / "keep.md").write_text("# Keep vault mounted\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        observed: list[tuple[str, str | None]] = []

        def enrich(text: str, op: Any) -> str:
            intent = _okf_write.current_okf_intent()
            observed.append((op, intent.path if intent else None))
            return text + "\nlegacy " + op + "\n"

        vault._doc_mgr._okf_write_enrich = enrich
        if operation == "body":
            vault.writer.edit(
                request_path,
                old_text="Run the zebra checks.",
                new_text="Run updated zebra checks.",
            )
        elif operation == "frontmatter":
            vault.writer.edit(
                request_path, old_text="name: demo", new_text="name: updated"
            )
        elif operation == "append":
            vault.writer.append(request_path, "\nupdated zebra\n")
        else:
            if operation == "create":
                destination.unlink()
            result = vault.writer.write(
                request_path, SKILL.replace("zebra", "updated zebra")
            )
            assert result.created == (operation == "create")
        assert observed == [
            ("write" if operation in {"overwrite", "create"} else "edit", target)
        ]
        assert "updated" in destination.read_text()
        assert "legacy " + observed[0][0] in destination.read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("target", "request_path", "nested"),
    [
        ("target/SKILL.md", "alias.md", True),
        ("target/ordinary.md", "aliases/SKILL.md", False),
    ],
)
def test_alias_content_edit_stamps_resolved_slot(
    tmp_path: Path, target: str, request_path: str, nested: bool
) -> None:
    """Real enrichment invalidates only the resolved note's authoritative slot."""
    from tests.fixtures.okf_skills import SKILL

    destination = tmp_path / target
    destination.parent.mkdir(parents=True)
    before = fm.loads(SKILL).metadata
    before.update(type="Literal", verified=[{"by": "human:literal"}])
    destination.write_text(fm.dumps(fm.Post("Run the zebra checks.", **before)))
    alias = tmp_path / request_path
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(destination)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        assert vault.reader.read(request_path).path == target
        vault.writer.edit(
            request_path, old_text="Run the zebra checks.", new_text="Changed zebra."
        )
        after = fm.loads(destination.read_text()).metadata
        slot = after["metadata"] if nested else after
        assert "Changed zebra." in destination.read_text()
        assert "verified" not in slot
        assert slot["generated"]["by"].startswith("markdown-vault-mcp/")
        _assert_recent_utc_stamp(slot["generated"]["at"])
        if nested:
            assert after["verified"] == before["verified"]
            assert after["type"] == before["type"]
        else:
            assert after["metadata"] == before["metadata"]
    finally:
        vault.close()


@pytest.mark.parametrize("actor", [None, "", "human:fixture"])
def test_path_scope_selects_nested_slot_and_preserves_actor(
    tmp_path: Path, actor: str | None
) -> None:
    """A path-only scope uses the configured actor; explicit empty actors stay explicit."""
    enrich = build_okf_write_enrich(
        okf_write=True, detector=_active_detector(tmp_path), version="fixture"
    )
    assert enrich is not None
    with (
        okf_write_intent(OkfWriteIntent(actor=actor)),
        _okf_write.okf_write_path("nested/SKILL.md"),
    ):
        meta = fm.loads(enrich("# Demo\n", "write")).metadata
    assert meta["metadata"]["generated"]["by"] == (
        "markdown-vault-mcp/fixture" if actor is None else actor
    )
    assert _okf_write.current_okf_intent() is None


def test_path_scope_restores_outer_intent_after_error(tmp_path: Path) -> None:
    """Nested native target scopes retain suppression and restore on exceptions."""
    enrich = build_okf_write_enrich(
        okf_write=True, detector=_active_detector(tmp_path), version="fixture"
    )
    assert enrich is not None
    with _okf_write.okf_write_path("outer/SKILL.md"):
        outer = _okf_write.current_okf_intent()
        assert outer is not None and outer.actor is None
        with okf_write_intent(OkfWriteIntent("", True)):
            suppressed = _okf_write.current_okf_intent()
            with (
                pytest.raises(RuntimeError, match="fixture"),
                _okf_write.okf_write_path("ordinary.md"),
            ):
                current = _okf_write.current_okf_intent()
                assert current is not None and current.path == "ordinary.md"
                assert enrich("# Demo\n", "write") == "# Demo\n"
                raise RuntimeError("fixture")
            assert _okf_write.current_okf_intent() is suppressed
        assert _okf_write.current_okf_intent() is outer
    assert _okf_write.current_okf_intent() is None


@pytest.mark.parametrize("write_alias", [False, True])
def test_native_write_refreshes_all_indexed_spellings(
    tmp_path: Path, alias_note, write_alias: bool
) -> None:
    """A real enriched write refreshes known live and tombstone names, never new aliases."""
    import json

    from markdown_vault_mcp.hashing import compute_etag
    from markdown_vault_mcp.types import SkippedFile

    target, alias, _, expected = alias_note
    (tmp_path / "restored.md").symlink_to(tmp_path / target)
    (tmp_path / "unrelated.md").write_text("# Unrelated")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        vault._fts.upsert_tombstone(
            SkippedFile(path="restored.md", category="parse_error", detail="fixture"),
            content_hash="old",
            modified_at=0,
        )
        (tmp_path / "unknown.md").symlink_to(tmp_path / target)
        untouched = vault._fts.get_note("unrelated.md")
        vault.writer.append(alias if write_alias else target, "\nupdated zebra\n")
        wait_for_writer_drain(vault)
        content = (tmp_path / target).read_bytes()
        raw = fm.loads(content.decode()).metadata
        slot = raw["metadata"] if expected["type"] == "Runbook" else raw
        assert "verified" not in slot and "generated" in slot
        for path in (target, alias, "restored.md"):
            row = vault._fts.get_note(path)
            assert row["content_hash"] == compute_etag(content)
            assert json.loads(row["frontmatter_json"]) == raw
        assert {
            h.path
            for h in vault.reader.search(
                "updated", mode="keyword", filters={"type": expected["type"]}
            )
        } == {target, alias, "restored.md"}
        assert vault.reader.okf_stats()["types"][expected["type"]] == 3
        assert vault._fts.get_note("unknown.md") is None
        assert vault._fts.get_note("unrelated.md") == untouched
        from markdown_vault_mcp.query_types import NoteQuery

        assert {
            n["path"]
            for n in vault.reader.query_documents(
                NoteQuery(filters={"type": "Literal"})
            ).items
        } == {target, alias, "restored.md"}
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["etag", "atomic", "malformed"])
def test_alias_refused_write_preserves_authoritative_bytes(
    tmp_path: Path, alias_note, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Native stamp computation never persists when validation or replacement fails."""
    from markdown_vault_mcp.exceptions import ConcurrentModificationError

    target, alias, _, _ = alias_note
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        vault.index.build_index()
        before = (tmp_path / target).read_bytes()
        if failure == "atomic":

            def failed(*_args: Any, **_kwargs: Any) -> None:
                raise OSError("synthetic atomic replacement refusal")

            monkeypatch.setattr(
                "markdown_vault_mcp.managers.document.atomic_write", failed
            )
            with pytest.raises(OSError, match="atomic replacement"):
                vault.writer.append(alias, "changed")
        elif failure == "etag":
            with pytest.raises(ConcurrentModificationError):
                vault.writer.write(alias, "# Replacement", if_match="stale-etag")
        else:
            with pytest.raises(InvalidRequestError):
                vault.writer.write(alias, "---\n[bad\n---\n# Replacement")
        assert (tmp_path / target).read_bytes() == before
        assert vault.index.wait_for_drain(timeout=5)
    finally:
        vault.close()
