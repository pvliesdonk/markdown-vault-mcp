"""Unit tests for the in-memory transfer token store (#622)."""

from markdown_vault_mcp.transfer.store import TransferStore


class _Clock:
    """A controllable monotonic clock for deterministic expiry tests."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_create_then_claim_succeeds():
    """A freshly created token is claimable once for its kind."""
    store = TransferStore(clock=_Clock(1000.0))
    rec = store.create("download", "a.md", False, 60)
    assert rec.token
    assert rec.kind == "download"
    assert rec.path == "a.md"
    assert rec.expires_at == 1060.0
    assert store.claim(rec.token, "download") is rec


def test_claim_unknown_token_returns_none():
    """An unknown token never claims."""
    store = TransferStore(clock=_Clock(1000.0))
    assert store.claim("nope", "download") is None


def test_claim_wrong_kind_returns_none():
    """A download token does not claim as an upload."""
    store = TransferStore(clock=_Clock(1000.0))
    rec = store.create("download", "a.md", False, 60)
    assert store.claim(rec.token, "upload") is None


def test_claim_expired_returns_none():
    """A token past its TTL does not claim."""
    clock = _Clock(1000.0)
    store = TransferStore(clock=clock)
    rec = store.create("download", "a.md", False, 60)
    clock.t = 1061.0
    assert store.claim(rec.token, "download") is None


def test_sweep_preserves_active_in_flight_token():
    """A create()-triggered sweep keeps an in-flight token under a live lease."""
    clock = _Clock(1000.0)
    store = TransferStore(clock=clock, lease_seconds=300.0)
    rec = store.create("upload", "a.md", False, 10)
    assert store.claim(rec.token, "upload") is rec
    clock.t = 1011.0  # past the 1010 TTL, within the 1300 lease
    store.create("download", "b.md", False, 60)  # triggers the sweep
    assert rec.token in store._entries


def test_sweep_removes_dead_in_flight_token():
    """A create()-triggered sweep drops an in-flight token whose lease also expired."""
    clock = _Clock(1000.0)
    store = TransferStore(clock=clock, lease_seconds=300.0)
    rec = store.create("upload", "a.md", False, 10)
    assert store.claim(rec.token, "upload") is rec
    clock.t = 2000.0  # past the 1010 TTL and past the 1300 lease
    store.create("download", "b.md", False, 60)  # triggers the sweep
    assert rec.token not in store._entries
