"""Tests for the push-webhook endpoints (issues #530, #1178).

The shared handler is exercised through the GitHub provider; the GitLab
section covers what differs — its two authentication forms and its event
vocabulary — rather than repeating the pull/reindex matrix against a second
provider that reaches the identical code.

Failure modes covered:
- Invalid / missing / malformed HMAC signature → 401
- ping event → 200, no pull
- Non-push events → 200 no-op
- push + HEAD advances → force_pull + reindex
- push + already up-to-date → no reindex
- push + force_pull applied=False → 503 retry (hosts retry transient failures)
- push + vault not queryable → 200, pull runs, reindex skipped
- push + vault singleton not initialized → 503 retry
- push + reindex raises → 200 (reindex failure is logged, not surfaced)
- push + no git strategy → 200 graceful no-op
- GitLab signing token: valid / tampered body / wrong id or timestamp /
  stale and future timestamps / multiple candidate signatures / missing headers
- GitLab secret token: valid / wrong / absent, and rejected when only the
  signing token is configured
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp._webhooks import (
    GITLAB_TIMESTAMP_TOLERANCE_S,
    _verify_github_signature,
    _verify_gitlab_signature,
    github_provider,
    gitlab_hmac_key,
    gitlab_provider,
    make_webhook_handler,
)
from markdown_vault_mcp.exceptions import ConfigurationError
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
    handler = make_webhook_handler(github_provider(secret))
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
# _verify_github_signature (pure function)
# ---------------------------------------------------------------------------


def test_verify_github_signature_valid():
    body = b'{"ref": "refs/heads/main"}'
    assert _verify_github_signature(body, SECRET, _sign(body)) is True


def test_verify_github_signature_wrong_digest():
    body = b'{"ref": "refs/heads/main"}'
    assert _verify_github_signature(body, SECRET, "sha256=deadbeef00") is False


def test_verify_github_signature_missing_header():
    assert _verify_github_signature(b"body", SECRET, None) is False


def test_verify_github_signature_no_sha256_prefix():
    body = b"body"
    raw_hex = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    # Header without the "sha256=" prefix must be rejected
    assert _verify_github_signature(body, SECRET, raw_hex) is False


def test_verify_github_signature_wrong_secret():
    body = b"body"
    sig = _sign(body, secret="wrong-secret")
    assert _verify_github_signature(body, SECRET, sig) is False


def test_verify_github_signature_body_mismatch():
    body = b"real body"
    sig = _sign(b"other body")
    assert _verify_github_signature(body, SECRET, sig) is False


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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton") as mock_get:
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton") as mock_get:
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
        "markdown_vault_mcp._webhooks.get_vault_singleton",
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
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
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
        resp = _post_push(client)

    assert resp.status_code == 503
    col.index.reindex.assert_not_called()


def test_webhook_still_returns_503_for_retryable_pull_failures() -> None:
    """The #1128 carve-out is narrow: fetch_failed still asks for a retry."""
    col = _mock_vault(
        pull_result=_pull_result(from_sha="aaa", to_sha="aaa", applied=False)
    )
    client = _make_client()
    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
        resp = _post_push(client)

    assert resp.status_code == 503
    assert resp.json()["reason"] == "fetch_failed"


# ---------------------------------------------------------------------------
# GitLab: authentication (issue #1178)
#
# GitLab 19.0+ signs deliveries per the Standard Webhooks specification, over
# `{webhook-id}.{webhook-timestamp}.{body}` rather than the body alone. Older
# versions offer only the plain-text `X-Gitlab-Token`. Both forms are accepted,
# so both are pinned here — including the replay window, which is the only
# thing standing between a captured delivery and an unlimited replay.
# ---------------------------------------------------------------------------

# A GitLab signing token is `whsec_` + standard base64 of the raw HMAC key,
# generated by GitLab rather than chosen by the operator. The tests build the
# key and the signature the way GitLab's own verification example does, from
# the token text outward — signing with the token text instead was the bug
# this fixture shape exists to catch.
SIGNING_KEY = b"\x9a" * 32
SIGNING_TOKEN = "whsec_" + base64.b64encode(SIGNING_KEY).decode()
SECRET_TOKEN = "test-gitlab-secret-token"
WEBHOOK_ID = "0d8c2f4e-1a3b-4c5d-8e9f-0a1b2c3d4e5f"


def _gitlab_signature(
    body: bytes,
    *,
    key: bytes = SIGNING_KEY,
    webhook_id: str = WEBHOOK_ID,
    timestamp: int | None = None,
) -> str:
    """Sign *body* the way GitLab does, returning the header value."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{webhook_id}.{ts}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


def _gitlab_signed_headers(
    body: bytes,
    *,
    key: bytes = SIGNING_KEY,
    webhook_id: str = WEBHOOK_ID,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else timestamp
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": _gitlab_signature(
            body, key=key, webhook_id=webhook_id, timestamp=ts
        ),
    }


def _gitlab_verify(headers: dict[str, str], body: bytes, **kwargs: str | None) -> bool:
    """Run a GitLab provider's verify() with the given configured credentials."""
    provider = gitlab_provider(
        kwargs.get("signing_token", SIGNING_TOKEN),
        kwargs.get("secret_token"),
    )
    return provider.verify(headers, body)


def test_gitlab_signing_token_accepts_a_valid_delivery() -> None:
    body = _push_body()
    assert _gitlab_verify(_gitlab_signed_headers(body), body) is True


def test_gitlab_signing_token_rejects_a_tampered_body() -> None:
    headers = _gitlab_signed_headers(_push_body())
    assert _gitlab_verify(headers, b'{"ref": "refs/heads/attacker"}') is False


def test_gitlab_signing_token_rejects_a_swapped_webhook_id() -> None:
    """The id is inside the digest, so replaying a signature under a new id fails."""
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-id"] = "11111111-2222-3333-4444-555555555555"
    assert _gitlab_verify(headers, body) is False


def test_gitlab_signing_token_rejects_a_swapped_timestamp() -> None:
    """Re-stamping a captured delivery to look fresh invalidates its signature."""
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-timestamp"] = str(int(time.time()) - 1)
    assert _gitlab_verify(headers, body) is False


def test_gitlab_signing_token_rejects_a_stale_delivery() -> None:
    """A correctly-signed delivery past the window is still refused (replay)."""
    body = _push_body()
    stale = int(time.time()) - GITLAB_TIMESTAMP_TOLERANCE_S - 1
    assert _gitlab_verify(_gitlab_signed_headers(body, timestamp=stale), body) is False


def test_gitlab_signing_token_rejects_a_delivery_from_the_future() -> None:
    body = _push_body()
    ahead = int(time.time()) + GITLAB_TIMESTAMP_TOLERANCE_S + 1
    assert _gitlab_verify(_gitlab_signed_headers(body, timestamp=ahead), body) is False


def test_gitlab_signing_token_accepts_the_window_edge() -> None:
    """The tolerance is inclusive, pinned at the boundary rather than near it.

    `_timestamp_is_fresh` compares with `<=`, so exactly
    `GITLAB_TIMESTAMP_TOLERANCE_S` seconds of skew must still authenticate;
    one second more must not, which the stale test above covers.
    """
    body = _push_body()
    now = time.time()
    edge = int(now) - GITLAB_TIMESTAMP_TOLERANCE_S
    headers = _gitlab_signed_headers(body, timestamp=edge)
    key = base64.b64decode(SIGNING_TOKEN.removeprefix("whsec_"))
    assert _verify_gitlab_signature(body, key, headers, now=float(int(now))) is True


def test_gitlab_signing_token_rejects_a_non_numeric_timestamp() -> None:
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-timestamp"] = "not-a-timestamp"
    assert _gitlab_verify(headers, body) is False


@pytest.mark.parametrize(
    "dropped", ["webhook-id", "webhook-timestamp", "webhook-signature"]
)
def test_gitlab_signing_token_rejects_a_missing_header(dropped: str) -> None:
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    del headers[dropped]
    assert _gitlab_verify(headers, body) is False


def test_gitlab_signing_token_accepts_any_of_several_signatures() -> None:
    """The header is documented as forward-compatible with multiple signatures."""
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    valid = headers["webhook-signature"]
    headers["webhook-signature"] = f"v1,{base64.b64encode(b'wrong').decode()} {valid}"
    assert _gitlab_verify(headers, body) is True


def test_gitlab_signing_token_rejects_an_unknown_signature_version() -> None:
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-signature"] = headers["webhook-signature"].replace("v1,", "v2,", 1)
    assert _gitlab_verify(headers, body) is False


def test_gitlab_signing_token_rejects_a_signature_from_another_token() -> None:
    body = _push_body()
    headers = _gitlab_signed_headers(body, key=b"\x11" * 32)
    assert _gitlab_verify(headers, body) is False


def test_gitlab_secret_token_accepts_a_matching_header() -> None:
    body = _push_body()
    assert (
        _gitlab_verify(
            {"X-Gitlab-Token": SECRET_TOKEN},
            body,
            signing_token=None,
            secret_token=SECRET_TOKEN,
        )
        is True
    )


@pytest.mark.parametrize("presented", ["wrong-token", ""])
def test_gitlab_secret_token_rejects_a_mismatch(presented: str) -> None:
    body = _push_body()
    assert (
        _gitlab_verify(
            {"X-Gitlab-Token": presented},
            body,
            signing_token=None,
            secret_token=SECRET_TOKEN,
        )
        is False
    )


def test_gitlab_secret_token_rejects_an_absent_header() -> None:
    body = _push_body()
    assert (
        _gitlab_verify({}, body, signing_token=None, secret_token=SECRET_TOKEN) is False
    )


def test_gitlab_secret_token_is_refused_when_only_signing_is_configured() -> None:
    """Configuring the strong form must not leave the weak one silently open."""
    body = _push_body()
    assert (
        _gitlab_verify({"X-Gitlab-Token": SECRET_TOKEN}, body, secret_token=None)
        is False
    )


def test_gitlab_signature_is_refused_when_only_the_secret_token_is_configured() -> None:
    body = _push_body()
    assert (
        _gitlab_verify(
            _gitlab_signed_headers(body),
            body,
            signing_token=None,
            secret_token=SECRET_TOKEN,
        )
        is False
    )


def test_gitlab_accepts_either_form_when_both_are_configured() -> None:
    """The overlap is what lets a live webhook migrate without a broken window."""
    body = _push_body()
    assert (
        _gitlab_verify(_gitlab_signed_headers(body), body, secret_token=SECRET_TOKEN)
        is True
    )
    assert (
        _gitlab_verify(
            {"X-Gitlab-Token": SECRET_TOKEN}, body, secret_token=SECRET_TOKEN
        )
        is True
    )


# ---------------------------------------------------------------------------
# GitLab: routing through the shared handler
# ---------------------------------------------------------------------------


def _make_gitlab_client(
    signing_token: str | None = SIGNING_TOKEN,
    secret_token: str | None = None,
) -> TestClient:
    handler = make_webhook_handler(gitlab_provider(signing_token, secret_token))
    app = Starlette(routes=[Route("/gitlab-webhook", handler, methods=["POST"])])
    return TestClient(app, raise_server_exceptions=False)


def test_gitlab_unsigned_delivery_is_rejected() -> None:
    client = _make_gitlab_client()
    response = client.post(
        "/gitlab-webhook", content=_push_body(), headers={"X-Gitlab-Event": "Push Hook"}
    )
    assert response.status_code == 401


def test_gitlab_push_hook_pulls_and_reindexes() -> None:
    body = _push_body()
    col = _mock_vault(pull_result=_pull_result(from_sha="aaa", to_sha="bbb"))
    headers = _gitlab_signed_headers(body) | {"X-Gitlab-Event": "Push Hook"}

    with patch("markdown_vault_mcp._webhooks.get_vault_singleton", return_value=col):
        response = _make_gitlab_client().post(
            "/gitlab-webhook", content=body, headers=headers
        )

    assert response.status_code == 200
    assert response.json()["commits_pulled"] == 1
    col.force_pull.assert_called_once()
    col.index.reindex.assert_called_once()


def test_gitlab_non_push_event_is_ignored() -> None:
    """`Push Hook` is the trigger; GitLab's other hooks reach the same route."""
    body = _push_body()
    headers = _gitlab_signed_headers(body) | {"X-Gitlab-Event": "Issue Hook"}
    response = _make_gitlab_client().post(
        "/gitlab-webhook", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["message"] == "event ignored"


def test_gitlab_has_no_ping_event() -> None:
    """GitLab's Test button sends a real Push Hook, so `ping` is not special-cased."""
    assert gitlab_provider(SIGNING_TOKEN, None).ping_event is None
    body = _push_body()
    headers = _gitlab_signed_headers(body) | {"X-Gitlab-Event": "ping"}
    response = _make_gitlab_client().post(
        "/gitlab-webhook", content=body, headers=headers
    )
    assert response.json()["message"] == "event ignored"


# ---------------------------------------------------------------------------
# GitLab: signing-token key derivation
#
# The token is `whsec_` + standard base64 of the raw HMAC key, and GitLab's
# verification example decodes it before computing the HMAC. Signing with the
# token *text* produces a digest that never matches a real delivery, and no
# test catches that unless it derives the key the way GitLab does — which is
# what these pin.
# ---------------------------------------------------------------------------


def test_gitlab_hmac_key_decodes_the_token() -> None:
    assert gitlab_hmac_key(SIGNING_TOKEN) == SIGNING_KEY


def test_gitlab_hmac_key_tolerates_a_missing_prefix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The prefix is GitLab's convention, not key material — warn, don't fail."""
    unprefixed = base64.b64encode(SIGNING_KEY).decode()
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._webhooks"):
        assert gitlab_hmac_key(unprefixed) == SIGNING_KEY
    assert "gitlab_signing_token_unprefixed" in caplog.text


def test_gitlab_hmac_key_rejects_an_undecodable_token() -> None:
    """A token that cannot be a key fails at startup, not per delivery."""
    with pytest.raises(ConfigurationError, match="signing token"):
        gitlab_hmac_key("whsec_not valid base64!!")


def test_gitlab_provider_rejects_an_undecodable_token() -> None:
    with pytest.raises(ConfigurationError):
        gitlab_provider("whsec_not valid base64!!", None)


def test_gitlab_rejects_a_signature_made_from_the_token_text() -> None:
    """The regression this file exists to hold.

    Signing with `SIGNING_TOKEN.encode()` rather than the decoded key is the
    plausible wrong implementation; it must not authenticate.
    """
    body = _push_body()
    headers = _gitlab_signed_headers(body, key=SIGNING_TOKEN.encode())
    assert _gitlab_verify(headers, body) is False


def test_gitlab_accepts_a_delivery_signed_with_the_decoded_key() -> None:
    """The interop contract, stated against GitLab's derivation rather than ours."""
    body = _push_body()
    key = base64.b64decode(SIGNING_TOKEN.removeprefix("whsec_"))
    headers = _gitlab_signed_headers(body, key=key)
    assert _gitlab_verify(headers, body) is True


# ---------------------------------------------------------------------------
# Non-ASCII credentials (all three comparison sites)
#
# ASGI decodes header values as latin-1, and `hmac.compare_digest` refuses two
# `str` operands when either holds a non-ASCII character. `verify` runs outside
# the handler's exception guard, so one byte above 0x7f in a signature header
# turned the documented 401 into an unhandled 500. GitHub's path had the same
# exposure and predates GitLab's, so all three are pinned here.
# ---------------------------------------------------------------------------

NON_ASCII = "caf\xe9"


def test_github_non_ascii_signature_is_refused_not_raised() -> None:
    assert (
        _verify_github_signature(_push_body(), SECRET, f"sha256={NON_ASCII}") is False
    )


def test_gitlab_non_ascii_signature_is_refused_not_raised() -> None:
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-signature"] = f"v1,{NON_ASCII}"
    assert _gitlab_verify(headers, body) is False


def test_gitlab_non_ascii_secret_token_is_refused_not_raised() -> None:
    assert (
        _gitlab_verify(
            {"X-Gitlab-Token": NON_ASCII},
            _push_body(),
            signing_token=None,
            secret_token=SECRET_TOKEN,
        )
        is False
    )


def test_non_ascii_signature_returns_401_through_the_handler() -> None:
    """End to end: the contract is 401, not a 500 with a traceback."""
    body = _push_body()
    headers = _gitlab_signed_headers(body)
    headers["webhook-signature"] = f"v1,{NON_ASCII}"
    headers["X-Gitlab-Event"] = "Push Hook"
    # httpx refuses to encode a non-ASCII str header, so hand it raw bytes the
    # way a hostile client would put them on the wire.
    raw = [(k.encode(), v.encode("latin-1")) for k, v in headers.items()]
    response = _make_gitlab_client().post("/gitlab-webhook", content=body, headers=raw)
    assert response.status_code == 401
