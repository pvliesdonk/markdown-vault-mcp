"""``TransferConfig``: defaults, validation, the env reads and the drift gate (#2321).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_transfer_config.py``.

Pins the env section for the ``/transfer`` feature: the default values, the
``__post_init__`` invariants (all-positive, default ≤ max), the ``from_env``
literal-suffix read surface, and the ``domain_env_suffixes`` drift gate.
"""

from __future__ import annotations

import dataclasses

import pytest

from markdown_vault_mcp._pvl_core import domain_env_suffixes, domain_env_surface
from markdown_vault_mcp.config_sections import TransferConfig
from markdown_vault_mcp.exceptions import ConfigurationError

# The full literal env surface TransferConfig.from_env reads; the drift gate
# (domain_env_suffixes) and from_env must agree on exactly this set.
_EXPECTED_SUFFIXES = frozenset(
    {
        "TRANSFER_TTL_DEFAULT_S",
        "TRANSFER_TTL_MAX_S",
        "TRANSFER_GRACE_TTL_S",
        "TRANSFER_LEASE_S",
        "TRANSFER_MAX_UPLOAD_BYTES",
    }
)


class TestDefaults:
    def test_construct_with_no_args_uses_defaults(self) -> None:
        cfg = TransferConfig()
        assert cfg.ttl_default_s == 3600.0
        assert cfg.ttl_max_s == 86_400.0
        assert cfg.grace_ttl_s == 60.0
        assert cfg.lease_s == 60.0
        assert cfg.max_upload_bytes == 100 * 1024 * 1024

    def test_default_ttl_within_max(self) -> None:
        # The default pair must itself satisfy the default ≤ max invariant.
        cfg = TransferConfig()
        assert cfg.ttl_default_s <= cfg.ttl_max_s

    def test_is_frozen(self) -> None:
        cfg = TransferConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.lease_s = 5.0  # type: ignore[misc]


class TestPostInitValidation:
    @pytest.mark.parametrize(
        "field",
        [
            "ttl_default_s",
            "ttl_max_s",
            "grace_ttl_s",
            "lease_s",
            "max_upload_bytes",
        ],
    )
    def test_non_positive_field_raises(self, field: str) -> None:
        # A base that satisfies default ≤ max, then drive one field to zero.
        base = {
            "ttl_default_s": 100.0,
            "ttl_max_s": 200.0,
            "grace_ttl_s": 10.0,
            "lease_s": 10.0,
            "max_upload_bytes": 1024,
        }
        base[field] = 0
        with pytest.raises(ConfigurationError, match=field):
            TransferConfig(**base)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field",
        [
            "ttl_default_s",
            "ttl_max_s",
            "grace_ttl_s",
            "lease_s",
            "max_upload_bytes",
        ],
    )
    def test_negative_field_raises(self, field: str) -> None:
        base = {
            "ttl_default_s": 100.0,
            "ttl_max_s": 200.0,
            "grace_ttl_s": 10.0,
            "lease_s": 10.0,
            "max_upload_bytes": 1024,
        }
        base[field] = -1
        with pytest.raises(ConfigurationError, match=field):
            TransferConfig(**base)  # type: ignore[arg-type]

    def test_default_exceeding_max_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="ttl_default_s"):
            TransferConfig(ttl_default_s=200.0, ttl_max_s=100.0)

    def test_default_equal_to_max_accepted(self) -> None:
        # Boundary: default == max is allowed (the clamp is a no-op there).
        cfg = TransferConfig(ttl_default_s=100.0, ttl_max_s=100.0)
        assert cfg.ttl_default_s == cfg.ttl_max_s == 100.0

    @pytest.mark.parametrize("bad", [float("inf"), float("nan")])
    def test_non_finite_field_raises(self, bad: float) -> None:
        # inf passes a naive value > 0 check (inf > 0 is True); the finiteness
        # guard rejects it so an operator cannot mint a never-expiring link on
        # the direct-construction path (the env path already rejects non-finite).
        with pytest.raises(ConfigurationError, match="ttl_max_s"):
            TransferConfig(ttl_max_s=bad)


class TestFromEnv:
    def test_reads_all_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_DEFAULT_S", "111")
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_MAX_S", "222")
        monkeypatch.setenv("MYAPP_TRANSFER_GRACE_TTL_S", "33")
        monkeypatch.setenv("MYAPP_TRANSFER_LEASE_S", "44")
        monkeypatch.setenv("MYAPP_TRANSFER_MAX_UPLOAD_BYTES", "555")
        cfg = TransferConfig.from_env("MYAPP")
        assert cfg.ttl_default_s == 111.0
        assert cfg.ttl_max_s == 222.0
        assert cfg.grace_ttl_s == 33.0
        assert cfg.lease_s == 44.0
        assert cfg.max_upload_bytes == 555

    def test_unset_falls_back_to_defaults(self) -> None:
        cfg = TransferConfig.from_env("MYAPP")
        assert cfg == TransferConfig()

    def test_lease_defaults_when_only_it_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every other var set, lease absent → lease takes its default, not a
        # silent zero. Guards the TRANSFER_LEASE_S wiring specifically.
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_DEFAULT_S", "111")
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_MAX_S", "222")
        monkeypatch.setenv("MYAPP_TRANSFER_GRACE_TTL_S", "33")
        monkeypatch.setenv("MYAPP_TRANSFER_MAX_UPLOAD_BYTES", "555")
        cfg = TransferConfig.from_env("MYAPP")
        assert cfg.lease_s == 60.0

    def test_malformed_float_raises_naming_the_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_TRANSFER_LEASE_S", "not-a-number")
        with pytest.raises(ConfigurationError, match="TRANSFER_LEASE_S"):
            TransferConfig.from_env("MYAPP")

    def test_malformed_int_raises_naming_the_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYAPP_TRANSFER_MAX_UPLOAD_BYTES", "1.5")
        with pytest.raises(ConfigurationError, match="TRANSFER_MAX_UPLOAD_BYTES"):
            TransferConfig.from_env("MYAPP")

    def test_env_value_violating_invariant_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A well-formed but out-of-contract env pair (default > max) is caught
        # by __post_init__ after parsing, not silently accepted.
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_DEFAULT_S", "500")
        monkeypatch.setenv("MYAPP_TRANSFER_TTL_MAX_S", "100")
        with pytest.raises(ConfigurationError, match="ttl_default_s"):
            TransferConfig.from_env("MYAPP")

    def test_negative_env_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYAPP_TRANSFER_GRACE_TTL_S", "-5")
        with pytest.raises(ConfigurationError, match="grace_ttl_s"):
            TransferConfig.from_env("MYAPP")


class TestDomainEnvSuffixes:
    def test_returns_exactly_the_read_surface(self) -> None:
        assert domain_env_suffixes(TransferConfig) == _EXPECTED_SUFFIXES

    def test_drift_gate_matches_from_env(self) -> None:
        """Anti-drift: the AST scan of from_env must equal the expected set.

        A literal read added/removed/renamed in ``from_env`` without updating
        ``_EXPECTED_SUFFIXES`` (and the ADR §7 table) fails here.
        """
        assert domain_env_suffixes(TransferConfig) == _EXPECTED_SUFFIXES


class TestDomainEnvSurface:
    """The metadata-carrying surface for the transfer section — the motivating
    case: composed ``TRANSFER_*`` vars must render documented, not required."""

    def test_surface_suffixes_match_the_frozenset_gate(self) -> None:
        assert {
            v.suffix for v in domain_env_surface(TransferConfig)
        } == _EXPECTED_SUFFIXES

    def test_every_var_carries_provenance(self) -> None:
        assert all(
            v.source == "TransferConfig" for v in domain_env_surface(TransferConfig)
        )

    def test_every_var_resolves_to_its_field(self) -> None:
        """Each prefixed suffix links back to the field it populates."""
        by_suffix = {v.suffix: v for v in domain_env_surface(TransferConfig)}
        assert by_suffix["TRANSFER_TTL_DEFAULT_S"].name == "ttl_default_s"
        assert by_suffix["TRANSFER_MAX_UPLOAD_BYTES"].name == "max_upload_bytes"

    def test_every_var_is_documented_and_tagged(self) -> None:
        surface = domain_env_surface(TransferConfig)
        assert all(v.help for v in surface)
        assert all("transfer" in v.tags for v in surface)

    def test_defaulted_vars_are_not_required(self) -> None:
        """All transfer knobs have defaults, so none render as required — the
        regression the flat frozenset caused downstream."""
        assert all(v.required is False for v in domain_env_surface(TransferConfig))

    def test_default_is_carried_through(self) -> None:
        ttl = next(
            v
            for v in domain_env_surface(TransferConfig)
            if v.suffix == "TRANSFER_TTL_DEFAULT_S"
        )
        assert ttl.default == pytest.approx(3600.0)

    def test_every_var_is_gated_to_server_deployment(self) -> None:
        """Transfer is HTTP-only (the route/tools mount only under an HTTP
        transport), so every knob's wizard hint gates to a server deployment —
        a stdio/local wizard must not surface them (issue #255). The group stays
        ``Transfer`` so the gating rides alongside, not replacing, the section."""
        surface = domain_env_surface(TransferConfig)
        assert surface  # guard: an empty surface would pass all() vacuously
        assert all(v.wizard.get("when") == "server" for v in surface)
        assert all(v.wizard.get("group") == "Transfer" for v in surface)
