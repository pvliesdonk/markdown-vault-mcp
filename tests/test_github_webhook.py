"""Tests for the GitHub webhook endpoint (issue #530).

Failure modes covered:
- Invalid / missing / malformed HMAC signature → 401
- ping event → 200, no pull
- Non-push events → 200 no-op
- push + HEAD advances → force_pull + reindex
- push + already up-to-date → no reindex
- push + force_pull applied=False → 503 retry (GitHub retries transient failures)
- push + vault not queryable → 200, pull runs, reindex skipped
- push + vault singleton not initialized → 503 retry
- push + reindex raises → 200 (reindex failure is logged, not surfaced to GitHub)
- push + no git strategy → 200 graceful no-op
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp._github_webhook import _verify_signature, make_webhook_handler
from markdown_vault_mcp.git import PullResult
from markdown_vault_mcp.vault import Vault

SECRET = "test-webhook-secret-xyz"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_client(secret: str = SECRET) -> TestClient:
    handler = make_webhook_handler(secret)
    app = Starlette(routes=[Route("/github-webhook", handler, methods=["POST"])])
    return TestClient(app, raise_server_exceptions=False)


def _push_body() -> bytes:
    return json.dumps({"ref": "refs/heads/main", "commits": []}).encode()


def _pull_result(*, from_sha: str, to_sha: str, applied: bool = True) -> PullResult:
    return PullResult(
        applied=applied,
        fast_forward=applied,
        commits_pulled=1 if (applied and from_sha != to_sha) else 0,
        from_sha=from_sha,
        to_sha=to_sha,
        reason=None if applied else "fetch_failed",
    )


def _mock_vault(
    *, queryable: bool = True, pull_result: PullResult | None = None
) -> MagicMock:
    col = MagicMock()
    col.index.is_queryable.return_value = queryable
    col.force_pull.return_value = pull_result or _pull_result(
        from_sha="aaa", to_sha="bbb"
    )
    return col


# ---------------------------------------------------------------------------
# _verify_signature (pure function)
# ---------------------------------------------------------------------------


def test_verify_signature_valid():
    body = b'{"ref": "refs/heads/main"}'
    assert _verify_signature(body, SECRET, _sign(body)) is True


def test_verify_signature_wrong_digest():
    body = b'{"ref": "refs/heads/main"}'
    assert _verify_signature(body, SECRET, "sha256=deadbeef00") is False


def test_verify_signature_missing_header():
    assert _verify_signature(b"body", SECRET, None) is False


def test_verify_signature_no_sha256_prefix():
    body = b"body"
    raw_hex = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    # Header without the "sha256=" prefix must be rejected
    assert _verify_signature(body, SECRET, raw_hex) is False


def test_verify_signature_wrong_secret():
    body = b"body"
    sig = _sign(body, secret="wrong-secret")
    assert _verify_signature(body, SECRET, sig) is False


def test_verify_signature_body_mismatch():
    body = b"real body"
    sig = _sign(b"other body")
    assert _verify_signature(body, SECRET, sig) is False


# ---------------------------------------------------------------------------
# HMAC rejection
# ---------------------------------------------------------------------------


def test_webhook_rejects_invalid_signature():
    client = _make_client()
    body = _push_body()
    resp = client.post(
        "/github-webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_webhook_rejects_missing_signature():
    client = _make_client()
    resp = client.post(
        "/github-webhook",
        content=_push_body(),
        headers={"X-GitHub-Event": "push", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_webhook_rejects_tampered_body():
    client = _make_client()
    original = _push_body()
    tampered = original + b"tampered"
    resp = client.post(
        "/github-webhook",
        content=tampered,
        headers={
            "X-Hub-Signature-256": _sign(original),  # signed original, not tampered
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# ping event
# ---------------------------------------------------------------------------


def test_webhook_ping_returns_200_without_pull():
    client = _make_client()
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    with patch("markdown_vault_mcp._github_webhook.get_vault_singleton") as mock_get:
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "ping",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Non-push events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", ["issues", "pull_request", "release", "star", "fork"])
def test_webhook_ignores_non_push_events(event: str):
    client = _make_client()
    body = json.dumps({"action": "opened"}).encode()
    with patch("markdown_vault_mcp._github_webhook.get_vault_singleton") as mock_get:
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": event,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# push event: pull + reindex
# ---------------------------------------------------------------------------


def test_webhook_push_triggers_pull_and_reindex():
    """Valid push with HEAD advancing calls force_pull then reindex."""
    col = _mock_vault(pull_result=_pull_result(from_sha="aaa", to_sha="bbb"))
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    col.force_pull.assert_called_once()
    col.index.reindex.assert_called_once()


def test_webhook_push_skips_reindex_when_already_up_to_date():
    """Remote already matches local HEAD — no reindex needed."""
    col = _mock_vault(pull_result=_pull_result(from_sha="aaa", to_sha="aaa"))
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    col.force_pull.assert_called_once()
    col.index.reindex.assert_not_called()


def test_webhook_push_returns_503_when_pull_fails():
    """force_pull applied=False → 503 so GitHub retries transient failures."""
    col = _mock_vault(
        pull_result=_pull_result(from_sha="aaa", to_sha="aaa", applied=False)
    )
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 503
    assert "error" in resp.json()
    col.index.reindex.assert_not_called()


def test_webhook_push_runs_pull_but_skips_reindex_when_not_queryable():
    """Cold start — force_pull runs (pure git, no FTS dependency) but reindex is skipped."""
    col = _mock_vault(
        queryable=False,
        pull_result=_pull_result(from_sha="aaa", to_sha="bbb"),
    )
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    col.force_pull.assert_called_once()
    col.index.reindex.assert_not_called()


def test_webhook_push_returns_503_when_singleton_not_initialized():
    """Server lifespan not yet complete — 503 so GitHub retries."""
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton",
        side_effect=RuntimeError("not initialized"),
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 503
    assert "error" in resp.json()


def test_webhook_push_no_git_strategy_returns_200():
    """Vault has no git strategy — force_pull returns None; graceful no-op."""
    col = _mock_vault()
    col.force_pull.return_value = None  # no git strategy
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    col.index.reindex.assert_not_called()


def test_webhook_push_reindex_failure_does_not_propagate_to_github():
    """Reindex error is logged but webhook returns 200 so GitHub doesn't retry."""
    col = _mock_vault(pull_result=_pull_result(from_sha="aaa", to_sha="bbb"))
    col.index.reindex.side_effect = Exception("disk full")
    client = _make_client()
    body = _push_body()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = client.post(
            "/github-webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Vault.force_pull — unit tests for the new public facade
# ---------------------------------------------------------------------------


def test_vault_force_pull_returns_none_without_git_strategy(
    tmp_path: Path,
) -> None:
    """Vault with no git strategy returns None."""

    vault = tmp_path / "vault"
    vault.mkdir()
    col = Vault(source_dir=vault)
    assert col.force_pull() is None


def test_vault_force_pull_delegates_to_strategy(tmp_path: Path) -> None:
    """Vault with a git strategy calls strategy.force_pull() and returns its result."""

    vault = tmp_path / "vault"
    vault.mkdir()
    expected = PullResult(
        applied=True,
        fast_forward=True,
        commits_pulled=3,
        from_sha="abc",
        to_sha="def",
    )
    mock_strategy = MagicMock()
    mock_strategy.force_pull.return_value = expected
    col = Vault(source_dir=vault, git_strategy=mock_strategy)
    result = col.force_pull()
    assert result is expected
    mock_strategy.force_pull.assert_called_once_with()


def test_vault_wires_pause_writes_into_git_strategy(tmp_path: Path) -> None:
    """Vault wires its pause_writes (and the dispatcher drain) into the git
    strategy at construction via set_write_quiescer, so the strategy self-quiesces
    around its own merge (#571). The pull facade no longer wraps pause_writes."""
    vault = tmp_path / "vault"
    vault.mkdir()

    mock_strategy = MagicMock()
    col = Vault(source_dir=vault, git_strategy=mock_strategy)

    mock_strategy.set_write_quiescer.assert_called_once()
    kwargs = mock_strategy.set_write_quiescer.call_args.kwargs
    assert kwargs["pause_writes"] == col.pause_writes
    # Wired to the dispatcher's drain specifically (not just any callable).
    assert kwargs["drain_writes"] == col._write_callback.drain

    # The facade delegates straight to the strategy (no Vault-level pause wrap).
    pull_result = PullResult(
        applied=True, fast_forward=True, commits_pulled=1, from_sha="aaa", to_sha="bbb"
    )
    mock_strategy.force_pull.return_value = pull_result
    assert col.force_pull() is pull_result
    mock_strategy.force_pull.assert_called_once_with()

    col.close()


# ---------------------------------------------------------------------------
# no managed remote (#1128)
# ---------------------------------------------------------------------------


def _post_push(client: TestClient) -> object:
    """POST a correctly-signed push delivery."""
    body = _push_body()
    return client.post(
        "/github-webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )


def test_force_pull_is_inert_without_remote_sync(tmp_path: Path) -> None:
    """An unmanaged strategy answers without running git at all (#1128).

    Before the fix ``enable_pull`` gated only the periodic loop, so
    ``force_pull`` ran ``git fetch origin`` on a remoteless checkout and
    reported the retryable-looking ``fetch_failed``.
    """
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    repo = tmp_path / "repo"
    repo.mkdir()

    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=repo,
    )
    try:
        with patch("subprocess.run") as run:
            result = strategy.force_pull()
        run.assert_not_called()
    finally:
        strategy.close()

    assert result.applied is False
    assert result.reason == "pull_disabled"


def test_force_pull_on_a_non_git_directory_does_not_raise(tmp_path: Path) -> None:
    """The second reported shape: a plain directory, not a git repo (#1128).

    ``_pull_pipeline``'s ``head_sha`` raised ``CalledProcessError`` here,
    which escaped the webhook handler as an unhandled 500.
    """
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    plain = tmp_path / "plain"
    plain.mkdir()

    strategy = GitWriteStrategy(
        token=None,
        repo_url=None,
        managed=False,
        enable_pull=False,
        enable_push=False,
        repo_path=plain,
    )
    try:
        result = strategy.force_pull()
    finally:
        strategy.close()

    assert result.reason == "pull_disabled"


def test_webhook_push_returns_200_when_pull_is_disabled() -> None:
    """A delivery with no remote to pull from is recorded, not retried (#1128).

    ``applied=False`` used to answer 503, so every push to the repository
    burned GitHub's full retry budget and then failed.
    """
    col = _mock_vault()
    col.force_pull.return_value = PullResult(
        applied=False,
        fast_forward=False,
        commits_pulled=0,
        from_sha="",
        to_sha="",
        reason="pull_disabled",
    )
    client = _make_client()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = _post_push(client)

    assert resp.status_code == 200
    assert resp.json()["message"] == "pull disabled"
    col.index.reindex.assert_not_called()


def test_webhook_push_returns_503_when_force_pull_raises() -> None:
    """An exception out of force_pull becomes a 503, never an unhandled 500."""
    import subprocess

    col = _mock_vault()
    col.force_pull.side_effect = subprocess.CalledProcessError(128, ["git"])
    client = _make_client()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = _post_push(client)

    assert resp.status_code == 503
    col.index.reindex.assert_not_called()


def test_webhook_still_returns_503_for_retryable_pull_failures() -> None:
    """The #1128 carve-out is narrow: fetch_failed still asks for a retry."""
    col = _mock_vault(
        pull_result=_pull_result(from_sha="aaa", to_sha="aaa", applied=False)
    )
    client = _make_client()
    with patch(
        "markdown_vault_mcp._github_webhook.get_vault_singleton", return_value=col
    ):
        resp = _post_push(client)

    assert resp.status_code == 503
    assert resp.json()["reason"] == "fetch_failed"
