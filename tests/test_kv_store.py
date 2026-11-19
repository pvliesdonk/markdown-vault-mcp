"""Tests for the key-value store factory (ported from pvl-core 10.2.0, #2279)."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import tempfile
import types

import pytest
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

from markdown_vault_mcp._kv_store import build_kv_store
from markdown_vault_mcp._pvl_core import ConfigurationError
from markdown_vault_mcp.config_sections.server import ServerConfig

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
"""Root ignores directory permission bits, so the unwritable-directory
cases cannot be exercised as root (CI runs unprivileged)."""


@pytest.fixture(autouse=True)
def _reset_one_shot_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a fresh process state for the one-shot warnings."""
    monkeypatch.setattr("markdown_vault_mcp._kv_store._legacy_url_warned", False)
    monkeypatch.setattr("markdown_vault_mcp._kv_store._default_fallback_warned", False)


def _fake_store_module(
    monkeypatch: pytest.MonkeyPatch, module: str, cls: str
) -> list[dict[str, object]]:
    """Stand a recorder in for a network-backed store class.

    The real stores open clients on construction, and their client
    libraries are optional, so a fake module records the arguments instead.
    """
    calls: list[dict[str, object]] = []

    def _record(**kwargs: object) -> MemoryStore:
        calls.append(kwargs)
        return MemoryStore()

    fake = types.ModuleType(module)
    setattr(fake, cls, _record)
    monkeypatch.setitem(sys.modules, module, fake)
    return calls


class TestBuildKvStoreMemoryBackend:
    def test_memory_url_returns_memory_backend(self):
        config = ServerConfig(kv_store_url="memory://")
        store = build_kv_store(config, namespace="ns")
        assert isinstance(store, PrefixCollectionsWrapper)
        assert isinstance(store.key_value, MemoryStore)

    def test_namespace_is_used_as_prefix(self):
        config = ServerConfig(kv_store_url="memory://")
        store = build_kv_store(config, namespace="my-ns")
        assert store.prefix == "my-ns"

    def test_distinct_namespaces_isolate_collections(self):
        # Two factories on the same URL must produce stores whose
        # collection prefixes differ, otherwise the namespace promise
        # is broken.
        cfg = ServerConfig(kv_store_url="memory://")
        a = build_kv_store(cfg, namespace="events")
        b = build_kv_store(cfg, namespace="file-exchange")
        assert a.prefix != b.prefix

    async def test_namespace_isolation_is_behavioral(self):
        # The .prefix check above proves the configuration; this proves
        # the runtime contract — a write under one namespace is invisible
        # under another, even when both share the same backend. This is
        # the bug class the wrapper exists to prevent (events writing
        # under collection "tokens" and file-exchange writing under
        # collection "tokens" silently colliding).
        cfg = ServerConfig(kv_store_url="memory://")
        a = build_kv_store(cfg, namespace="events")
        b = build_kv_store(cfg, namespace="file-exchange")
        await a.put(collection="tokens", key="k1", value={"who": "events"})
        assert await a.get(collection="tokens", key="k1") == {"who": "events"}
        assert await b.get(collection="tokens", key="k1") is None


class TestBuildKvStoreFileBackend:
    def test_file_url(self):
        with tempfile.TemporaryDirectory() as td:
            config = ServerConfig(kv_store_url=f"file://{td}/state")
            store = build_kv_store(config, namespace="ns")
            assert isinstance(store, PrefixCollectionsWrapper)
            assert isinstance(store.key_value, FileTreeStore)

    def test_default_when_unset(self, tmp_path, monkeypatch):
        """No URL and no legacy override → default file:// path."""
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR",
            str(tmp_path / "default-state"),
        )
        config = ServerConfig()
        store = build_kv_store(config, namespace="ns")
        assert isinstance(store.key_value, FileTreeStore)
        assert (tmp_path / "default-state").exists()

    def test_file_url_with_netloc_rejected(self):
        # `file://relative/path` is a common typo for `file:///abs/path`
        # — netloc absorbs the would-be path leading segment. Reject
        # explicitly rather than silently rewriting to the default.
        config = ServerConfig(kv_store_url="file://var/state")
        with pytest.raises(ConfigurationError, match="host component"):
            build_kv_store(config, namespace="ns")

    def test_file_url_with_empty_path_rejected(self):
        config = ServerConfig(kv_store_url="file://")
        with pytest.raises(ConfigurationError, match="missing a path"):
            build_kv_store(config, namespace="ns")

    def test_file_url_error_does_not_leak_credentials(self):
        # A malformed `file://` URL with credentials in userinfo
        # (rare but possible operator misconfiguration) must not echo
        # the raw URL into the error text — that string ends up
        # in process logs / Sentry. Parallel to the legacy-warning
        # credential-redaction promise.
        config = ServerConfig(kv_store_url="file://alice:hunter2@host/p")
        with pytest.raises(ConfigurationError) as exc_info:
            build_kv_store(config, namespace="ns")
        msg = str(exc_info.value)
        assert "hunter2" not in msg
        assert "alice" not in msg


class TestBuildKvStoreDefaultDirectoryUnusable:
    """The unset-URL default degrades instead of crashing construction.

    ``file:///data/state`` is a Docker convention. The same code runs
    unconfigured on hosts that never mount ``/data`` — CI runners, uvx
    installs, the stdio plugin channel — where the eager ``mkdir`` used
    to raise ``PermissionError`` at *server construction* time. There
    the default resolves to ``memory://`` instead.
    """

    def test_missing_parent_falls_back_to_memory(self, tmp_path, monkeypatch):
        # `/data` absent is the CI-runner / uvx case: the host never
        # opted into the volume convention.
        default_dir = tmp_path / "absent-mount" / "state"
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(default_dir)
        )
        store = build_kv_store(ServerConfig(), namespace="ns")
        assert isinstance(store.key_value, MemoryStore)

    def test_missing_parent_is_not_created(self, tmp_path, monkeypatch):
        # Running as root the old mkdir silently "succeeded", leaking a
        # root-level directory nobody mounted. The probe must not create
        # the mount point — only a directory *inside* an existing one.
        default_dir = tmp_path / "absent-mount" / "state"
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(default_dir)
        )
        build_kv_store(ServerConfig(), namespace="ns")
        assert not (tmp_path / "absent-mount").exists()

    def test_fallback_warns_and_names_the_variable(self, tmp_path, monkeypatch, caplog):
        default_dir = tmp_path / "absent-mount" / "state"
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(default_dir)
        )
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"):
            build_kv_store(ServerConfig(), namespace="ns")
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "memory://" in messages
        assert "KV_STORE_URL" in messages

    def test_fallback_warning_is_one_shot_per_process(
        self, tmp_path, monkeypatch, caplog
    ):
        # A server builds three namespaced stores (events, transfer,
        # jobs); the operator should see one warning, not three.
        default_dir = tmp_path / "absent-mount" / "state"
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(default_dir)
        )
        config = ServerConfig()
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"):
            build_kv_store(config, namespace="events")
            build_kv_store(config, namespace="transfer")
            build_kv_store(config, namespace="jobs")
        fallbacks = [r for r in caplog.records if "memory://" in r.getMessage()]
        assert len(fallbacks) == 1

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses directory permission checks")
    def test_unwritable_parent_falls_back_to_memory(self, tmp_path, monkeypatch):
        # `/data` present but not writable — the mkdir itself raises.
        mount = tmp_path / "ro-mount"
        mount.mkdir()
        mount.chmod(0o500)
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(mount / "state")
        )
        try:
            store = build_kv_store(ServerConfig(), namespace="ns")
        finally:
            mount.chmod(0o700)
        assert isinstance(store.key_value, MemoryStore)

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses directory permission checks")
    def test_existing_unwritable_directory_falls_back_to_memory(
        self, tmp_path, monkeypatch
    ):
        # mkdir(exist_ok=True) is a no-op here, so only the explicit
        # access check catches it — otherwise the failure would surface
        # at the first job promotion rather than at construction.
        default_dir = tmp_path / "state"
        default_dir.mkdir()
        default_dir.chmod(0o500)
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR", str(default_dir)
        )
        try:
            store = build_kv_store(ServerConfig(), namespace="ns")
        finally:
            default_dir.chmod(0o700)
        assert isinstance(store.key_value, MemoryStore)

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses directory permission checks")
    def test_explicit_file_url_is_never_degraded(self, tmp_path):
        # The degradation is a property of the *default*, not of the
        # file backend: an operator who named a directory gets a hard
        # error when it is unusable, never a silent memory store.
        mount = tmp_path / "ro-mount"
        mount.mkdir()
        mount.chmod(0o500)
        config = ServerConfig(kv_store_url=f"file://{mount}/state")
        try:
            with pytest.raises(PermissionError):
                build_kv_store(config, namespace="ns")
        finally:
            mount.chmod(0o700)

    def test_legacy_url_still_takes_precedence_over_the_fallback(
        self, tmp_path, monkeypatch
    ):
        # The default resolves only when *no* URL is configured; the
        # legacy override must not be short-circuited by the probe.
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR",
            str(tmp_path / "absent-mount" / "state"),
        )
        config = ServerConfig(event_store_url=f"file://{tmp_path}/legacy")
        store = build_kv_store(config, namespace="ns")
        assert isinstance(store.key_value, FileTreeStore)


class TestBuildKvStoreNamespaceValidation:
    def test_empty_namespace_rejected(self):
        # The whole isolation guarantee is "different subsystems pick
        # different prefixes"; an empty prefix defeats it.
        config = ServerConfig(kv_store_url="memory://")
        with pytest.raises(ValueError, match="non-empty"):
            build_kv_store(config, namespace="")

    def test_whitespace_only_namespace_rejected(self):
        # The guard uses `.strip()` so whitespace-only is also caught;
        # an empty-after-strip prefix is the same isolation defeat.
        config = ServerConfig(kv_store_url="memory://")
        with pytest.raises(ValueError, match="non-empty"):
            build_kv_store(config, namespace="   ")


class TestBuildKvStoreUrlPrecedence:
    def test_kv_store_url_wins_over_event_store_url(self):
        config = ServerConfig(
            kv_store_url="memory://",
            event_store_url="file:///should-not-be-used",
        )
        store = build_kv_store(config, namespace="ns")
        assert isinstance(store.key_value, MemoryStore)

    def test_event_store_url_used_when_kv_store_url_unset(self):
        with tempfile.TemporaryDirectory() as td:
            config = ServerConfig(event_store_url=f"file://{td}/legacy")
            store = build_kv_store(config, namespace="ns")
            assert isinstance(store.key_value, FileTreeStore)

    def test_legacy_event_store_url_warns(self, caplog):
        config = ServerConfig(event_store_url="memory://")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"):
            build_kv_store(config, namespace="ns")
        assert any("legacy" in record.message.lower() for record in caplog.records)

    def test_legacy_warning_does_not_leak_credentials(self, caplog):
        # Operator-set URLs may carry secrets in userinfo
        # (redis://user:pass@host/0, mongodb://user:pass@host/db).
        # The legacy-fallback warning must log only the scheme, not
        # the full URL — a regression here would page security.
        config = ServerConfig(event_store_url="redis://carol:hunter2@redis.example/0")
        # The redis backend may not be installed in this env; only the log
        # line emitted before backend construction matters here.
        with (
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"),
            contextlib.suppress(ConfigurationError),
        ):
            build_kv_store(config, namespace="ns")
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "hunter2" not in messages
        assert "carol" not in messages
        assert "redis.example" not in messages

    def test_kv_store_url_does_not_warn(self, caplog):
        config = ServerConfig(kv_store_url="memory://")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"):
            build_kv_store(config, namespace="ns")
        assert not any("legacy" in record.message.lower() for record in caplog.records)

    def test_legacy_warning_is_one_shot_per_process(self, caplog):
        # Multiple subsystems (events, oauth-state, ...) calling
        # build_kv_store on the same legacy-configured config should
        # see exactly one warning — not one per subsystem.
        config = ServerConfig(event_store_url="memory://")
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"):
            build_kv_store(config, namespace="events")
            build_kv_store(config, namespace="oauth-state")
            build_kv_store(config, namespace="file-exchange")
        legacy_warnings = [r for r in caplog.records if "legacy" in r.message.lower()]
        assert len(legacy_warnings) == 1


class TestBuildKvStoreBrokenBackendModule:
    """A store module that is present but cannot provide its symbol.

    Separate from :class:`TestBuildKvStoreOptionalBackends` because it
    deliberately does not use that class's ``_hide_module`` helper —
    the helper synthesises a ``ModuleNotFoundError``, which is the one
    shape that cannot exercise this path.
    """

    def test_store_module_present_but_broken_is_a_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The shape the other tests in this class cannot produce.

        ``py-key-value-aio`` always ships the store submodules; only the
        client libraries are extras, and each store module turns its
        missing client into a bare ``ImportError`` rather than a
        ``ModuleNotFoundError``. A handler narrowed to the latter lets
        that escape, which made the one-type contract false in the most
        common partial install. ``_hide_module`` cannot reproduce it —
        ``sys.modules[name] = None`` synthesises a
        ``ModuleNotFoundError``. Substituting a module object that
        lacks the symbol does, deterministically and without depending
        on which extras this environment happens to have.
        """
        import sys
        import types

        broken = types.ModuleType("key_value.aio.stores.redis")
        monkeypatch.setitem(sys.modules, "key_value.aio.stores.redis", broken)
        config = ServerConfig(kv_store_url="redis://localhost:6379/0")
        with pytest.raises(ConfigurationError, match=r"py-key-value-aio\[redis\]") as e:
            build_kv_store(config, namespace="ns")
        # The hint must not be the whole story: the real cause travels
        # with it, so an unrelated import failure cannot be misread as
        # "you forgot to install the extra".
        assert "The import failed with" in str(e.value)
        assert isinstance(e.value.__cause__, ImportError)


class TestBuildKvStoreOptionalBackends:
    """The redis/dynamodb/mongodb backends are optional extras.

    When the relevant ``py-key-value-aio`` extra is not installed, the
    factory must raise ``ConfigurationError`` with a message that names the
    extra to install — not a bare ``ModuleNotFoundError`` that
    leaves the operator guessing.

    These tests mock the import explicitly so they run identically
    whether or not the extra is installed in the test environment
    (CI installs everything via ``uv sync --all-extras``).
    """

    @staticmethod
    def _hide_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        """Force ``import <name>`` to raise ``ModuleNotFoundError``."""
        import sys

        monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setitem(sys.modules, name, None)  # type: ignore[arg-type]

    def test_redis_import_error_names_extra(self, monkeypatch: pytest.MonkeyPatch):
        self._hide_module(monkeypatch, "key_value.aio.stores.redis")
        config = ServerConfig(kv_store_url="redis://localhost:6379/0")
        with pytest.raises(ConfigurationError, match=r"py-key-value-aio\[redis\]"):
            build_kv_store(config, namespace="ns")

    def test_dynamodb_import_error_names_extra(self, monkeypatch: pytest.MonkeyPatch):
        self._hide_module(monkeypatch, "key_value.aio.stores.dynamodb")
        config = ServerConfig(kv_store_url="dynamodb://my-table?region=us-east-1")
        with pytest.raises(ConfigurationError, match=r"py-key-value-aio\[dynamodb\]"):
            build_kv_store(config, namespace="ns")

    def test_mongodb_import_error_names_extra(self, monkeypatch: pytest.MonkeyPatch):
        self._hide_module(monkeypatch, "key_value.aio.stores.mongodb")
        config = ServerConfig(kv_store_url="mongodb://localhost:27017/db")
        with pytest.raises(ConfigurationError, match=r"py-key-value-aio\[mongodb\]"):
            build_kv_store(config, namespace="ns")


class TestBuildKvStoreUnparseableUrl:
    def test_unparseable_url_is_a_configuration_error(self):
        """`urlparse` rejects some URLs before any scheme dispatch.

        An unclosed IPv6 bracket is the operator's typo, so it must not
        surface as the bare ``ValueError`` that means "the calling code
        is wrong" everywhere else in this module (#337).
        """
        config = ServerConfig(kv_store_url="redis://[oops")
        with pytest.raises(ConfigurationError) as exc_info:
            build_kv_store(config, namespace="ns")
        assert "could not be parsed" in str(exc_info.value)

    def test_nfkc_netloc_error_does_not_leak_userinfo(self):
        """The second ``urlparse`` failure path, which embeds the netloc.

        ``_checknetloc`` raises "netloc '<raw netloc>' contains invalid
        characters under NFKC normalization" — with userinfo intact —
        for a host that NFKC-normalises into a delimiter (U+2100 becomes
        ``a/c``). Interpolating that message, or chaining it as a cause,
        publishes the password to every log and Sentry event carrying
        the traceback. Caught post-push by the bot reviewer on PR #342
        after a local probe covered only the IPv6-bracket path.
        """
        config = ServerConfig(kv_store_url="redis://alice:hunter2@h\u2100st")
        with pytest.raises(ConfigurationError) as exc_info:
            build_kv_store(config, namespace="ns")
        msg = str(exc_info.value)
        assert "hunter2" not in msg
        assert "alice" not in msg
        # A chained cause would print the same netloc in the traceback.
        assert exc_info.value.__cause__ is None
        assert (
            exc_info.value.__context__ is None
            or not str(exc_info.value.__context__).count("hunter2")
            or exc_info.value.__suppress_context__
        )

    def test_unparseable_url_error_does_not_echo_the_url(self):
        """Same redaction promise as the ``file://`` guards.

        An unparseable URL can still carry userinfo, and this message is
        built from an exception this project did not author, so the
        no-echo guarantee is worth pinning rather than assuming.
        """
        config = ServerConfig(kv_store_url="redis://alice:hunter2@[oops")
        with pytest.raises(ConfigurationError) as exc_info:
            build_kv_store(config, namespace="ns")
        msg = str(exc_info.value)
        assert "hunter2" not in msg
        assert "alice" not in msg


class TestBuildKvStoreUnknownScheme:
    def test_unknown_scheme_raises(self):
        config = ServerConfig(kv_store_url="postgres://localhost/db")
        with pytest.raises(ConfigurationError, match="Unsupported kv_store URL scheme"):
            build_kv_store(config, namespace="ns")

    def test_dynamodb_requires_table_name(self, monkeypatch: pytest.MonkeyPatch):
        # ``dynamodb://`` with no host portion is meaningless — bail
        # early rather than constructing a store that points at no
        # table.
        _fake_store_module(
            monkeypatch, "key_value.aio.stores.dynamodb", "DynamoDBStore"
        )
        config = ServerConfig(kv_store_url="dynamodb://?region=us-east-1")
        with pytest.raises(ConfigurationError, match="must include a table name"):
            build_kv_store(config, namespace="ns")

    def test_dynamodb_url_parts_reach_the_store(self, monkeypatch: pytest.MonkeyPatch):
        # The table comes from the netloc (a stray ``:port`` is dropped),
        # region and endpoint from the query. The real store builds an
        # aiobotocore client on construction, so a recorder stands in.
        calls = _fake_store_module(
            monkeypatch, "key_value.aio.stores.dynamodb", "DynamoDBStore"
        )
        config = ServerConfig(
            kv_store_url=(
                "dynamodb://my-table:1?region=eu-west-1&endpoint=http://localhost:8000"
            )
        )
        build_kv_store(config, namespace="ns")
        assert calls == [
            {
                "table_name": "my-table",
                "region_name": "eu-west-1",
                "endpoint_url": "http://localhost:8000",
            }
        ]


class TestBuildKvStoreNetworkBackends:
    """The redis and mongodb URLs reach their store verbatim."""

    @pytest.mark.parametrize(
        ("url", "module", "cls"),
        [
            ("redis://localhost:6379/0", "key_value.aio.stores.redis", "RedisStore"),
            (
                "mongodb://localhost:27017/db",
                "key_value.aio.stores.mongodb",
                "MongoDBStore",
            ),
        ],
    )
    def test_url_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, url: str, module: str, cls: str
    ):
        calls = _fake_store_module(monkeypatch, module, cls)
        store = build_kv_store(ServerConfig(kv_store_url=url), namespace="ns")
        assert isinstance(store, PrefixCollectionsWrapper)
        assert calls == [{"url": url}]


def test_unreadable_default_parent_falls_back_to_memory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A parent whose stat is refused degrades to memory, not a crash."""
    import markdown_vault_mcp._kv_store as kv

    def _refuse(_path: object) -> bool:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(kv, "is_directory", _refuse)
    monkeypatch.setattr(kv, "_DEFAULT_KV_STORE_DIR", str(tmp_path / "state"))
    store = build_kv_store(ServerConfig(), namespace="ns")
    assert isinstance(store.key_value, MemoryStore)


def test_legacy_warning_on_an_unparseable_url_logs_a_placeholder(caplog):
    """The legacy warning runs before parsing, so it must not raise or echo."""
    config = ServerConfig(event_store_url="redis://alice:hunter2@h℀st")
    with (
        caplog.at_level(logging.WARNING, logger="markdown_vault_mcp._kv_store"),
        pytest.raises(ConfigurationError),
    ):
        build_kv_store(config, namespace="ns")
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "<unparseable>" in messages
    assert "hunter2" not in messages
