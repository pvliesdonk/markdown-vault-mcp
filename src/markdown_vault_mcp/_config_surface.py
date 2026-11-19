"""Config-surface introspection: which env variables a config class reads.

Ported from ``fastmcp_pvl_core._config`` 10.2.0 (#2289): ``server_config_surface``
documents every :class:`ServerConfig` field, and ``domain_env_surface`` /
``domain_env_suffixes`` walk a config dataclass's ``from_env`` with :mod:`ast`
to list the variables it reads. ``scripts/gen_config_surface.py`` builds the
configuration reference, env examples and wizard spec from these.

The tree walk excludes :class:`ServerConfig` by class identity, so it imports
the same class ``ProjectConfig.server`` holds (#2288).
"""

from __future__ import annotations

import ast
import dataclasses
import typing
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from markdown_vault_mcp.config_sections.server import ServerConfig


@dataclass(frozen=True)
class ConfigField:
    """One env-configurable :class:`ServerConfig` field and its metadata.

    ``help`` deliberately does **not** restate a scalar default: ``default``
    carries it structurally, and a documentation generator renders both. The
    one exception is ``transport``, where naming the accepted literal values is
    the documentation.
    """

    suffix: str
    """Env suffix, i.e. the part after ``{PREFIX}_`` — e.g. ``BASE_URL``."""

    name: str
    """Python field name — e.g. ``base_url``."""

    type_name: str
    """The annotation as written, e.g. ``str | None``."""

    default: object
    """The declared default. ``default_factory`` fields report the built value."""

    help: str
    """One-or-two-sentence description. Empty when undocumented."""

    tags: tuple[str, ...]
    """Semantic tags used to route this field into documentation sections.

    Mostly semantic — core says *what* a field is about, not which file
    documents it. One exception: ``readme`` marks a field prominent
    enough for a landing-page summary table, which is a prominence
    signal rather than a topic. A field may carry several tags, and
    appearing in more than one destination is intentional.
    """

    inferred: bool
    """True when no wizard control is offered for this field.

    A wizard-presentation concern only. The variable remains
    operator-settable and MUST still appear in env references and
    ``.env.example`` — this flag is not a signal that the value cannot
    be set.
    """

    wizard: Mapping[str, object]
    """Presentation hints for a config wizard.

    Recognised keys:

    - ``group`` — name of the wizard's collapsed "Advanced" section.
      Absence means the field is a primary question.
    - ``when`` — the context in which the question applies: ``"server"``
      for HTTP deployments, or ``"oidc"`` / ``"bearer"`` for the
      matching auth selection (both of which also imply an HTTP
      deployment).
    - ``secret`` — ``True`` when the value must never appear in a
      shareable link.
    - ``control`` — ``"emit"`` when a routing select emits this value
      and the field gets no question of its own.

    Empty when :attr:`inferred` is True.
    """


def _config_field_from(f: dataclasses.Field[Any]) -> ConfigField:
    """Build one :class:`ConfigField` record from a ``ServerConfig`` field.

    Raises:
        ValueError: If ``metadata["wizard"]`` is neither a mapping of hints
            nor the recognised ``"inferred"`` shorthand — e.g. a typo like
            ``"infered"``, or an accidental list. Falling through silently
            would otherwise crash later on ``raw_wizard.items()``, since
            only a mapping has ``.items()``.
    """
    if f.default is not dataclasses.MISSING:
        default: object = f.default
    elif f.default_factory is not dataclasses.MISSING:
        default = f.default_factory()
    else:
        # A field with neither a default nor a default_factory — a required
        # var. No ``ServerConfig`` field hits this (they all have defaults), but
        # ``domain_env_surface`` reaches it for a domain sub-config's required
        # field; ``_domain_env_var_from`` then reports ``required=True``.
        default = None

    tags = tuple(str(tag) for tag in f.metadata.get("tags", ()))

    # ``metadata={"wizard": "inferred"}`` is the shorthand for a field with
    # no control; anything else is a mapping of presentation hints.
    raw_wizard = f.metadata.get("wizard", {})
    inferred = raw_wizard == "inferred"
    wizard: dict[str, object] = {}
    if not inferred and raw_wizard:
        if not isinstance(raw_wizard, Mapping):
            raise ValueError(
                f"ServerConfig.{f.name}: metadata['wizard'] must be a "
                f"mapping of hints or the string 'inferred'; got "
                f"{raw_wizard!r}."
            )
        wizard = {str(k): v for k, v in raw_wizard.items()}

    return ConfigField(
        suffix=f.name.upper(),
        name=f.name,
        type_name=f.type if isinstance(f.type, str) else str(f.type),
        default=default,
        help=str(f.metadata.get("help", "")),
        tags=tags,
        inferred=inferred,
        wizard=wizard,
    )


def server_config_surface() -> tuple[ConfigField, ...]:
    """Return every :class:`ServerConfig` env field, in declaration order.

    Declaration order is part of the contract: a consumer that renders this
    tuple produces byte-identical output on every run. Prefer this over
    :func:`server_config_env_suffixes`, which returns a ``frozenset`` whose
    iteration order varies between processes under hash randomisation.

    Covers the same 21 variables as :func:`server_config_env_suffixes`, adding
    each field's type, default, help text, tags, and wizard hints.
    """
    return tuple(_config_field_from(f) for f in dataclasses.fields(ServerConfig))


_ENV_READ_FUNCS = frozenset({"env", "env_int", "env_float"})


def _literal_env_read(n: ast.AST) -> tuple[str, ast.Call] | None:
    """``(suffix, call)`` when *n* is a literal env read, else ``None``.

    A literal env read is an unqualified ``env``/``env_int``/``env_float`` call
    whose suffix argument is a string literal — the one predicate
    :func:`_literal_env_reads` and :func:`_required_literal_reads` share.
    """
    if (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in _ENV_READ_FUNCS
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
        and isinstance(n.args[1].value, str)
    ):
        return n.args[1].value, n
    return None


def _literal_env_reads(node: ast.AST) -> list[tuple[str, int, int]]:
    """Return ``(suffix, lineno, col)`` for each literal env read under *node*.

    Walks *node* for unqualified ``env``/``env_int``/``env_float`` calls whose
    suffix argument is a string literal. Shared by :func:`domain_env_suffixes`
    and :func:`domain_env_surface` so both scans recognise exactly the same
    reads; a renamed import, an attribute-form call, or a variable/keyword-form
    suffix is invisible to either. Position is included so a consumer that wants
    deterministic ordering can sort by it; the suffix-only caller ignores it.
    """
    out: list[tuple[str, int, int]] = []
    for n in ast.walk(node):
        read = _literal_env_read(n)
        if read is not None:
            suffix, call = read
            out.append((suffix, call.lineno, call.col_offset))
    return out


def _required_literal_reads(node: ast.AST) -> frozenset[str]:
    """Suffixes of the literal env reads under *node* that pass ``required=True``.

    Only the literal keyword counts: ``required=flag`` is invisible here, as a
    variable-form suffix is to :func:`_literal_env_reads`.
    """
    out: set[str] = set()
    for n in ast.walk(node):
        read = _literal_env_read(n)
        if read is None:
            continue
        suffix, call = read
        if any(
            kw.arg == "required"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in call.keywords
        ):
            out.add(suffix)
    return frozenset(out)


def _from_env_tree(cls: type, *, caller: str) -> ast.Module:
    """Parse the source of ``cls.from_env`` for an env-read scan.

    Args:
        cls: A config dataclass exposing a ``from_env`` classmethod.
        caller: The public scan function, named in the error message.

    Returns:
        The parsed module holding the dedented ``from_env`` source.

    Raises:
        OSError: If the source cannot be read.
        TypeError: If ``from_env`` is not a Python function.
    """
    import inspect
    import textwrap

    try:
        src = textwrap.dedent(inspect.getsource(cls.from_env))  # type: ignore[attr-defined]
    except (OSError, TypeError) as exc:  # source unreadable / not a Python function
        # Re-raise preserving the original type (OSError vs TypeError) with
        # class context, so a type error isn't masqueraded as I/O.
        raise type(exc)(
            f"{caller}: cannot read source for {cls.__qualname__}.from_env: {exc}"
        ) from exc
    return ast.parse(src)


def _iter_env_configs(cls: type, visited: set[type], *, caller: str) -> Iterator[type]:
    """Yield *cls* and the sub-configs it composes that expose ``from_env``.

    The config-tree walk shared by :func:`domain_env_suffixes` and
    :func:`domain_env_surface`: depth-first, a class before its sub-configs,
    each class once (tracked in *visited*), skipping :class:`ServerConfig` and
    non-dataclass types. A class is yielded before its type hints are
    resolved, so a caller scanning each yielded class hits an unreadable
    ``from_env`` before a broken annotation.

    Args:
        cls: The config dataclass to walk from.
        visited: Classes already walked; updated in place.
        caller: The public scan function, named in the error message.

    Yields:
        Each config class in the tree that exposes ``from_env``.

    Raises:
        NameError: If a field annotation cannot be resolved.
    """
    if cls in visited or cls is ServerConfig or not dataclasses.is_dataclass(cls):
        return
    visited.add(cls)
    if hasattr(cls, "from_env"):
        yield cls
    try:
        hints = typing.get_type_hints(cls)
    except NameError as exc:
        raise NameError(
            f"{caller}: cannot resolve type hints for "
            f"{cls.__qualname__} — annotations must be importable at module "
            f"scope: {exc}"
        ) from exc
    for f in dataclasses.fields(cls):
        resolved = hints.get(f.name, f.type)
        for candidate in (resolved, *typing.get_args(resolved)):
            if (
                isinstance(candidate, type)
                and dataclasses.is_dataclass(candidate)
                and candidate is not ServerConfig
                and hasattr(candidate, "from_env")
            ):
                yield from _iter_env_configs(candidate, visited, caller=caller)


def domain_env_suffixes(config_cls: type) -> frozenset[str]:
    """Return the ``{PREFIX}_``-stripped env suffixes a domain config reads.

    AST-scans ``config_cls.from_env`` for ``env``/``env_int``/``env_float``
    calls whose suffix is a string literal, and recurses into the config's
    dataclass fields whose resolved type — or a type argument of it, e.g. the
    inner ``Section`` of ``Optional[Section]`` or the element type of
    ``list[Section]`` — is a dataclass exposing a ``from_env`` classmethod. So
    a config that delegates env reads to sub-config *sections* reports its full
    surface. The composed :class:`ServerConfig` field is excluded (its surface
    is :func:`server_config_env_suffixes`); a flat config with no sub-config
    fields scans only its own ``from_env``. Recursion is depth-first with a
    visited-set, so reference cycles and a type shared by two fields terminate
    and de-duplicate.

    Field types are resolved with :func:`typing.get_type_hints`, so the config
    and its sub-configs must be defined at module scope (the normal case); a
    resolution failure propagates rather than yielding a silently-incomplete
    set in a drift gate. Only unqualified ``env(prefix, "LITERAL")`` reads are
    seen — a renamed import (``env as _e``), an attribute-form call
    (``mod.env(...)``), or a variable/keyword-form suffix is invisible to the
    scan; keep reads in the literal ``env(prefix, "LITERAL")`` form.

    Only one level of :func:`typing.get_args` is expanded — ``list[Section]``
    (element type) and ``Section | None`` (direct union member) are traversed,
    but a nested container generic such as ``list[Optional[Section]]`` is not.

    Args:
        config_cls: The domain config dataclass; its ``from_env`` classmethod
            is the scan root.

    Returns:
        The frozenset of literal env suffixes read by ``config_cls.from_env``
        and its sub-config sections, excluding the :class:`ServerConfig` field.

    Raises:
        TypeError: If ``config_cls`` is not a dataclass.
        OSError: If a sub-config's ``from_env`` source cannot be read
            (compiled, frozen, or dynamically-defined class).
        NameError: If a field annotation cannot be resolved at
            :func:`typing.get_type_hints` time — the config or a sub-config
            is not defined at module scope, or contains a broken forward
            reference.
    """
    # ``is_dataclass`` is true for instances too; require the class itself so a
    # mistakenly-passed instance fails loudly rather than silently scanning.
    if not isinstance(config_cls, type) or not dataclasses.is_dataclass(config_cls):
        raise TypeError(
            f"domain_env_suffixes: expected a dataclass type, got {config_cls!r}"
        )

    caller = "domain_env_suffixes"
    found: set[str] = set()
    for cls in _iter_env_configs(config_cls, set(), caller=caller):
        tree = _from_env_tree(cls, caller=caller)
        found.update(suffix for suffix, _lineno, _col in _literal_env_reads(tree))
    return frozenset(found)


@dataclass(frozen=True)
class DomainEnvVar:
    """One env var a domain config reads, with provenance and field metadata.

    Carries a suffix a domain config (or a composed sub-config) reads, the class
    that read it, and — when resolvable — the metadata of the field it populates.
    This is the per-record counterpart to :func:`domain_env_suffixes`'s bare
    ``frozenset[str]``. The frozenset flattens every recursed suffix into one
    set and discards which class read it, so a consumer cannot attach per-field
    metadata to a suffix a composed sub-config contributed. Each record here
    keeps that provenance (:attr:`source`) and links the var to its declaring
    field, so the config-surface generator can document a composed sub-config's vars
    with the same help / tags / wizard hints and required-ness as a top-level
    field — the gap recorded in the motivating issue — without flattening the
    config.
    """

    suffix: str
    """Env suffix as read — the part after ``{PREFIX}_``, e.g.
    ``TRANSFER_TTL_DEFAULT_S``. For a composed *section* this carries the
    section's own prefix, so it is generally **not** ``name.upper()``."""

    source: str
    """``__qualname__`` of the (sub-)config class whose ``from_env`` reads this
    var — e.g. ``TransferConfig``. This is the provenance the flat frozenset
    discards; a suffix read by two different classes yields one record per
    class."""

    name: str | None
    """Dataclass field the read populates — e.g. ``ttl_default_s`` — or ``None``
    when the read is not tied to a single constructor field (a throwaway read,
    or a value assembled from several reads). When ``None`` the metadata fields
    below carry neutral placeholders and must not be treated as authoritative;
    the var still appears so the surface never loses a suffix the frozenset had.
    """

    type_name: str | None
    """The field's annotation as written, e.g. ``float``. ``None`` when
    :attr:`name` is ``None``."""

    default: object
    """The field's declared default (a ``default_factory`` field reports the
    built value). ``None`` when :attr:`name` is ``None``, and when ``from_env``
    reads the var with a literal ``required=True``: a field default that exists
    only to satisfy dataclass ordering is not an operator default (see
    :attr:`required`)."""

    help: str
    """The field's ``metadata["help"]``. Empty when undocumented or unresolved."""

    tags: tuple[str, ...]
    """The field's ``metadata["tags"]``. Empty when untagged or unresolved."""

    inferred: bool
    """True when the field carries the ``"inferred"`` wizard shorthand (no
    control offered). ``False`` when :attr:`name` is ``None``."""

    wizard: Mapping[str, object]
    """The field's wizard presentation hints. Empty for inferred or unresolved
    vars."""

    required: bool
    """True when an operator must set the var: its field has no default, or
    ``from_env`` reads it with a literal ``required=True`` (see :func:`env`).
    For a ``required=True`` read :attr:`default` is ``None`` — a field default
    that exists only to satisfy dataclass ordering is not an operator default.
    A read that maps to no field (:attr:`name` ``None``) is required only
    through ``required=True``."""


def _domain_env_var_from(
    source: type,
    suffix: str,
    f: dataclasses.Field[Any] | None,
    *,
    required_by_read: bool = False,
) -> DomainEnvVar:
    """Build one :class:`DomainEnvVar`.

    When ``f`` is ``None`` the read could not be tied to a constructor field, so
    the metadata fields carry neutral placeholders (the var is still emitted so
    the surface is a strict superset of :func:`domain_env_suffixes`). Otherwise
    the field's metadata is extracted via :func:`_config_field_from` — the same
    reader ``server_config_surface`` uses, so help/tags/wizard parsing (and its
    validation) live in one place.

    ``required_by_read`` is true when ``from_env`` reads *suffix* with a literal
    ``required=True``: the record is then required whatever the field declares,
    and its default is ``None`` — a default that exists only to satisfy
    dataclass field ordering is not an operator default.
    """
    if f is None:
        return DomainEnvVar(
            suffix=suffix,
            source=source.__qualname__,
            name=None,
            type_name=None,
            default=None,
            help="",
            tags=(),
            inferred=False,
            wizard={},
            required=required_by_read,
        )
    cf = _config_field_from(f)
    no_default = (
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    )
    return DomainEnvVar(
        suffix=suffix,
        source=source.__qualname__,
        name=cf.name,
        type_name=cf.type_name,
        default=None if required_by_read else cf.default,
        help=cf.help,
        tags=cf.tags,
        inferred=cf.inferred,
        wizard=cf.wizard,
        required=no_default or required_by_read,
    )


def _keyword_env_read(kw: ast.keyword) -> tuple[str, str] | None:
    """Return ``(suffix, field)`` if *kw* reads exactly one literal env suffix.

    ``None`` for a ``**kwargs`` splat (no field name) and for a value with
    zero or several literal reads, which is ambiguous.
    """
    if kw.arg is None:
        return None
    literals = {lit for lit, _, _ in _literal_env_reads(kw.value)}
    if len(literals) != 1:
        return None
    return next(iter(literals)), kw.arg


def _ctor_field_by_suffix(tree: ast.AST, cls: type) -> dict[str, str]:
    """Map a literal suffix to the ``cls(...)`` keyword it is read into.

    Only a keyword whose value expression contains exactly one literal env
    read is mapped; zero or several is ambiguous and left unmapped. If the
    same suffix appears in two keywords (unusual — a section's suffixes are
    distinct), the first in source order wins and the later field goes
    unmapped, matching the frozenset's de-duplication of that suffix.
    """
    ctor_names = {"cls", cls.__name__}
    mapping: dict[str, str] = {}
    for n in ast.walk(tree):
        if not (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in ctor_names
        ):
            continue
        for kw in n.keywords:
            pair = _keyword_env_read(kw)
            if pair is not None:
                mapping.setdefault(*pair)
    return mapping


def _class_env_reads(
    tree: ast.AST, cls: type
) -> list[tuple[str, dataclasses.Field[Any] | None, bool]]:
    """Resolve each literal env read in *cls*'s ``from_env`` to its field.

    Args:
        tree: The parsed ``from_env`` source of *cls*.
        cls: The config dataclass that source belongs to.

    Returns:
        One ``(suffix, field, required_by_read)`` per distinct suffix, in
        source order of its first read. ``field`` is ``None`` when neither
        resolution tier of :func:`domain_env_surface` ties the read to one.
    """
    field_of = _ctor_field_by_suffix(tree, cls)
    required_reads = _required_literal_reads(tree)
    fields_by_name = {f.name: f for f in dataclasses.fields(cls)}
    # Field-name fallback: a field's env var is ``{PREFIX}_{NAME.upper()}``
    # by convention, so a read this class does not tie to a constructor
    # keyword (consumed via a local, or assembled from several reads) still
    # resolves to the field whose ``name.upper()`` equals the suffix.
    fields_by_suffix = {f.name.upper(): f for f in dataclasses.fields(cls)}
    reads: list[tuple[str, dataclasses.Field[Any] | None, bool]] = []
    local_seen: set[str] = set()
    for suffix, _lineno, _col in sorted(
        _literal_env_reads(tree), key=lambda t: (t[1], t[2])
    ):
        if suffix in local_seen:
            continue
        local_seen.add(suffix)
        fname = field_of.get(suffix)
        if fname is not None:
            f = fields_by_name.get(fname)
        else:
            f = fields_by_suffix.get(suffix)
        reads.append((suffix, f, suffix in required_reads))
    return reads


def domain_env_surface(config_cls: type) -> tuple[DomainEnvVar, ...]:
    """Return :class:`DomainEnvVar` records for the env vars a domain config reads.

    The metadata-carrying counterpart of :func:`domain_env_suffixes`: it walks
    the same scan — literal ``env``/``env_int``/``env_float`` reads in
    ``config_cls.from_env`` plus every composed sub-config's ``from_env``,
    excluding the :class:`ServerConfig` field — but returns one record per var
    instead of a flat ``frozenset[str]``. Each record keeps the sub-config it
    came from (:attr:`DomainEnvVar.source`) and, when the read populates a
    constructor field, that field's metadata and required-ness, so a composed
    sub-config's vars can be documented like a top-level field's without
    flattening the config.

    **Suffix→field resolution** happens in two tiers. First, each ``cls(...)`` /
    ``{ClassName}(...)`` construction in ``from_env`` is inspected: a keyword
    argument whose value expression contains exactly one literal env read links
    that field to that suffix. This tier is what resolves a *section* field,
    whose suffix carries the section's own prefix (a ``ttl_default_s`` field read
    as ``TRANSFER_TTL_DEFAULT_S``), so ``name.upper()`` alone cannot identify it.

    Second, a read the first tier does not tie to a keyword — one consumed via
    an intermediate local (``x = parse_bool(env(prefix, "X")); cls(x=x)``), or a
    value assembled from several reads — falls back to the field whose
    ``name.upper()`` equals the suffix, since a field's env var is
    ``{PREFIX}_{NAME.upper()}`` by convention. This restores the metadata the
    pre-4.6 field-name resolution attached and covers the common shape where a
    value needs parsing or a fallback before construction.

    A suffix that neither tier resolves — a throwaway ``_ = env(...)``, or a
    *section* field read via a local (its prefixed suffix is not ``name.upper()``
    of any field, and the keyword did not carry the literal) — still yields a
    record, with :attr:`DomainEnvVar.name` ``None`` and neutral metadata, so no
    suffix the frozenset carried is dropped. Keep a section field's read inline
    in its constructor keyword (``field=env(prefix, "LITERAL")``) so its metadata
    is attached; a top-level field resolves either way.

    Records are ordered deterministically: depth-first over the config tree
    (a class's own reads before its sub-configs'), and within a class by the
    source position of each read — so a consumer that renders this tuple
    produces byte-stable output, as :func:`server_config_surface` does.
    ``{v.suffix for v in domain_env_surface(cls)}`` equals
    ``domain_env_suffixes(cls)``.

    **Required-ness** comes from the field (no default at all) or from the read:
    a literal ``required=True`` keyword on the ``env``/``env_int``/``env_float``
    call makes the record required with default ``None``, even for a read that
    maps to no field.

    Args:
        config_cls: The domain config dataclass; its ``from_env`` classmethod
            is the scan root.

    Returns:
        A tuple of :class:`DomainEnvVar` records, one per ``(source, suffix)``.

    Raises:
        TypeError: If ``config_cls`` is not a dataclass, or a sub-config's
            ``from_env`` is not a readable Python function.
        OSError: If a sub-config's ``from_env`` source cannot be read.
        NameError: If a field annotation cannot be resolved at
            :func:`typing.get_type_hints` time (the config or a sub-config is
            not defined at module scope, or has a broken forward reference).
        ValueError: If a resolved field's ``metadata["wizard"]`` is malformed
            (see :func:`_config_field_from`).
    """
    if not isinstance(config_cls, type) or not dataclasses.is_dataclass(config_cls):
        raise TypeError(
            f"domain_env_surface: expected a dataclass type, got {config_cls!r}"
        )

    caller = "domain_env_surface"
    records: list[DomainEnvVar] = []
    seen: set[tuple[str, str]] = set()
    for cls in _iter_env_configs(config_cls, set(), caller=caller):
        tree = _from_env_tree(cls, caller=caller)
        for suffix, f, required_by_read in _class_env_reads(tree, cls):
            key = (cls.__qualname__, suffix)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                _domain_env_var_from(cls, suffix, f, required_by_read=required_by_read)
            )
    return tuple(records)
