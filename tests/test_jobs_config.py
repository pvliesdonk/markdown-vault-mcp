"""``JobsConfig``: defaults, validation, the env reads and the drift gate (#2308).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_jobs_config.py``.
"""

from __future__ import annotations

import pytest

from markdown_vault_mcp._pvl_core import domain_env_suffixes, domain_env_surface
from markdown_vault_mcp.config_sections import JobsConfig
from markdown_vault_mcp.exceptions import ConfigurationError

_PREFIX = "MVM_JOBS_TEST"

# The full literal env surface JobsConfig.from_env reads; the drift gate
# (domain_env_suffixes) and from_env must agree on exactly this set.
_EXPECTED_SUFFIXES = frozenset(
    {
        "JOBS_SOFT_DEADLINE_S",
        "JOBS_RESULT_TTL_S",
        "JOBS_MAX_PER_SUBJECT",
    }
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for suffix in _EXPECTED_SUFFIXES:
        monkeypatch.delenv(f"{_PREFIX}_{suffix}", raising=False)


class TestDefaults:
    def test_defaults(self) -> None:
        cfg = JobsConfig()
        assert cfg.soft_deadline_s == 25.0
        assert cfg.result_ttl_s == 3600.0
        assert cfg.max_per_subject == 256

    def test_frozen(self) -> None:
        cfg = JobsConfig()
        with pytest.raises(AttributeError):
            cfg.soft_deadline_s = 1.0  # type: ignore[misc]


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"soft_deadline_s": 0.0},
            {"soft_deadline_s": -1.0},
            {"soft_deadline_s": float("inf")},
            {"soft_deadline_s": float("nan")},
            {"result_ttl_s": 0.0},
            {"max_per_subject": 0},
        ],
    )
    def test_non_positive_or_non_finite_rejected(
        self, kwargs: dict[str, float]
    ) -> None:
        with pytest.raises(ConfigurationError):
            JobsConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.usefixtures("clean_env")
class TestFromEnv:
    def test_unset_yields_defaults(self) -> None:
        assert JobsConfig.from_env(_PREFIX) == JobsConfig()

    def test_reads_all_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(f"{_PREFIX}_JOBS_SOFT_DEADLINE_S", "10.5")
        monkeypatch.setenv(f"{_PREFIX}_JOBS_RESULT_TTL_S", "120")
        monkeypatch.setenv(f"{_PREFIX}_JOBS_MAX_PER_SUBJECT", "8")
        cfg = JobsConfig.from_env(_PREFIX)
        assert cfg.soft_deadline_s == 10.5
        assert cfg.result_ttl_s == 120.0
        assert cfg.max_per_subject == 8

    def test_invalid_value_raises_naming_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{_PREFIX}_JOBS_SOFT_DEADLINE_S", "soon")
        with pytest.raises(ConfigurationError, match=f"{_PREFIX}_JOBS_SOFT_DEADLINE_S"):
            JobsConfig.from_env(_PREFIX)

    def test_zero_cap_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(f"{_PREFIX}_JOBS_MAX_PER_SUBJECT", "0")
        with pytest.raises(ConfigurationError, match=f"{_PREFIX}_JOBS_MAX_PER_SUBJECT"):
            JobsConfig.from_env(_PREFIX)


class TestDriftGate:
    def test_env_surface_matches(self) -> None:
        assert domain_env_suffixes(JobsConfig) == _EXPECTED_SUFFIXES

    def test_surface_records_resolve_to_fields(self) -> None:
        records = domain_env_surface(JobsConfig)
        assert {r.suffix for r in records} == _EXPECTED_SUFFIXES
        # Every read is tied to its constructor field, so the config-surface
        # generator can document these vars with field metadata.
        assert all(r.name is not None for r in records)
        assert all(r.help for r in records)
