"""Config-surface introspection: ``domain_env_*`` and ``server_config_surface`` (#2289).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_config.py``
(``TestDomainEnvSuffixes``, ``TestDomainEnvSurface``,
``TestDomainEnvSurfaceNameFallback``, ``TestServerConfigSurface`` and their
fixture classes); its ``ServerConfig`` reader tests move with #2288.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from markdown_vault_mcp._config_surface import (
    ConfigField,
    DomainEnvVar,
    _config_field_from,
    domain_env_suffixes,
    domain_env_surface,
    server_config_surface,
)
from markdown_vault_mcp._env import env, env_float, env_int
from markdown_vault_mcp.config_sections.server import (
    DEFAULT_BEARER_SUBJECT,
    ServerConfig,
    server_config_env_suffixes,
)

# ---------------------------------------------------------------------------
# Module-level fixture classes for TestDomainEnvSuffixes
# (defined at module scope so typing.get_type_hints can resolve them —
# the only shape real configs take).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Sec:
    @classmethod
    def from_env(cls, prefix: str) -> _Sec:
        _ = env(prefix, "SEC_TOKEN")
        _ = env_int(prefix, "SEC_COUNT", 0)
        return cls()


@dataclass(frozen=True)
class _Flat:
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _Flat:
        _ = env(prefix, "FLAT_S")
        _ = env_int(prefix, "FLAT_I", 0)
        _ = env_float(prefix, "FLAT_F", 1.0)
        return cls()


@dataclass(frozen=True)
class _Composed:
    sec: _Sec = field(default_factory=_Sec)
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _Composed:
        _ = env(prefix, "TOP")
        return cls(sec=_Sec.from_env(prefix), server=ServerConfig.from_env(prefix))


@dataclass(frozen=True)
class _OptComposed:
    sec: _Sec | None = None

    @classmethod
    def from_env(cls, prefix: str = "X") -> _OptComposed:
        _ = env(prefix, "OPT_TOP")
        return cls()


@dataclass(frozen=True)
class _CycA:
    b: _CycB | None = None

    @classmethod
    def from_env(cls, prefix: str = "X") -> _CycA:
        _ = env(prefix, "CYC_A")
        return cls()


@dataclass(frozen=True)
class _CycB:
    a: _CycA | None = None

    @classmethod
    def from_env(cls, prefix: str = "X") -> _CycB:
        _ = env(prefix, "CYC_B")
        return cls()


@dataclass(frozen=True)
class _TwoFields:
    x: _Sec = field(default_factory=_Sec)
    y: _Sec = field(default_factory=_Sec)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _TwoFields:  # noqa: ARG003 - real from_env shape
        return cls()


@dataclass(frozen=True)
class _Plain:
    v: int = 0


@dataclass(frozen=True)
class _HasPlain:
    p: _Plain = field(default_factory=_Plain)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _HasPlain:
        _ = env(prefix, "HP_TOP")
        return cls()


@dataclass(frozen=True)
class _SubForList:
    @classmethod
    def from_env(cls, prefix: str) -> _SubForList:
        _ = env(prefix, "LIST_SUB")
        return cls()


@dataclass(frozen=True)
class _ListTypedField:
    subs: list[_SubForList] = field(default_factory=list)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _ListTypedField:
        _ = env(prefix, "LIST_TOP")
        return cls()


@dataclass(frozen=True)
class _BadRef:
    x: NonExistentType = 0  # type: ignore[name-defined]  # noqa: F821 — unresolvable on purpose

    @classmethod
    def from_env(cls, prefix: str = "X") -> _BadRef:
        _ = env(prefix, "BAD")
        return cls()


# ---------------------------------------------------------------------------
# Extra fixtures for TestDomainEnvSurface: a section whose prefixed suffixes
# are tied to metadata-carrying fields via constructor keywords (the realistic
# shape the surface resolves), and a config that composes it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MetaSec:
    token: str | None = field(
        default=None,
        metadata={
            "help": "Secret token.",
            "tags": ("meta",),
            "wizard": {"secret": True},
        },
    )
    count: int = field(
        default=3,
        metadata={"help": "How many.", "tags": ("meta",)},
    )

    @classmethod
    def from_env(cls, prefix: str = "X") -> _MetaSec:
        return cls(
            token=env(prefix, "META_TOKEN"),
            count=env_int(prefix, "META_COUNT", 3),
        )


@dataclass(frozen=True)
class _MetaComposed:
    label: str = field(default="", metadata={"help": "A label.", "tags": ("top",)})
    meta: _MetaSec = field(default_factory=_MetaSec)
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _MetaComposed:
        return cls(
            label=env(prefix, "LABEL", ""),
            meta=_MetaSec.from_env(prefix),
            server=ServerConfig.from_env(prefix),
        )


@dataclass(frozen=True)
class _ReqReadSec:
    token: str = field(default="", metadata={"help": "Section token."})

    @classmethod
    def from_env(cls, prefix: str = "X") -> _ReqReadSec:
        return cls(token=env(prefix, "RR_SEC_TOKEN", required=True))


@dataclass(frozen=True)
class _ReqRead:
    """A top-level ``required=True`` read, a section one, an unmapped one, and
    two reads without it (a defaulted ``env_int`` and an explicit
    ``required=False``) as controls."""

    api_token: str = field(default="", metadata={"help": "API token."})
    retries: int = 3
    label: str = ""
    sec: _ReqReadSec = field(default_factory=_ReqReadSec)

    @classmethod
    def from_env(cls, prefix: str = "X") -> _ReqRead:
        _ = env(prefix, "RR_UNMAPPED", required=True)
        return cls(
            api_token=env(prefix, "RR_API_TOKEN", required=True),
            retries=env_int(prefix, "RR_RETRIES", 3),
            label=env(prefix, "RR_LABEL", required=False) or "",
            sec=_ReqReadSec.from_env(prefix),
        )


@dataclass(frozen=True)
class _ReqSec:
    endpoint: str  # no default -> a required var

    @classmethod
    def from_env(cls, prefix: str = "X") -> _ReqSec:
        return cls(endpoint=env(prefix, "REQ_ENDPOINT", "fallback"))


# ---------------------------------------------------------------------------
# Fixtures for the field-name resolution fallback (issue #243): reads consumed
# via a local before cls(...) are not keyword-mapped, but a top-level field
# whose name.upper() equals the suffix must still carry its metadata.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalRead:
    read_only: bool = field(
        default=False,
        metadata={"help": "Read-only mode.", "tags": ("mode",)},
    )
    api_key: str | None = field(
        default=None,
        metadata={"help": "API key.", "tags": ("auth",), "wizard": {"secret": True}},
    )

    @classmethod
    def from_env(cls, prefix: str = "X") -> _LocalRead:
        # Both reads land in a local first, so neither cls(...) keyword carries
        # the literal; only the name fallback can attach their metadata.
        read_only_raw = env(prefix, "READ_ONLY")
        api_key = env(prefix, "API_KEY")
        return cls(read_only=bool(read_only_raw), api_key=api_key)


@dataclass(frozen=True)
class _TwoLiteralKeyword:
    host: str = field(default="", metadata={"help": "Host.", "tags": ("net",)})
    port: str = field(default="", metadata={"help": "Port.", "tags": ("net",)})
    addr: str = field(default="", metadata={"help": "Combined.", "tags": ("net",)})

    @classmethod
    def from_env(cls, prefix: str = "X") -> _TwoLiteralKeyword:
        # Two literals in one keyword -> not keyword-mapped; each resolves by
        # field name instead.
        return cls(addr=f"{env(prefix, 'HOST')}:{env(prefix, 'PORT')}")


@dataclass(frozen=True)
class _SectionLocalRead:
    # A section-style field: its read carries a section prefix, so the prefixed
    # suffix is not name.upper() of any field. Read via a local -> unresolved.
    ttl_s: float = field(default=1.0, metadata={"help": "TTL.", "tags": ("sec",)})

    @classmethod
    def from_env(cls, prefix: str = "X") -> _SectionLocalRead:
        raw = env(prefix, "SECTION_TTL_S")
        return cls(ttl_s=float(raw) if raw else 1.0)


class TestDomainEnvSuffixes:
    def test_flat_exact_surface_and_all_read_funcs(self) -> None:
        """Flat config: env/env_int/env_float all matched, ServerConfig excluded."""
        assert domain_env_suffixes(_Flat) == frozenset({"FLAT_S", "FLAT_I", "FLAT_F"})

    def test_recurses_into_subconfig_excludes_server(self) -> None:
        """Sub-config reads collected exactly; the ServerConfig field is not."""
        assert domain_env_suffixes(_Composed) == frozenset(
            {"TOP", "SEC_TOKEN", "SEC_COUNT"}
        )
        assert "TRANSPORT" not in domain_env_suffixes(_Composed)

    def test_optional_subconfig_discovered_via_get_args(self) -> None:
        """Optional[Sub]/Sub|None resolves to a Union; the inner type is found
        via typing.get_args (no default_factory needed)."""
        assert domain_env_suffixes(_OptComposed) == frozenset(
            {"OPT_TOP", "SEC_TOKEN", "SEC_COUNT"}
        )

    def test_module_scope_cycle_terminates_via_visited(self) -> None:
        """A<->B mutual references resolve via get_type_hints in both directions;
        the visited-set is what terminates the walk."""
        assert domain_env_suffixes(_CycA) == frozenset({"CYC_A", "CYC_B"})

    def test_same_type_two_fields_deduped(self) -> None:
        """A type referenced by two fields is scanned once (visited-set)."""
        assert domain_env_suffixes(_TwoFields) == frozenset({"SEC_TOKEN", "SEC_COUNT"})

    def test_dataclass_field_without_from_env_is_skipped(self) -> None:
        """A dataclass field lacking from_env is not scanned and does not crash."""
        assert domain_env_suffixes(_HasPlain) == frozenset({"HP_TOP"})

    def test_server_config_as_root_returns_empty(self) -> None:
        """ServerConfig as root yields empty; use server_config_env_suffixes."""
        assert domain_env_suffixes(ServerConfig) == frozenset()

    def test_non_dataclass_input_raises_typeerror(self) -> None:
        """A non-dataclass argument is a caller error, not a silent empty result."""
        with pytest.raises(TypeError, match="dataclass"):
            domain_env_suffixes(int)

    def test_dataclass_instance_raises_typeerror(self) -> None:
        """An instance (vs the class) is rejected — is_dataclass alone accepts both."""
        with pytest.raises(TypeError, match="dataclass"):
            domain_env_suffixes(_Flat())  # type: ignore[arg-type]

    def test_list_subconfig_field_traversed(self) -> None:
        """list[Sub] fields are traversed via get_args (one level)."""
        assert domain_env_suffixes(_ListTypedField) == frozenset(
            {"LIST_TOP", "LIST_SUB"}
        )

    def test_source_unavailable_raises_oserror_with_context(self) -> None:
        """A class whose from_env source can't be read raises OSError naming it."""
        ns: dict[str, object] = {}
        exec(  # noqa: S102, RUF100 - Builds a test-only class with no source; S102 is gate-only.
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ExecCfg:\n"
            "    @classmethod\n"
            "    def from_env(cls, prefix='X'):\n"
            "        return cls()\n",
            ns,
        )
        with pytest.raises(OSError, match=r"ExecCfg\.from_env"):
            domain_env_suffixes(ns["ExecCfg"])  # type: ignore[arg-type]

    def test_unresolvable_forward_ref_raises_nameerror(self) -> None:
        """An annotation not importable at module scope propagates as NameError."""
        with pytest.raises(NameError, match="domain_env_suffixes"):
            domain_env_suffixes(_BadRef)


class TestDomainEnvSurface:
    @pytest.mark.parametrize(
        "cls",
        [
            _Flat,
            _Composed,
            _OptComposed,
            _CycA,
            _TwoFields,
            _HasPlain,
            _ListTypedField,
            _ReqRead,
        ],
    )
    def test_suffixes_match_the_frozenset_gate(self, cls: type) -> None:
        """The surface never drops or adds a suffix the flat frozenset carries."""
        assert {v.suffix for v in domain_env_surface(cls)} == domain_env_suffixes(cls)

    def test_records_are_domain_env_var_instances(self) -> None:
        assert all(
            isinstance(v, DomainEnvVar) for v in domain_env_surface(_MetaComposed)
        )

    def test_top_level_read_carries_its_field_metadata(self) -> None:
        surface = domain_env_surface(_MetaComposed)
        label = next(v for v in surface if v.suffix == "LABEL")
        assert label.source == "_MetaComposed"
        assert label.name == "label"
        assert label.help == "A label."
        assert label.tags == ("top",)
        assert label.required is False

    def test_section_read_carries_provenance_and_field_metadata(self) -> None:
        """A composed section's prefixed suffix resolves to its field's metadata."""
        surface = domain_env_surface(_MetaComposed)
        token = next(v for v in surface if v.suffix == "META_TOKEN")
        assert token.source == "_MetaSec"
        assert token.name == "token"
        assert token.help == "Secret token."
        assert token.tags == ("meta",)
        assert token.wizard == {"secret": True}
        assert token.required is False

    def test_default_is_carried_through(self) -> None:
        count = next(
            v for v in domain_env_surface(_MetaComposed) if v.suffix == "META_COUNT"
        )
        assert count.default == 3
        assert count.type_name == "int"

    def test_field_without_default_is_required(self) -> None:
        """A section field with no default reports required=True."""
        endpoint = next(
            v for v in domain_env_surface(_ReqSec) if v.suffix == "REQ_ENDPOINT"
        )
        assert endpoint.name == "endpoint"
        assert endpoint.required is True

    @staticmethod
    def _req_read(suffix: str) -> DomainEnvVar:
        return next(v for v in domain_env_surface(_ReqRead) if v.suffix == suffix)

    def test_required_read_marks_its_field_required(self) -> None:
        """A literal ``required=True`` read is required, with no default shown."""
        token = self._req_read("RR_API_TOKEN")
        assert token.name == "api_token"
        assert token.help == "API token."
        assert token.required is True
        assert token.default is None

    def test_required_read_in_a_section_is_required(self) -> None:
        token = self._req_read("RR_SEC_TOKEN")
        assert token.source == "_ReqReadSec"
        assert token.name == "token"
        assert token.required is True
        assert token.default is None

    def test_unmapped_required_read_is_required(self) -> None:
        unmapped = self._req_read("RR_UNMAPPED")
        assert unmapped.name is None
        assert unmapped.required is True

    def test_reads_without_required_true_are_unchanged(self) -> None:
        retries = self._req_read("RR_RETRIES")
        assert retries.required is False
        assert retries.default == 3
        label = self._req_read("RR_LABEL")
        assert label.required is False
        assert label.default == ""

    def test_throwaway_read_yields_placeholder_record(self) -> None:
        """A read not tied to a constructor field is still emitted, with name=None."""
        surface = domain_env_surface(_Composed)
        assert {v.suffix for v in surface} == {"TOP", "SEC_TOKEN", "SEC_COUNT"}
        assert all(v.name is None for v in surface)
        top = next(v for v in surface if v.suffix == "TOP")
        assert top.help == ""
        assert top.tags == ()
        assert top.required is False

    def test_order_is_depth_first_root_before_sections(self) -> None:
        """Root's own read precedes the composed section's reads."""
        order = [v.suffix for v in domain_env_surface(_MetaComposed)]
        assert order == ["LABEL", "META_TOKEN", "META_COUNT"]

    def test_server_config_field_is_excluded(self) -> None:
        suffixes = {v.suffix for v in domain_env_surface(_MetaComposed)}
        assert "TRANSPORT" not in suffixes
        assert "HOST" not in suffixes

    def test_server_config_as_root_returns_empty(self) -> None:
        assert domain_env_surface(ServerConfig) == ()

    def test_non_dataclass_input_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="dataclass"):
            domain_env_surface(int)

    def test_dataclass_instance_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="dataclass"):
            domain_env_surface(_Flat())  # type: ignore[arg-type]

    def test_source_unavailable_raises_oserror_with_context(self) -> None:
        ns: dict[str, object] = {}
        exec(  # noqa: S102, RUF100 - Builds a test-only class with no source; S102 is gate-only.
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ExecCfg:\n"
            "    @classmethod\n"
            "    def from_env(cls, prefix='X'):\n"
            "        return cls()\n",
            ns,
        )
        with pytest.raises(OSError, match=r"ExecCfg\.from_env"):
            domain_env_surface(ns["ExecCfg"])  # type: ignore[arg-type]

    def test_unresolvable_forward_ref_raises_nameerror(self) -> None:
        with pytest.raises(NameError, match="domain_env_surface"):
            domain_env_surface(_BadRef)

    def test_order_is_stable_under_hash_randomisation(self) -> None:
        """Byte-stability guard: record order must not vary between processes."""
        program = (
            "from tests.test_config_surface import _MetaComposed;"
            "from markdown_vault_mcp._config_surface import domain_env_surface;"
            "print(','.join(v.suffix for v in domain_env_surface(_MetaComposed)))"
        )
        outputs = set()
        for seed in ("1", "2", "3"):
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True,
                text=True,
                check=True,
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "PYTHONHASHSEED": seed},
            )
            outputs.add(result.stdout.strip())
        assert outputs == {"LABEL,META_TOKEN,META_COUNT"}


class TestDomainEnvSurfaceNameFallback:
    """Issue #243: a read consumed via a local (or assembled from several reads)
    is not keyword-mapped, but a field whose name.upper() equals the suffix must
    still carry its metadata — restoring the pre-4.6 field-name resolution."""

    def test_local_read_resolves_to_its_field(self) -> None:
        by_suffix = {v.suffix: v for v in domain_env_surface(_LocalRead)}
        read_only = by_suffix["READ_ONLY"]
        assert read_only.name == "read_only"
        assert read_only.help == "Read-only mode."
        assert read_only.tags == ("mode",)
        assert read_only.required is False

    def test_secret_wizard_hint_survives_the_local_read(self) -> None:
        """The regression lost isSecret masking on API keys; it must be back."""
        api_key = next(
            v for v in domain_env_surface(_LocalRead) if v.suffix == "API_KEY"
        )
        assert api_key.name == "api_key"
        assert api_key.wizard == {"secret": True}

    def test_suffixes_still_match_the_frozenset_gate(self) -> None:
        assert {
            v.suffix for v in domain_env_surface(_LocalRead)
        } == domain_env_suffixes(_LocalRead)

    def test_keyword_mapping_still_wins_when_present(self) -> None:
        """Inline reads keep their keyword resolution; the fallback is a fallback."""
        endpoint = next(
            v for v in domain_env_surface(_ReqSec) if v.suffix == "REQ_ENDPOINT"
        )
        assert endpoint.name == "endpoint"
        assert endpoint.required is True

    def test_two_literals_in_one_keyword_resolve_by_field_name(self) -> None:
        """A keyword with two literals is not keyword-mapped; each suffix falls
        back to its same-named field (open question 1 in the issue)."""
        by_suffix = {v.suffix: v for v in domain_env_surface(_TwoLiteralKeyword)}
        assert by_suffix["HOST"].name == "host"
        assert by_suffix["HOST"].help == "Host."
        assert by_suffix["PORT"].name == "port"
        assert by_suffix["PORT"].help == "Port."

    def test_section_field_read_via_local_stays_unresolved(self) -> None:
        """A prefixed section suffix does not equal name.upper() of any field, so
        a section field read via a local stays name=None (documented boundary)."""
        ttl = next(
            v
            for v in domain_env_surface(_SectionLocalRead)
            if v.suffix == "SECTION_TTL_S"
        )
        assert ttl.name is None
        assert ttl.help == ""
        assert ttl.required is False

    def test_throwaway_read_with_no_matching_field_stays_none(self) -> None:
        """The fallback must not invent a field: _Composed's throwaway reads have
        no same-named field, so they remain name=None as before."""
        assert all(v.name is None for v in domain_env_surface(_Composed))


class TestServerConfigSurface:
    def test_surface_returns_config_field_records(self):
        assert all(isinstance(c, ConfigField) for c in server_config_surface())

    @pytest.mark.parametrize(
        ("pattern", "reason"),
        [
            (r"\be\.g\.", "Latin abbreviation; write 'for example'"),
            (r"\bi\.e\.", "Latin abbreviation; write 'that is'"),
            (r"\betc\.", "Latin abbreviation; rephrase"),
            (r"\s[\u2014\u2013]\s", "spaced dash; use a comma, period, or parentheses"),
        ],
    )
    def test_help_is_publishable_prose(self, pattern: str, reason: str):
        """``help`` is public metadata consumers render into Vale-checked docs
        (#237). Keep it free of the constructs Vale's Google style rejects."""
        import re

        offenders = [
            c.name for c in server_config_surface() if re.search(pattern, c.help)
        ]
        assert offenders == [], f"{reason}: {offenders}"

    def test_covers_every_field_in_declaration_order(self):
        """Declaration order is the contract — it makes generated output stable."""
        surface = server_config_surface()
        assert tuple(c.name for c in surface) == tuple(
            f.name for f in dataclasses.fields(ServerConfig)
        )

    def test_returns_twenty_three_fields(self):
        assert len(server_config_surface()) == 23

    def test_suffix_is_the_upper_cased_field_name(self):
        assert all(c.suffix == c.name.upper() for c in server_config_surface())

    def test_suffixes_match_the_env_suffix_set(self):
        """The surface and the existing frozenset describe the same vars."""
        assert {
            c.suffix for c in server_config_surface()
        } == server_config_env_suffixes()

    def test_scalar_default_is_carried_through(self):
        host = next(c for c in server_config_surface() if c.name == "host")
        assert host.default == "127.0.0.1"

    def test_default_factory_is_resolved_to_a_value(self):
        """oidc_required_scopes uses default_factory=tuple; the surface reports ()."""
        scopes = next(
            c for c in server_config_surface() if c.name == "oidc_required_scopes"
        )
        assert scopes.default == ()

    def test_type_name_is_the_annotation_string(self):
        port = next(c for c in server_config_surface() if c.name == "port")
        assert port.type_name == "int"

    def test_records_are_frozen(self):
        record = server_config_surface()[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.help = "mutated"  # type: ignore[misc]

    def test_order_is_stable_under_hash_randomisation(self):
        """Guards the generated-output byte-stability failure mode.

        server_config_env_suffixes() returns a frozenset, whose iteration order
        varies between processes because CPython randomises string hashing. The
        surface must not inherit that.
        """
        program = (
            "from markdown_vault_mcp._config_surface import server_config_surface;"
            "print(','.join(c.suffix for c in server_config_surface()))"
        )
        outputs = set()
        for seed in ("1", "2", "3"):
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
            )
            outputs.add(result.stdout.strip())
        assert len(outputs) == 1

    def test_every_field_is_documented(self):
        undocumented = [c.name for c in server_config_surface() if not c.help]
        assert undocumented == []

    def test_every_field_is_tagged(self):
        untagged = [c.name for c in server_config_surface() if not c.tags]
        assert untagged == []

    def test_inferred_fields_are_the_expert_overrides(self):
        """AUTH_MODE is an expert override and the tool visibility lists are
        deployment-specific operator knobs, so the wizard offers no control
        for any of them."""
        assert [c.name for c in server_config_surface() if c.inferred] == [
            "tools_allow",
            "tools_deny",
            "auth_mode",
        ]

    def test_inferred_field_carries_no_wizard_hints(self):
        auth_mode = next(c for c in server_config_surface() if c.name == "auth_mode")
        assert auth_mode.wizard == {}

    def test_base_url_carries_several_tags(self):
        """A field can honestly belong to several documentation sections."""
        base_url = next(c for c in server_config_surface() if c.name == "base_url")
        assert set(base_url.tags) == {"server", "oidc", "apps"}

    def test_secret_fields_are_marked(self):
        secrets = {c.suffix for c in server_config_surface() if c.wizard.get("secret")}
        assert secrets == {
            "BEARER_TOKEN",
            "OIDC_CLIENT_SECRET",
            "OIDC_JWT_SIGNING_KEY",
        }

    def test_oidc_fields_share_the_oidc_tag(self):
        tagged = {c.suffix for c in server_config_surface() if "oidc" in c.tags}
        assert tagged == {
            "BASE_URL",
            "OIDC_CONFIG_URL",
            "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET",
            "OIDC_AUDIENCE",
            "OIDC_REQUIRED_SCOPES",
            "OIDC_ADVERTISED_SCOPES",
            "OIDC_JWT_SIGNING_KEY",
            "OIDC_VERIFY_ACCESS_TOKEN",
        }

    def test_kv_store_url_is_readme_prominent(self):
        """The consuming README shows a 3-row curated table; this is its core row."""
        kv = next(c for c in server_config_surface() if c.name == "kv_store_url")
        assert "readme" in kv.tags

    def test_defaults_are_unchanged_by_the_metadata_migration(self):
        """Behaviour guard: converting to field(default=...) must not alter values."""
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.transport == "stdio"
        assert config.oidc_required_scopes == ()
        assert config.oidc_verify_access_token is False
        assert config.bearer_default_subject == DEFAULT_BEARER_SUBJECT
        assert config.base_url is None

    def test_unknown_wizard_string_is_rejected(self):
        """A typo like "infered" must fail loudly, not crash on .items()."""

        @dataclass(frozen=True)
        class _BadWizard:
            oops: str = field(default="x", metadata={"wizard": "infered"})

        (bad,) = dataclasses.fields(_BadWizard)
        with pytest.raises(ValueError, match="must be a mapping of hints"):
            _config_field_from(bad)

    def test_non_mapping_non_string_wizard_is_rejected(self):
        """The class is "any non-mapping", not just a mistyped string."""

        @dataclass(frozen=True)
        class _BadWizard:
            oops: str = field(default="x", metadata={"wizard": ["inferred"]})

        (bad,) = dataclasses.fields(_BadWizard)
        with pytest.raises(ValueError, match="must be a mapping of hints"):
            _config_field_from(bad)

    def test_wizard_hints_use_only_documented_keys(self):
        """An unrecognised hint key (e.g. a typo) would be silently ignored."""
        documented = {"group", "when", "secret", "control"}
        offenders = {
            c.name: sorted(set(c.wizard) - documented)
            for c in server_config_surface()
            if set(c.wizard) - documented
        }
        assert offenders == {}

    def test_every_declared_default_is_unchanged(self):
        """Full 23-field guard; a spot check would miss a silent default change."""
        expected = {
            "transport": "stdio",
            "host": "127.0.0.1",
            "port": 8000,
            "shutdown_grace_s": 3,
            "base_url": None,
            "bearer_token": None,
            "oidc_config_url": None,
            "oidc_client_id": None,
            "oidc_client_secret": None,
            "oidc_audience": None,
            "oidc_required_scopes": (),
            "oidc_advertised_scopes": (),
            "oidc_jwt_signing_key": None,
            "oidc_verify_access_token": False,
            "kv_store_url": None,
            "event_store_url": None,
            "tasks_url": None,
            "app_domain": None,
            "tools_allow": (),
            "tools_deny": (),
            "auth_mode": None,
            "bearer_tokens_file": None,
            "bearer_default_subject": DEFAULT_BEARER_SUBJECT,
        }
        actual = {c.name: c.default for c in server_config_surface()}
        assert actual == expected
