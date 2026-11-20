"""Provenance preservation and explicit OKF review behavior."""

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
    okf_write_intent,
    okf_write_suppressed,
    resolve_human_subject,
)
from markdown_vault_mcp.okf import (
    append_okf_verification,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import (
    _parse_tool_data,
    wait_for_mcp_writer_drain,
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
# Pure transforms: append_okf_verification
# ---------------------------------------------------------------------------


class TestAppendOkfVerification:
    @pytest.mark.parametrize("existing_verification", [False, True])
    def test_preserves_body_and_other_fields(self, existing_verification: bool) -> None:
        verification = (
            "verified: [{by: 'human:old', at: old}]\n" if existing_verification else ""
        )
        text = (
            "---\nzeta: {zeta: 1, alpha: 2}\n"
            + verification
            + "sources: [{resource: 'https://example.com', id: source}]\n"
            "title: T\nalpha: false\n---\n\n# T\n\nBody line."
        )
        before = fm.loads(text)
        post = fm.loads(append_okf_verification(text, subject="reviewer", now=_NOW))
        expected = (
            ["zeta", "verified", "sources", "title", "alpha"]
            if existing_verification
            else ["zeta", "sources", "title", "alpha", "verified"]
        )
        assert list(post.metadata) == expected
        assert post.metadata["verified"] == [
            *before.metadata.get("verified", []),
            {"by": "human:reviewer", "at": _ISO},
        ]
        for key in ("zeta", "sources", "title", "alpha"):
            assert post.metadata[key] == before.metadata[key]
        assert list(post.metadata["zeta"]) == ["zeta", "alpha"]
        assert list(post.metadata["sources"][0]) == ["resource", "id"]
        assert post.content == before.content

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

    def test_a_bare_mapping_is_kept_as_the_first_entry(self) -> None:
        # The spec's single-verifier shorthand is a one-element list; the
        # existing attestation must survive the append (#1357).
        text = "---\nverified:\n  by: human:alice\n  at: 2026-01-01\n---\n# N\n"
        meta = fm.loads(
            append_okf_verification(text, subject="peter", now=_NOW)
        ).metadata
        assert meta["verified"] == [
            {"by": "human:alice", "at": _dt.date(2026, 1, 1)},
            {"by": "human:peter", "at": _ISO},
        ]

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
    def test_resolve_human_subject_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        assert resolve_human_subject() == "peter"

    @pytest.mark.parametrize("value", [None, "local"])
    def test_resolve_human_subject_unattributable(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: value)
        assert resolve_human_subject() is None


# ---------------------------------------------------------------------------
# Maintenance suppression context
# ---------------------------------------------------------------------------


class TestIntentContextvar:
    def test_current_intent_defaults_none(self) -> None:
        assert _okf_write.current_okf_intent() is None

    def test_intent_reset_after_block(self) -> None:
        with okf_write_intent(OkfWriteIntent(suppress=True)):
            assert _okf_write.current_okf_intent() == OkfWriteIntent(suppress=True)
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


@pytest.fixture
def okf_alias_vault(tmp_path: Path) -> Iterator[Vault]:
    """A writable vault that declares OKF and sets the OKF_WRITE alias."""
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


class TestProvenancePreservation:
    @pytest.mark.parametrize("operation", ["write", "edit", "append", "frontmatter"])
    def test_supplied_claims_survive(
        self, okf_alias_vault: Vault, operation: str
    ) -> None:
        path = okf_alias_vault.source_dir / "note.md"
        path.write_text(_VERIFIED_NOTE, encoding="utf-8")
        okf_alias_vault.index.build_index()
        if operation == "write":
            okf_alias_vault.writer.write("new.md", _VERIFIED_NOTE)
            path = okf_alias_vault.source_dir / "new.md"
            assert path.read_bytes() == _VERIFIED_NOTE.encode()
        elif operation == "edit":
            okf_alias_vault.writer.edit(
                "note.md", old_text="Body.", new_text="Changed."
            )
        elif operation == "append":
            okf_alias_vault.writer.append("note.md", "Added")
        else:
            okf_alias_vault.writer.edit(
                "note.md", old_text="title: Playbook", new_text="title: Changed"
            )
        meta = fm.loads(path.read_text(encoding="utf-8")).metadata
        assert meta["verified"] == [{"by": "human:peter", "at": _dt.date(2026, 1, 1)}]
        assert meta["generated"] == {"by": "process:enrich"}

    def test_malformed_frontmatter_is_preserved(self, okf_alias_vault: Vault) -> None:
        content = "---\ntitle: [unclosed\n---\n# b\n"
        okf_alias_vault.writer.write("bad.md", content)
        okf_alias_vault.writer.edit("bad.md", old_text="# b", new_text="# c")
        assert (okf_alias_vault.source_dir / "bad.md").read_text() == content.replace(
            "# b", "# c"
        )


# ---------------------------------------------------------------------------
# End to end through the MCP server (authorship + okf_verify tool)
# ---------------------------------------------------------------------------

_PLAIN_NOTE = "# Plain\n\nSee [[playbook]].\n"


@pytest.fixture
def okf_alias_env(
    _clear_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Env for a writable, OKF-declared vault with the OKF_WRITE alias set."""
    vault = tmp_path / "vault"
    (vault / "guides").mkdir(parents=True)
    (vault / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    (vault / "guides" / "playbook.md").write_text(
        "---\ntitle: Playbook\n---\n# Playbook\n", encoding="utf-8"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    return vault


@pytest.fixture
def trust_auth_env(okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Enforced-write vault with okf_verify pinned to the no-confirmation mode."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
    return okf_alias_env


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


@pytest.mark.usefixtures("okf_alias_env")
class TestWriteToolAuthorship:
    async def test_human_credentials_do_not_imply_authorship(
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(
                "write", {"path": "note.md", "content": "# N\n\nBody.\n"}
            )
            await wait_for_mcp_writer_drain(client)
        meta = fm.loads(
            (okf_alias_env / "note.md").read_text(encoding="utf-8")
        ).metadata
        assert "generated" not in meta


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
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == 1
        # The explicit verification survives its own write.
        meta = fm.loads(
            (trust_auth_env / "guides" / "playbook.md").read_text(encoding="utf-8")
        ).metadata
        (entry,) = meta["verified"]
        assert entry["by"] == "human:peter"
        _assert_recent_utc_stamp(entry["at"])

    @pytest.mark.usefixtures("trust_auth_env")
    @pytest.mark.parametrize("subject", ["user:alice", "urn:uuid:123", "team/alice"])
    async def test_written_subject_remains_recorded_on_read(
        self, monkeypatch: pytest.MonkeyPatch, subject: str
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: subject)
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": subject}
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("read", {"path": "guides/playbook.md"})
        annotation = _parse_tool_data(result)["okf"]
        assert annotation["trust_tier"] == "human-reviewed"
        assert annotation["review"]["state"] == "recorded"
        assert annotation["review"]["recorded_entries"] == 1
        assert annotation["review"]["malformed_entries"] == 0
        assert annotation["recorded"]["verified"][0]["by"] == f"human:{subject}"

    async def test_missing_note_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
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
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        import markdown_vault_mcp._tools.writer as writer_mod

        real_append = writer_mod.append_okf_verification

        def racing_append(text: str, *, subject: str, now: Any) -> str:
            (trust_auth_env / "guides" / "playbook.md").write_text(
                "---\ntitle: Raced\n---\n# Raced\n", encoding="utf-8"
            )
            return real_append(text, subject=subject, now=now)

        monkeypatch.setattr(writer_mod, "append_okf_verification", racing_append)
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="changed while it was being verified"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})

    async def test_counts_a_bare_mapping_as_one(
        self, trust_auth_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # verified_count is the list length after the append; a bare-mapping
        # verified is one entry, not zero (#1357).
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        (trust_auth_env / "guides" / "playbook.md").write_text(
            "---\nverified:\n  by: human:alice\n  at: 2026-01-01\n---\n# P\n",
            encoding="utf-8",
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        assert _parse_tool_data(result)["verified_count"] == 2


@pytest.mark.usefixtures("okf_alias_env")
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
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An affirmative elicitation reply attributes to the authenticated
        # subject and records the verification.
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(make_server(), elicitation_handler=_accept_review) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "okf_verify", {"path": "guides/playbook.md"}
            )
            await wait_for_mcp_writer_drain(client)
        payload = _parse_tool_data(result)
        assert payload["verifier"] == "human:peter"
        assert payload["verified_count"] == 1
        (entry,) = _verified_meta(okf_alias_env / "guides" / "playbook.md")
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
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(make_server(), elicitation_handler=_decline_review) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(okf_alias_env / "guides" / "playbook.md") is None

    async def test_negative_reply_writes_nothing(
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Accepting the form but answering 'no' is not an affirmation.
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(
            make_server(), elicitation_handler=_accept_negative
        ) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(okf_alias_env / "guides" / "playbook.md") is None

    async def test_unsupported_client_fails_closed(
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # On the modern protocol the client drives the returned input request;
        # with no handler it refuses before issuing a continuation call.
        monkeypatch.setattr("markdown_vault_mcp._subject.get_subject", lambda: "peter")
        monkeypatch.setattr(
            "markdown_vault_mcp._subject.get_claims", lambda: {"sub": "peter"}
        )
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            with pytest.raises(MCPError, match="Elicitation not supported"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(okf_alias_env / "guides" / "playbook.md") is None


@pytest.mark.usefixtures("okf_alias_env")
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
    request: pytest.FixtureRequest, okf_alias_env: Path
) -> AsyncIterator[None]:
    """Bind a real static verifier result, including mapped subject labels."""
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    from markdown_vault_mcp._auth import build_bearer_auth
    from markdown_vault_mcp.config_sections.server import ServerConfig

    tokens_file = okf_alias_env.parent / "tokens.toml"
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
    async def test_trust_auth_refuses_without_changing_note(
        self, okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "trust-auth")
        path = okf_alias_env / "guides" / "playbook.md"
        before = path.read_bytes()
        async with Client(make_server()) as client:
            with pytest.raises(ToolError, match="no human identity"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert path.read_bytes() == before

    @pytest.mark.parametrize("legacy", [False, True])
    async def test_elicit_attributes_confirmed_review_to_local_human(
        self, okf_alias_env: Path, legacy: bool
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
        (entry,) = _verified_meta(okf_alias_env / "guides" / "playbook.md")
        assert entry["by"] == "human:local"

    async def test_elicit_decline_does_not_verify(self, okf_alias_env: Path) -> None:
        async with Client(make_server(), elicitation_handler=_decline_review) as client:
            with pytest.raises(ToolError, match="not confirmed"):
                await client.call_tool("okf_verify", {"path": "guides/playbook.md"})
            await wait_for_mcp_writer_drain(client)
        assert _verified_meta(okf_alias_env / "guides" / "playbook.md") is None


def test_ordinary_write_preserves_supplied_provenance(okf_alias_vault: Vault) -> None:
    fields = {
        "z": "first",
        "generated": {"by": "agent/example", "at": "2026-01-01T00:00:00Z"},
        "verified": [{"by": "human:reviewer", "at": "2026-01-02T00:00:00Z"}],
        "a": "last",
    }
    okf_alias_vault.writer.write("provenance.md", "Body\n", frontmatter=fields)
    text = (okf_alias_vault._source_dir / "provenance.md").read_text()
    assert list(fm.loads(text).metadata) == list(fields)
    assert fm.loads(text).metadata == fields


async def test_protected_verify_precedes_elicitation(
    okf_alias_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = okf_alias_env / "_conventions.md"
    path.write_text("original")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROTECTED_PATHS", "_conventions.md")
    calls = 0

    async def accept(*_args: Any) -> bool:
        nonlocal calls
        calls += 1
        return True

    async with Client(
        make_server(), mode="legacy", elicitation_handler=accept
    ) as client:
        with pytest.raises(ToolError, match="PROTECTED_PATHS"):
            await client.call_tool("okf_verify", {"path": "_conventions.md"})
    assert calls == 0
    assert path.read_text() == "original"
