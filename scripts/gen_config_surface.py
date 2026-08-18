#!/usr/bin/env python3
"""Generate the config-surface artifacts from a single source of truth.

Reads a rendered project's ``.copier-answers.yml`` and its
``config-presentation.yml``, then merges four provenance sources into one
declaration-ordered variable list:

- ``core`` — from ``fastmcp_pvl_core.server_config_surface()``, which owns
  the help text and tags for every ``ServerConfig`` field.
- ``template`` / ``external`` — from ``config-presentation.yml``, the
  template-owned vars core does not know about.
- ``domain`` — reserved for a project's own ``ProjectConfig`` fields.

This script is template-owned and ships byte-identical to every project, so
it discovers the project root and its dependency floor at runtime rather
than hard-coding a package name or version. ``config-presentation.yml``
ships the same way; ``{PREFIX}`` in it is substituted with the project's
``env_prefix`` at generation time, not by Jinja.

copier's ``_tasks`` run before any virtualenv exists for the freshly
rendered project, so this script cannot assume ``fastmcp-pvl-core`` or
PyYAML are importable; ``ensure_core_available`` re-execs itself under
``uv run --no-project`` with both pinned ad hoc when the import fails.

Usage::

    python scripts/gen_config_surface.py           # Generate config artifacts
    python scripts/gen_config_surface.py --check    # Verify they are up-to-date (offline)
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
    from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Var:
    """One config variable, from whichever provenance produced it."""

    name: str  # full env var name, e.g. "SCHOLAR_MCP_BASE_URL" or "FASTMCP_LOG_LEVEL"
    suffix: str | None  # part after "{PREFIX}_", or None for unprefixed vars
    provenance: str  # "core" | "template" | "external" | "domain"
    type_name: str
    default: object
    help: str
    tags: tuple[str, ...]
    inferred: bool
    wizard: Mapping[str, object]
    example: str | None = None  # shown as the value when `default` is null


# Sentinel for "this domain field has neither a `default` nor a
# `default_factory` at all" — a genuinely required field — distinct from a
# real default of `None` (`x: str | None = None`, an ordinary optional
# field). `_discover_domain_vars` used to collapse both cases to
# `default=None` on the `Var`, which made `_is_required`'s domain fallback
# ("no default means required") wrongly mark every `... | None = None`
# domain field as required. Every other `Var.default` consumer
# (`_format_default`, `_format_value`, `_is_empty_default`/`_md_default_cell`)
# treats this sentinel exactly as it already treated `None` — it changes
# ONLY the required-ness signal, never env-file, wizard, or markdown-default
# output. Never leaked to a caller outside this module: every field is
# either a real value, `None`, or this sentinel, and every renderer that
# touches `default` is listed above.
_NO_DEFAULT = object()

# Rank order for the provenance merge; vars are ordered by this rank first,
# then by declaration order within each provenance.
_PROVENANCE_ORDER = ("core", "template", "external", "domain")

# Captures the full version constraint (e.g. ">=4.11.0,<5"), not just the
# floor: the bootstrap re-exec must resolve the SAME version `uv sync` will
# resolve for the project venv, or copy-time generation and a later venv
# regeneration disagree the moment a core release changes any help text
# (#335). A bare `==floor` pin did exactly that.
_CORE_CONSTRAINT_RE = re.compile(r"fastmcp-pvl-core(?:\[[^\]]*\])?\s*([><=!~][^\"']*)")


def _clean_help(help_text: str) -> str:
    """Strip RST inline-literal markup (````word````) down to a single backtick.

    Core's help text is RST-flavoured prose; a double-backtick reads as noise
    in a plain-text env file comment. Applied once, here, at the point every
    Var's ``help`` is set — so every consumer (both env files here and the
    wizard spec's use of ``var.help``) inherits already-clean text instead of
    each needing to remember to clean it again.
    """
    return help_text.replace("``", "`")


def _require_env_prefix(answers: Mapping[str, object]) -> str:
    """Return ``answers["env_prefix"]``, or raise the same `SystemExit` every caller expects.

    Both `collect_vars` and `write_artifacts` need this value before they can
    do anything else; sharing one guard means a malformed answers file always
    fails the same deliberate way, never with a bare `KeyError` from whichever
    caller forgot to check first.
    """
    if "env_prefix" not in answers:
        raise SystemExit(
            "ERROR: 'env_prefix' missing from .copier-answers.yml — a project "
            "rendered by this template always answers that question."
        )
    return str(answers["env_prefix"])


# ---------------------------------------------------------------------------
# Answers + presentation config
# ---------------------------------------------------------------------------


def load_answers(project_root: Path | str) -> dict[str, object]:
    """Read `.copier-answers.yml` from a rendered project."""
    import yaml

    project_root = Path(project_root)
    answers_path = project_root / ".copier-answers.yml"
    if not answers_path.exists():
        raise SystemExit(
            f"ERROR: {answers_path} not found — this script must be run from "
            "the root of a project rendered by copier (missing "
            ".copier-answers.yml)."
        )
    data = yaml.safe_load(answers_path.read_text(encoding="utf-8"))
    return data or {}


def _substitute_prefix(obj: Any, env_prefix: str) -> Any:
    """Recursively replace the literal token ``{PREFIX}`` with *env_prefix*.

    Applies to dict keys as well as values — ``wizard_routing`` options emit
    dicts keyed by ``"{PREFIX}_TRANSPORT"``.
    """
    if isinstance(obj, str):
        return obj.replace("{PREFIX}", env_prefix)
    if isinstance(obj, dict):
        return {
            _substitute_prefix(k, env_prefix): _substitute_prefix(v, env_prefix)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_substitute_prefix(item, env_prefix) for item in obj]
    return obj


def load_presentation(project_root: Path | str, env_prefix: str) -> dict[str, Any]:
    """Load `config-presentation.yml` with `{PREFIX}` substituted."""
    import yaml

    project_root = Path(project_root)
    presentation_path = project_root / "config-presentation.yml"
    if not presentation_path.exists():
        raise SystemExit(f"ERROR: {presentation_path} not found.")
    raw = yaml.safe_load(presentation_path.read_text(encoding="utf-8"))
    return _substitute_prefix(raw, env_prefix)


# ---------------------------------------------------------------------------
# Domain discovery
# ---------------------------------------------------------------------------


def _load_domain_presentation(
    presentation_root: Path, env_prefix: str
) -> dict[str, Any]:
    """Load `config-presentation.domain.yml`, tolerating its absence.

    Unlike `load_presentation`'s template-owned file (mandatory in every
    render), this one is *seeded* — a downstream project owns and edits it.
    Its ``vars`` feed `collect_vars`, its ``wizard_routing``/
    ``wizard_guards`` feed `render_wizard_spec`, and its ``files`` overlay
    the template's artifact map (see `_merged_files`). A missing file means
    "nothing manually declared" rather than a configuration error.
    """
    import yaml

    domain_path = presentation_root / "config-presentation.domain.yml"
    if not domain_path.exists():
        return {"vars": []}
    raw = yaml.safe_load(domain_path.read_text(encoding="utf-8")) or {}
    return _substitute_prefix(raw, env_prefix)


def _import_project_config(project_root: Path, python_module: str) -> type | None:
    """Import ``{python_module}.config.ProjectConfig``, or ``None`` if it can't be.

    A freshly rendered project has no dependencies installed yet, and this
    generator's own unit tests use fixture projects with no config module at
    all — both are legitimate "nothing to discover" cases, not errors: the
    module (or its parent package) genuinely does not exist, so importing it
    raises a ``ModuleNotFoundError`` naming ``{python_module}.config`` or
    ``{python_module}`` itself, and that specific case returns ``None``
    silently. Domain discovery is best-effort enrichment that must never turn
    an unrelated project's problem into a hard failure of this generator, so
    every other exception — a ``ModuleNotFoundError`` for some *other*
    missing module (a broken third-party import reached via ``config.py`` or
    ``__init__.py``), a mid-edit ``SyntaxError``, an ``AttributeError`` from a
    missing ``ProjectConfig``, or anything else the module's own top level
    raises — also returns ``None``, but only after printing a warning naming
    the exception class and message to stderr. Silently returning ``None``
    for *every* exception (the previous behaviour) let a broken ``config.py``
    make every domain var vanish from the generated artifacts with no
    diagnostic at all, most realistically during copier-update's bootstrap
    re-exec, which has none of the project's own dependencies installed.

    Only ``sys.path`` is restored before returning. ``sys.modules`` is
    deliberately left alone here: `typing.get_type_hints` (used inside
    `fastmcp_pvl_core.domain_env_surface`, which the caller runs against the
    returned class right after this) resolves a class's annotations via
    ``sys.modules[cls.__module__]``, so popping the module before that scan
    runs would turn every annotation lookup into a `NameError`. The caller
    owns popping ``sys.modules`` once it is done using the returned class.

    ``sys.dont_write_bytecode`` is forced ``True`` for the duration of the
    import and restored afterwards (never left permanently changed, so a
    caller that already runs with bytecode writing enabled for its own
    reasons keeps that behaviour once this returns). Without it, importing
    the project's own package writes ``__pycache__/*.pyc`` files into
    ``project_root/src`` — timestamp-embedding, so two otherwise-identical
    renders produce byte-different ``.pyc`` files and template-ci's
    render-twice-and-diff idempotence check (this generator runs as a
    copier `_tasks` entry, before any comparison) would fail on generated
    cache files that have nothing to do with this generator's own output.
    """
    src_dir = project_root / "src"
    if not src_dir.is_dir():
        return None

    added_to_path = str(src_dir) not in sys.path
    if added_to_path:
        sys.path.insert(0, str(src_dir))
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module = importlib.import_module(f"{python_module}.config")
        return module.ProjectConfig
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name in (
            python_module,
            f"{python_module}.config",
        ):
            return None
        print(
            f"WARNING: importing {python_module}.config failed: "
            f"{exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if added_to_path:
            sys.path.remove(str(src_dir))


def _discover_domain_vars(
    project_root: Path, env_prefix: str, answers: Mapping[str, object]
) -> tuple[Var, ...]:
    """Auto-discover domain vars from the project's own ``ProjectConfig``.

    Scans the config tree via ``fastmcp_pvl_core.domain_env_surface`` (core
    ≥ 4.6.1), which AST-walks ``from_env`` in ``ProjectConfig`` *and every
    composed sub-config* and returns one metadata-carrying record per read —
    so a var contributed by a composed section documents with the same
    help/tags/wizard hints and required-ness as a top-level field, without
    flattening the config. Core 4.6.1 also resolves a top-level field read
    into a local before construction (``x = parse(env(...)); cls(x=x)``) to
    that field by name, so its metadata survives whether or not the read is
    inline; a *section* field must still be read inline in its constructor
    keyword to carry metadata (core cannot unambiguously strip the section
    prefix). This is best-effort enrichment, not a required
    provenance source: a fresh render has no domain fields (and often no
    venv yet to even import its own package), so `_import_project_config`
    treats the module genuinely not existing as silent "nothing to
    discover" — but any other import-time failure (a broken third-party
    import, a mid-edit `SyntaxError`) prints a warning there instead of
    vanishing. A failure *during the scan itself* (the module imported fine
    but `domain_env_surface` couldn't resolve it — e.g. a type hint
    referencing something not importable at module scope) is not swallowed
    silently either: the scan's own contract is that such a failure must
    propagate rather than yield a silently-incomplete set, so this prints a
    warning naming the exception and returns no domain vars, rather than
    letting `--check` report "up to date" while every domain var is
    actually missing.

    Every discovered var is tagged ``domain`` (in addition to whatever tags
    its field metadata declares) so it always lands in a file spec's
    ``tags: [domain]`` section regardless of the field author's own tag
    choices. Ordering follows the scan's own deterministic contract:
    depth-first over the config tree, a class's own reads (in source
    position) before its sub-configs'. A suffix read by more than one class
    yields one Var — the first record wins, matching how the pre-4.6.0
    frozenset de-duplicated it.

    Every ``sys.modules`` entry gained while importing and introspecting the
    project's config module — including any side-effect submodules it pulls
    in along the way — is removed before returning, once every use of the
    class is finished. That cleanup runs on every exit path, including the
    early returns above, so a later call against a different project that
    happens to share the same module name never resolves against a stale
    cached package from an earlier call in the same process.
    """
    python_module = answers.get("python_module")
    if not python_module:
        return ()
    python_module = str(python_module)

    modules_before = frozenset(sys.modules)
    try:
        project_config_cls = _import_project_config(project_root, python_module)
        if project_config_cls is None:
            return ()

        from fastmcp_pvl_core import domain_env_surface

        try:
            records = domain_env_surface(project_config_cls)
        except Exception as exc:
            print(
                f"WARNING: domain env-var discovery failed for "
                f"{python_module}.config: {exc.__class__.__name__}: {exc}",
                file=sys.stderr,
            )
            return ()

        discovered: list[Var] = []
        seen_suffixes: set[str] = set()
        for record in records:
            if record.suffix in seen_suffixes:
                continue
            seen_suffixes.add(record.suffix)
            if record.name is None or record.required:
                # Tied to no constructor field (no default knowable) or a
                # field with neither `default` nor `default_factory`: both
                # mean "no default declared". Must NOT collapse to `None` —
                # that would be indistinguishable from a real
                # `x: str | None = None` optional field once both reach
                # `_is_required`'s domain fallback.
                default = _NO_DEFAULT
            else:
                default = record.default
            tags = tuple(dict.fromkeys((*record.tags, "domain")))
            discovered.append(
                Var(
                    name=f"{env_prefix}_{record.suffix}",
                    suffix=record.suffix,
                    provenance="domain",
                    type_name=record.type_name or "str",
                    default=default,
                    help=_clean_help(record.help),
                    tags=tags,
                    inferred=record.inferred,
                    wizard=dict(record.wizard),
                )
            )
        return tuple(discovered)
    finally:
        for name in set(sys.modules) - modules_before:
            del sys.modules[name]


# Helpers whose literal suffix argument the core AST scan
# (`fastmcp_pvl_core.domain_env_surface`) recognizes inside `from_env`.
_SCANNED_READ_HELPERS = frozenset({"env", "env_int", "env_float"})

# A string literal shaped like an env-var suffix or full name: all-caps with
# at least one underscore. Single all-caps words ("DEBUG", "INFO") stay out —
# they are far more often log levels or modes than env suffixes.
_SUFFIX_LITERAL_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def _unscanned_from_env_reads(
    project_root: Path, python_module: str
) -> tuple[tuple[str, str, int], ...]:
    """Suffix-shaped string literals `from_env` passes to unscanned calls.

    The core scan only recognizes literal ``env``/``env_int``/``env_float``
    calls; a read through any other helper (``opt_int(prefix,
    "MAX_CHUNK_CHARS")``, ``os.environ.get(...)``) is invisible to it, and
    the var silently vanishes from every generated artifact while
    ``--check`` still passes — the generator's output is self-consistent
    with what the scan saw. This is the guard for that gap: parse the
    project's ``config.py`` *source* (no import — it works even when the
    module can't be imported), find ``from_env``, and return every
    ``(literal, callee_name, lineno)`` where a suffix-shaped string literal
    is an argument to a call whose callee is not one of the scanned
    helpers. `collect_vars` filters the candidates against everything that
    IS documented before failing, so a helper read of a var that some
    provenance already declares never fires.

    Purely syntactic best-effort: a missing or unparsable ``config.py``
    returns nothing (the import path already warns about a broken module),
    and only ``from_env`` methods are inspected — every class-level one in
    the file, since a composed sub-config section's ``from_env`` is scanned
    (and so silently droppable) exactly like ``ProjectConfig``'s; reads
    elsewhere are the domain YAML's documented territory.
    """
    config_path = project_root / "src" / python_module / "config.py"
    if not config_path.exists():
        return ()
    try:
        tree = ast.parse(config_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return ()

    from_envs = [
        item
        for class_node in ast.walk(tree)
        if isinstance(class_node, ast.ClassDef)
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "from_env"
    ]
    return tuple(
        candidate
        for from_env in from_envs
        for candidate in _suffix_reads_in_function(from_env)
    )


def _call_callee_name(node: ast.Call) -> str:
    """Best-effort callee name for a ``Call``: a bare name, an attribute's
    tail (``mod.env`` -> ``env``), or ``"<call>"`` for anything else."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return "<call>"


def _suffix_reads_in_function(
    from_env: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[tuple[str, str, int]]:
    """Yield ``(literal, callee, lineno)`` for every suffix-shaped string
    literal passed to a non-scanned call anywhere inside *from_env*."""
    for node in ast.walk(from_env):
        if not isinstance(node, ast.Call):
            continue
        callee = _call_callee_name(node)
        if callee in _SCANNED_READ_HELPERS:
            continue
        args = [*node.args, *(kw.value for kw in node.keywords)]
        for arg in args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _SUFFIX_LITERAL_RE.match(arg.value)
            ):
                yield arg.value, callee, node.lineno


def _assert_no_unscanned_from_env_reads(
    project_root: Path,
    python_module: str,
    env_prefix: str,
    collected: Sequence[Var],
    scan_ignore: Collection[str],
) -> None:
    """`SystemExit` when `from_env` reads a var no provenance documents.

    A candidate survives only when its literal matches no collected var's
    suffix or full name, is not the project's own ``env_prefix`` (a helper
    taking the prefix as a literal first argument would otherwise flag the
    prefix itself), and is not listed under the domain YAML's
    ``scan_ignore`` — so the failure names exactly the reads that would
    otherwise drop out of every artifact with no red gate anywhere.
    """
    candidates = _unscanned_from_env_reads(project_root, python_module)
    if not candidates:
        return
    known_suffixes = {var.suffix for var in collected if var.suffix}
    known_names = {var.name for var in collected}
    ignored = set(scan_ignore)
    offenders = [
        (literal, callee, lineno)
        for literal, callee, lineno in candidates
        if literal != env_prefix
        and literal not in known_suffixes
        and literal not in known_names
        and literal not in ignored
    ]
    if not offenders:
        return
    lines = "\n".join(
        f"  - {literal!r} passed to {callee}() (config.py line {lineno})"
        for literal, callee, lineno in offenders
    )
    raise SystemExit(
        f"ERROR: {python_module}.config from_env contains env-var reads the "
        f"AST scan cannot see:\n{lines}\n"
        "The scan only recognizes literal env()/env_int()/env_float() calls, "
        "so these vars would silently vanish from every generated artifact. "
        "Either read the var via env()/env_int()/env_float() inside "
        "from_env, declare it in config-presentation.domain.yml under "
        "vars:, or — if the literal is not an env var at all — list it "
        "under scan_ignore: in that same file."
    )


# ---------------------------------------------------------------------------
# Provenance merge
# ---------------------------------------------------------------------------


def _presentation_root(project_root: Path) -> Path:
    """Where `config-presentation*.yml` live for *project_root*.

    Both presentation files ship byte-identical, so they live at
    *project_root* in a real rendered project. Fall back to the copy
    co-located with this script (the template repo root, when running the
    template's own tests) so callers work against a project_root that only
    has the parts a caller actually needs — e.g. a bare `.copier-answers.yml`
    in unit tests.
    """
    project_root = Path(project_root)
    if (project_root / "config-presentation.yml").exists():
        return project_root
    return _project_root()


def _apply_wizard_hint_overrides(
    collected: list[Var], presentation: Mapping[str, Any]
) -> list[Var]:
    """Apply the presentation's `wizard_hints` override map to *collected*.

    Keyed by full var name like `examples` — the correction lever for a var
    whose provenance owns its hint but got it wrong (core 4.10.1 ships
    TOOLS_ALLOW/TOOLS_DENY as ``"wizard": "inferred"``, though nothing
    derives their values — see #321/#322). An entry replaces the var's wizard
    mapping wholesale and clears the `inferred` shorthand; content is
    validated later by `_validate_wizard_hint`, exactly like a hint from any
    other source. An entry naming a var no provenance collected fails loudly
    rather than silently doing nothing: the map exists to paper over upstream
    metadata until it is fixed there, and a stale entry outliving that fix
    (or a typo never matching anything) is a bug in config-presentation.yml.
    """
    hints_map: dict[str, Any] = presentation.get("wizard_hints", {}) or {}
    if not hints_map:
        return collected
    unknown_hints = sorted(set(hints_map) - {v.name for v in collected})
    if unknown_hints:
        raise SystemExit(
            "ERROR: config-presentation.yml wizard_hints names var(s) no "
            f"provenance collected: {', '.join(unknown_hints)}. Remove "
            "the stale entry, or fix the name."
        )
    return [
        dataclasses.replace(v, wizard=dict(hints_map[v.name]), inferred=False)
        if v.name in hints_map
        else v
        for v in collected
    ]


def collect_vars(
    project_root: Path | str, answers: Mapping[str, object]
) -> tuple[Var, ...]:
    """Merge core + presentation-declared vars into one provenance-ordered tuple.

    Ordering is contractual: template-ci renders the template twice and diffs
    the results, so the merge must be deterministic across processes. Core
    vars come from ``server_config_surface()``, which returns a
    declaration-ordered tuple for exactly this reason — never iterate a
    ``set``/``frozenset`` here.
    """
    from fastmcp_pvl_core import server_config_surface

    project_root = Path(project_root)
    env_prefix = _require_env_prefix(answers)
    presentation_root = _presentation_root(project_root)
    presentation = load_presentation(presentation_root, env_prefix)

    collected: list[Var] = [
        Var(
            name=f"{env_prefix}_{field.suffix}",
            suffix=field.suffix,
            provenance="core",
            type_name=field.type_name,
            default=field.default,
            help=_clean_help(field.help),
            tags=tuple(field.tags),
            inferred=field.inferred,
            wizard=dict(field.wizard),
        )
        for field in server_config_surface()
    ]

    prefix_marker = f"{env_prefix}_"
    for raw in presentation.get("vars", ()):
        when_answer = raw.get("when_answer")
        if when_answer is not None and not answers.get(when_answer):
            continue
        name = raw["name"]
        suffix = name[len(prefix_marker) :] if name.startswith(prefix_marker) else None
        collected.append(
            Var(
                name=name,
                suffix=suffix,
                provenance=raw["provenance"],
                type_name=raw["type_name"],
                default=raw.get("default"),
                help=_clean_help(raw["help"]),
                tags=tuple(raw.get("tags", ())),
                inferred=bool(raw.get("inferred", False)),
                wizard=dict(raw.get("wizard", {})),
            )
        )

    domain_presentation = _load_domain_presentation(presentation_root, env_prefix)
    for raw in domain_presentation.get("vars", ()):
        when_answer = raw.get("when_answer")
        if when_answer is not None and not answers.get(when_answer):
            continue
        name = raw["name"]
        suffix = name[len(prefix_marker) :] if name.startswith(prefix_marker) else None
        collected.append(
            Var(
                name=name,
                suffix=suffix,
                provenance=raw.get("provenance", "domain"),
                type_name=raw["type_name"],
                # `raw.get("default", _NO_DEFAULT)`, not `raw.get("default")`:
                # this is the manual escape hatch's half of the same
                # required-ness signal `_discover_domain_vars` produces for
                # the AST-scanned path. Omitting `default:` here must mean
                # "no default declared at all" (required), matching a
                # dataclass field with neither `default` nor
                # `default_factory`; an explicit `default: null` still means
                # a real default of `None` (optional), exactly as it does for
                # the AST-scanned path.
                default=raw.get("default", _NO_DEFAULT),
                help=_clean_help(raw["help"]),
                # Same "domain" tag `_discover_domain_vars` adds to every
                # AST-discovered var, so a var declared here — the "the AST
                # scan can't see it" escape hatch documented in
                # `config-migration.md.jinja` — always matches the Domain
                # section too, regardless of what the author did or didn't
                # list under `tags:`.
                tags=tuple(dict.fromkeys((*raw.get("tags", ()), "domain"))),
                inferred=bool(raw.get("inferred", False)),
                wizard=dict(raw.get("wizard", {})),
            )
        )

    collected.extend(_discover_domain_vars(project_root, env_prefix, answers))

    # Placeholder examples for vars whose real `default` is null — keyed by
    # full var name so a core var (whose help/tags/default this template does
    # not own) can still get one, without redeclaring the whole var.
    examples_map: dict[str, str] = presentation.get("examples", {}) or {}
    if examples_map:
        collected = [
            dataclasses.replace(v, example=examples_map[v.name])
            if v.name in examples_map
            else v
            for v in collected
        ]

    collected = _apply_wizard_hint_overrides(collected, presentation)

    seen_names: dict[str, str] = {}
    for var in collected:
        prior_provenance = seen_names.get(var.name)
        if prior_provenance is not None:
            raise SystemExit(
                f"ERROR: duplicate config var name {var.name!r} — declared by "
                f"both the {prior_provenance!r} and {var.provenance!r} "
                "provenance sources. Every var name must be unique across "
                "core, template, external, and domain."
            )
        seen_names[var.name] = var.provenance

    # Runs after every provenance is merged so a helper read of a var that
    # any source already documents (a dataclass field, a template var, a
    # domain-YAML declaration) never fires.
    python_module = answers.get("python_module")
    if python_module:
        _assert_no_unscanned_from_env_reads(
            project_root,
            str(python_module),
            env_prefix,
            collected,
            scan_ignore=domain_presentation.get("scan_ignore") or (),
        )

    return tuple(sorted(collected, key=lambda v: _PROVENANCE_ORDER.index(v.provenance)))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _format_default(default: object) -> str:
    """Render a var's default as the text after ``=`` in an env file.

    ``None`` and an empty sequence both render as an empty value — an env
    file leaves "no default" blank for the reader to fill in, rather than
    spelling out ``None`` or ``()``. Booleans render lower-case, matching
    shell/env convention.

    ``set``/``frozenset`` are sorted before joining: unlike ``list``/``tuple``
    (whose declared order is meaningful and preserved as-is), a set's
    iteration order is not stable across processes, and template-ci renders
    the template twice and diffs the results — an unsorted join would make
    that diff flaky. A ``dict`` default has no defined env-file rendering, so
    it is rejected loudly rather than silently rendered as whatever
    ``str()``/``repr()`` would print (also order-unstable, pre-3.7 dict
    ordering guarantees aside — the point is there is no *sensible* rendering,
    not just an unstable one).

    `_NO_DEFAULT` (a domain field with no declared default at all) renders
    exactly like `None` — blank — never its own `repr()`. Both callers
    (`_format_value`, `_md_default_cell`) already guard against passing the
    sentinel here, but the guard is repeated at this, the lowest level, so no
    future caller can leak it into rendered output by skipping that guard.
    """
    if default is None or default is _NO_DEFAULT:
        return ""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, dict):
        raise SystemExit(
            f"ERROR: a var default of type dict ({default!r}) has no defined "
            "env-file rendering — give it a scalar or sequence default, or "
            "an `example` string, instead."
        )
    if isinstance(default, (set, frozenset)):
        return ",".join(sorted(str(item) for item in default))
    if isinstance(default, (list, tuple)):
        return ",".join(str(item) for item in default)
    return str(default)


def _format_value(var: Var, sub: Callable[[str], str]) -> str:
    """The text after ``=`` for one var: its real default, else its example.

    A ``None`` default, or `_NO_DEFAULT` (a domain field with no default
    declared at all — no real default either way) falls back to
    `var.example` — a presentation-declared placeholder shown so a reader
    knows the expected *shape* of the value (a JSON blob, a file path, a
    URL), not just that the field exists. A real default, even a falsy one
    (``False``, ``0``, an empty sequence), always wins over an example: only
    "no default at all" is ambiguous enough to need one. *sub* applies the
    same ``{HUMAN_NAME}``/``{PROJECT_NAME}`` substitution the header and
    section notes get, since an example like ``/etc/{PROJECT_NAME}/tokens.toml``
    is only useful once ``{PROJECT_NAME}`` is a real project name.
    """
    if var.default is not None and var.default is not _NO_DEFAULT:
        return _format_default(var.default)
    if var.example:
        return sub(var.example)
    return ""


def _name_substituter(answers: Mapping[str, object]) -> Callable[[str], str]:
    """A substituter for the ``{HUMAN_NAME}``/``{PROJECT_NAME}`` presentation tokens.

    `load_presentation` only replaces ``{PREFIX}``; these two are
    answers-derived and applied at render time instead. Shared by every
    destination that emits presentation-authored text (an env file's header
    and section notes, a `server.json` entry's ``placeholder``), so a path
    like ``/etc/{PROJECT_NAME}/tokens.toml`` reads the same wherever it
    surfaces rather than leaking a raw token in whichever destination forgot
    to substitute.
    """
    human_name = str(answers.get("human_name", ""))
    project_name = str(answers.get("project_name", ""))

    def _sub(text: str) -> str:
        return text.replace("{HUMAN_NAME}", human_name).replace(
            "{PROJECT_NAME}", project_name
        )

    return _sub


def _render_env_section(
    section: Mapping[str, Any],
    section_vars: Sequence[Var],
    sub: Callable[[str], str],
    value_prefix: str,
) -> list[str]:
    """Render one env-file section's title/note/var lines.

    Selection (which vars a section claims, whether it renders at all) stays
    in `render_env_file` — this renders an already-selected section, so the
    claim-once-per-file invariant has exactly one owner.
    """
    lines: list[str] = []
    title = section.get("title")
    if title is not None:
        lines.append(f"# --- {title} ---")
    note = section.get("note")
    if note is not None:
        for note_line in sub(str(note)).rstrip("\n").split("\n"):
            lines.append(f"# {note_line}".rstrip())
    for var in section_vars:
        for help_line in var.help.splitlines():
            lines.append(f"# {help_line}".rstrip())
        lines.append(f"{value_prefix}{var.name}={_format_value(var, sub)}")
    return lines


def render_env_file(
    spec: Mapping[str, Any], vars_: Sequence[Var], answers: Mapping[str, object]
) -> str:
    """Render one whole-file env artifact's full text.

    ``spec`` is one entry from ``config-presentation.yml``'s ``files``
    mapping (already ``{PREFIX}``-substituted by `load_presentation`).
    ``{HUMAN_NAME}`` / ``{PROJECT_NAME}`` header tokens are substituted here
    from *answers*, since `load_presentation` only replaces ``{PREFIX}``.

    Each section claims the first-declared, not-yet-claimed vars whose tags
    intersect its own — so a var whose tags span multiple sections (e.g. a
    core field tagged both ``server`` and ``apps``) appears exactly once,
    under whichever section is declared first. A section gated off by a
    false ``when_answer``, or with no matching vars *and* no ``note``, emits
    nothing at all — a dangling header with nothing under it would fail
    render-hygiene review and confuse a reader. A section with a ``note`` but
    no matching vars still emits its header and note: that is a deliberate
    signpost (e.g. "domain vars are discovered from your ProjectConfig") that
    must survive even when the project using it happens to have none yet —
    otherwise a project with no domain fields gives its reader no hint that
    domain vars exist as a concept at all. A section's own ``exclude`` list
    (full var names) drops specific vars even though their tags match — used
    when a var's tag-based inclusion would be actively wrong for one
    artifact (e.g. `examples/bearer-auth.env` excludes `BEARER_TOKENS_FILE`,
    since shipping both it and `BEARER_TOKEN` together makes core prefer the
    non-existent tokens file and refuse to start).
    """
    _sub = _name_substituter(answers)

    lines: list[str] = []

    def _blank() -> None:
        if lines and lines[-1] != "":
            lines.append("")

    header = spec.get("header")
    if header:
        for header_line in _sub(str(header)).rstrip("\n").split("\n"):
            lines.append(f"# {header_line}".rstrip())

    commented = bool(spec.get("commented", False))
    value_prefix = "# " if commented else ""
    placed: set[str] = set()

    for section in spec.get("sections", ()):
        when_answer = section.get("when_answer")
        if when_answer is not None and not answers.get(when_answer):
            continue
        section_tags = set(section.get("tags", ()))
        excluded = set(section.get("exclude", ()))
        section_vars = [
            v
            for v in vars_
            if v.name not in placed
            and v.name not in excluded
            and section_tags & set(v.tags)
        ]
        if not section_vars and section.get("note") is None:
            continue
        placed.update(v.name for v in section_vars)

        _blank()
        lines.extend(_render_env_section(section, section_vars, _sub, value_prefix))

    text = "\n".join(lines).rstrip("\n")
    return f"{text}\n" if text else ""


# `when: <token>` wizard hints (a var's own `wizard` mapping, and a
# `wizard_routing` entry's top-level `when`) expand to the same two-
# dimensional `showIf` the schema expects — `server` alone gates on the
# routing "deployment" choice; `oidc`/`bearer` additionally gate on the
# routing "auth" choice, since OIDC/bearer-specific vars only make sense once
# both a server deployment *and* that auth mode are selected.
_WIZARD_SHOW_IF: dict[str, dict[str, list[str]]] = {
    "server": {"deployment": ["server"]},
    "oidc": {"deployment": ["server"], "auth": ["oidc", "both"]},
    "bearer": {"deployment": ["server"], "auth": ["bearer", "both"]},
}

# The full vocabulary a `Var.wizard` hint mapping is allowed to use.
# `_validate_wizard_hint` rejects anything outside this — an unrecognised key
# or value used to be silently ignored (`_wizard_show_if` returned `None` for
# an unknown `when`, and only `control: emit` was ever checked), which let a
# typo silently promote a var to a primary, always-visible wizard question
# instead of failing loudly.
_KNOWN_WIZARD_HINT_KEYS = frozenset({"group", "when", "secret", "control"})
# `emit`: a `wizard_routing` option already emits this var (no question of
# its own). `none`: documented in the env artifacts, no wizard control at
# all — the parallel of `emit` for a var nothing routes for (e.g. a
# development-only var). Neither implies `inferred`, which means something
# different: "this value is derived from other settings", not "unrouted".
_KNOWN_WIZARD_CONTROL_VALUES = frozenset({"emit", "none"})

_TYPE_NAME_CLASS_RE = re.compile(r"<class '(?:[\w.]+\.)?(\w+)'>$")
_QUESTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _wizard_show_if(when: object, *, source: str) -> dict[str, list[str]] | None:
    """Map a scalar ``when`` hint to the spec's `showIf`.

    Returns ``None`` for no hint at all (``when`` is falsy). Raises loudly
    for a non-empty ``when`` that doesn't match a known token — *source*
    names the offending var or `wizard_routing` question in the message, so
    a typo (e.g. ``when: servr``) fails the generation run instead of
    silently rendering as if `when` had never been set (which would make the
    var/question a primary, always-visible one — never the intended
    behaviour for a hint author who bothered to write a `when` at all).
    """
    if not when:
        return None
    token = str(when)
    show_if = _WIZARD_SHOW_IF.get(token)
    if show_if is None:
        raise SystemExit(
            f"ERROR: {source} has unknown wizard 'when' token {token!r} — "
            f"known tokens are {sorted(_WIZARD_SHOW_IF)!r}."
        )
    return show_if


def _validate_wizard_hint(var: Var) -> None:
    """Reject any `Var.wizard` hint outside the known vocabulary, loudly.

    Checked once per var, regardless of whether that var ends up producing a
    question — a bad hint on an `inferred`/`control`-skipped var is still a
    bug in `config-presentation.yml` worth catching, not just a bug in
    whichever var happens to reach the wizard. Mirrors this file's existing
    loud-failure style (`_format_default`'s dict rejection, `collect_vars`'s
    duplicate-name guard).
    """
    unknown_keys = set(var.wizard) - _KNOWN_WIZARD_HINT_KEYS
    if unknown_keys:
        raise SystemExit(
            f"ERROR: {var.name} has unknown wizard hint key(s) "
            f"{sorted(unknown_keys)!r} — known keys are "
            f"{sorted(_KNOWN_WIZARD_HINT_KEYS)!r}."
        )
    _wizard_show_if(var.wizard.get("when"), source=var.name)
    control = var.wizard.get("control")
    if control is not None and control not in _KNOWN_WIZARD_CONTROL_VALUES:
        raise SystemExit(
            f"ERROR: {var.name} has unknown wizard 'control' value "
            f"{control!r} — known values are "
            f"{sorted(_KNOWN_WIZARD_CONTROL_VALUES)!r}."
        )


def _normalize_type_name(type_name: str) -> str:
    """Reduce a `Var.type_name` to its bare, lower-cased base token.

    `type_name` is annotation-form dependent: a core/template field (declared
    under ``from __future__ import annotations``) carries the literal
    annotation string (``"str | None"``, ``"Path"``), but a domain field
    discovered from a project *without* that import carries
    ``repr(field.type)`` instead (``"<class 'pathlib.Path'>"``). Both forms
    reduce here, rather than at each consumer matching either exact string —
    a union (``"str | None"``) or generic (``"tuple[str, ...]"``) is reduced
    to its first/outer name the same way. Shared by every destination that
    maps a declared type onto its own vocabulary (`_wizard_question_type`'s
    four question types, `_json_input_format`'s schema `format` enum) so the
    two can never disagree about what a given annotation *is*, only about how
    to present it.
    """
    match = _TYPE_NAME_CLASS_RE.match(type_name)
    normalized = match.group(1) if match else type_name
    return normalized.split("|", 1)[0].split("[", 1)[0].strip().lower()


def _wizard_question_type(type_name: str) -> str:
    """Normalise a `Var.type_name` down to one of the spec's four question types.

    Anything that isn't recognisably ``bool``/``int``/``float`` renders as a
    plain text question — including `Path`, which has no dedicated wizard
    control in this spec (unlike `server.json`, whose schema has a
    ``filepath`` format; see `_json_input_format`).
    """
    base = _normalize_type_name(type_name)
    if base == "bool":
        return "bool"
    if base in ("int", "float"):
        return "number"
    return "text"


def _wizard_label(var: Var) -> str:
    """A human-readable label derived from the var's suffix (full name if unprefixed)."""
    token = var.suffix or var.name
    return token.replace("_", " ").title()


def _routing_question(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Render one already-`{PREFIX}`-substituted `wizard_routing` entry as a question."""
    question: dict[str, Any] = {"id": raw["id"], "label": raw["label"]}
    help_text = raw.get("help")
    if help_text:
        question["help"] = help_text
    question["type"] = raw["type"]
    show_if = _wizard_show_if(raw.get("when"), source=f"wizard_routing[{raw['id']!r}]")
    if show_if is not None:
        question["showIf"] = show_if
    options = raw.get("options")
    if options:
        rendered_options: list[dict[str, Any]] = []
        for option in options:
            rendered: dict[str, Any] = {
                "value": option["value"],
                "label": option["label"],
            }
            emit = option.get("emit")
            if emit:
                rendered["emit"] = dict(emit)
            rendered_options.append(rendered)
        question["options"] = rendered_options
    return question


def _var_question(
    var: Var, labels: Mapping[str, str], help_overrides: Mapping[str, str]
) -> dict[str, Any] | None:
    """Render one `Var`'s wizard hint as a question, or ``None`` to emit nothing.

    ``inferred=True`` means "no wizard control offered" — the var still
    appears in the env artifacts (`collect_vars`/`render_env_file` never
    filter on it), only the wizard spec skips it. A `control: emit` hint
    (``TRANSPORT``) means a `wizard_routing` option already emits the var as
    a side effect of the routing choice — a second, independent question for
    it would let a user pick a value that contradicts that routing choice.
    A `control: none` hint (``DEBUG_PORT``, ``DEBUG_WAIT``) means the var is
    documented in the env artifacts but has no wizard-appropriate control at
    all — unlike `inferred`, nothing about its value is derived from another
    setting; it is just out of scope for the wizard.

    *labels*/*help_overrides* are the `wizard_labels`/`wizard_help` override
    maps from `config-presentation.yml`, keyed by full var name — checked
    before falling back to `_wizard_label`/`var.help`, since the mechanical
    fallbacks read as env-file prose (`"Oidc Client Id"`) or carry markup
    and paragraph-length text the wizard UI renders verbatim and unstyled.
    """
    _validate_wizard_hint(var)
    if var.inferred:
        return None
    if var.wizard.get("control") in ("emit", "none"):
        return None

    question: dict[str, Any] = {
        "id": (var.suffix or var.name).lower(),
        "label": labels.get(var.name, _wizard_label(var)),
        "type": _wizard_question_type(var.type_name),
        "var": var.name,
    }
    help_text = help_overrides.get(var.name, var.help)
    if help_text:
        question["help"] = help_text
    group = var.wizard.get("group")
    if group:
        question["advancedGroup"] = str(group)
    show_if = _wizard_show_if(var.wizard.get("when"), source=var.name)
    if show_if is not None:
        question["showIf"] = show_if
    return question


def _register_question_id(question_id: str, seen: dict[str, str], source: str) -> None:
    """Reserve *question_id* for *source*, raising loudly on a collision or a bad shape.

    `wizard.js` keys all wizard state by `answers[question.id]`, so two
    questions sharing an id — e.g. a domain field literally named ``auth`` or
    ``deployment`` colliding with a `wizard_routing` id — would silently
    corrupt every `showIf` evaluation rather than raise. Also rejects an id
    that doesn't match the schema's own `^[a-z][a-z0-9_]*$` (e.g. a
    dataclass field with a leading underscore), since that would fail
    schema validation with a far less useful error message.
    """
    if not _QUESTION_ID_RE.match(question_id):
        raise SystemExit(
            f"ERROR: {source} produced an invalid wizard question id "
            f"{question_id!r} — must match ^[a-z][a-z0-9_]*$."
        )
    prior = seen.get(question_id)
    if prior is not None:
        raise SystemExit(
            f"ERROR: duplicate wizard question id {question_id!r} — produced "
            f"by both {prior} and {source}. Every question id must be unique."
        )
    seen[question_id] = source


def _require_keys(raw: Mapping[str, Any], keys: Sequence[str], source: str) -> None:
    """Fail loudly when *raw* is missing any of *keys*, naming *source*.

    The template-owned presentation file gets these keys right by
    construction, but the domain seed is downstream-edited — a missing key
    there must name the file and entry rather than surface as a bare
    `KeyError` from deep inside a render helper.
    """
    missing = [key for key in keys if key not in raw]
    if missing:
        raise SystemExit(
            f"ERROR: {source} is missing required key(s) "
            f"{', '.join(repr(k) for k in missing)}."
        )


def _wizard_guard(
    raw: Mapping[str, Any], index: int, source: str = "wizard_guards"
) -> dict[str, Any]:
    """Render one `wizard_guards` entry, rejecting a non-list `when` value.

    ``list(scalar_string)`` silently explodes a scalar into one list entry
    per character (`when: {deployment: server}` → `["s", "e", ...]`) instead
    of raising — a guard's `when` must already be a list in YAML (e.g.
    `[server]`), so this checks that rather than coercing.
    """
    _require_keys(raw, ("level", "message", "when"), f"{source}[{index}]")
    when_raw = raw["when"]
    when: dict[str, list[str]] = {}
    for key, value in when_raw.items():
        if not isinstance(value, list):
            raise SystemExit(
                f"ERROR: {source}[{index}] has a non-list 'when[{key!r}]' "
                f"value {value!r} — expected a list of strings, e.g. [server]."
            )
        when[key] = list(value)
    return {"level": raw["level"], "message": raw["message"], "when": when}


def render_wizard_spec(
    pres: Mapping[str, Any],
    vars_: Sequence[Var],
    answers: Mapping[str, object],
    domain_pres: Mapping[str, Any] | None = None,
) -> str:
    """Render the config-wizard spec (`docs/javascripts/config-wizard/wizard-spec.json`).

    ``pres`` is the loaded, already `{PREFIX}`-substituted
    `config-presentation.yml` (the same value `write_artifacts` already loads
    for the env artifacts) — its ``wizard_routing``, ``wizard_guards``,
    ``wizard_labels``, and ``wizard_help`` keys drive the questions, guards,
    and label/help overrides; ``examples`` is not used here (it feeds
    env-file value rendering only, via `_format_value`).

    ``domain_pres`` is the loaded `config-presentation.domain.yml`, whose
    ``wizard_routing``/``wizard_guards`` sections merge in after the
    template-owned ones: domain routing questions render after the
    template's routing questions (still ahead of the per-var questions),
    domain guards after the template's guards, each in declaration order.
    A question id colliding across the two files is a `SystemExit` like any
    other id collision. The file is downstream-edited, so its entries get
    named-key validation (`config-presentation.domain.yml wizard_routing[…]`
    in the message) rather than a bare `KeyError`.

    ``secretKeys`` is derived from the *emitted questions*, not from
    `vars_` independently — a secret var that later gains `inferred`,
    `control: emit`, or `control: none` (and so stops producing a question)
    must drop out of `secretKeys` too, rather than leaving the rendered
    project's own schema-conformance check (`secretKeys` must be a subset of
    every question's ``var``) to fail downstream instead of this generator
    catching it.

    Key order within every emitted object is fixed by this function's own
    dict-literal construction, never by iterating a `set`/`frozenset` or by
    passing through whatever order an external mapping happened to iterate
    in — `template-ci` renders the template twice and diffs the results, so
    the JSON text must be byte-identical across processes regardless of
    `PYTHONHASHSEED`.
    """
    project_name = str(answers.get("project_name", ""))
    docker_registry = str(answers.get("docker_registry", ""))
    env_prefix = str(answers.get("env_prefix", ""))
    labels: Mapping[str, str] = pres.get("wizard_labels") or {}
    help_overrides: Mapping[str, str] = pres.get("wizard_help") or {}

    domain_pres = domain_pres or {}
    domain_routing_source = "config-presentation.domain.yml wizard_routing"
    domain_guards_source = "config-presentation.domain.yml wizard_guards"

    seen_ids: dict[str, str] = {}
    questions: list[dict[str, Any]] = []
    for raw in pres.get("wizard_routing", ()):
        question = _routing_question(raw)
        _register_question_id(
            question["id"], seen_ids, f"wizard_routing[{raw['id']!r}]"
        )
        questions.append(question)
    for index, raw in enumerate(domain_pres.get("wizard_routing") or ()):
        _require_keys(raw, ("id", "label", "type"), f"{domain_routing_source}[{index}]")
        question = _routing_question(raw)
        _register_question_id(
            question["id"], seen_ids, f"{domain_routing_source}[{raw['id']!r}]"
        )
        questions.append(question)
    for var in vars_:
        var_question = _var_question(var, labels, help_overrides)
        if var_question is not None:
            _register_question_id(var_question["id"], seen_ids, var.name)
            questions.append(var_question)

    question_vars = {q["var"] for q in questions if "var" in q}
    secret_keys = [
        var.name
        for var in vars_
        if var.wizard.get("secret") and var.name in question_vars
    ]

    guards = [
        _wizard_guard(raw, index)
        for index, raw in enumerate(pres.get("wizard_guards", ()))
    ]
    guards.extend(
        _wizard_guard(raw, index, source=domain_guards_source)
        for index, raw in enumerate(domain_pres.get("wizard_guards") or ())
    )

    spec = {
        "version": 1,
        "meta": {
            "projectName": project_name,
            "dockerImage": f"{docker_registry}/{project_name}:latest",
            "envPrefix": env_prefix,
        },
        "secretKeys": secret_keys,
        "questions": questions,
        "guards": guards,
    }
    text = json.dumps(spec, indent=2, ensure_ascii=False)
    return f"{text}\n"


# ---------------------------------------------------------------------------
# Markdown table splicing (docs artifacts)
# ---------------------------------------------------------------------------

# Column id -> header title, for `render_md_table`. `columns` in a spliced
# region's `config-presentation.yml` declaration is a sequence of these ids,
# rendered in the declared order.
_MD_COLUMN_TITLES: dict[str, str] = {
    "variable": "Variable",
    "default": "Default",
    "description": "Description",
    "required": "Required",
}


def _escape_pipes(text: str) -> str:
    """Escape ``|`` so it can't be mistaken for a Markdown table-cell separator.

    An unescaped pipe in a help string or default (e.g. help text quoting a
    shell pipeline, or a default value containing one) would otherwise split
    the row into extra cells and corrupt every cell after it.
    """
    return text.replace("|", r"\|")


def _is_required(var: Var, required_names: Collection[str] | None) -> bool:
    """Whether *var* counts as "required" — the single resolution consulted
    by both a spliced region's `required:` filter and `render_md_table`'s
    `required` column, so the two can never disagree.

    *required_names* is `config-presentation.yml`'s `required_vars:` list
    (already `{PREFIX}`-substituted), or `None` when no such declaration is
    in scope at all (e.g. `render_md_table` called directly, with no
    splice-region context):

    1. If *required_names* is not `None` and *var.name* appears in it, the
       var is required — this is the template's own, explicit source of
       truth for the vars it presents.
    2. Otherwise, when *required_names* is not `None`: a `domain`-provenance
       var — whose full name this template cannot enumerate ahead of time —
       falls back to "no default *declared* means required": does the
       field carry `_NO_DEFAULT` — neither a `default` nor a
       `default_factory` at all — the signal a downstream `ProjectConfig`
       author already controls by omitting a field default, since that
       author has no way to edit this template-owned list. This is
       deliberately keyed on `_NO_DEFAULT`, not on `var.default is None`: a
       domain field declared `x: str | None = None` has a real default (of
       `None`) and is optional, not required. Two producers agree on this:
       `_discover_domain_vars` (the AST-scanned path) produces `_NO_DEFAULT`
       when the field genuinely declares neither, and `collect_vars`'s
       `config-presentation.domain.yml` loop (the manual escape hatch for a
       var the AST scan cannot see) produces it the same way, when a manual
       entry omits its `default:` key. Any other var (core/template/external,
       all fully enumerable by this template) that isn't explicitly listed
       is optional; a null default alone is *not* evidence of being required
       for those — several are null-defaulted and still have a working
       fallback (e.g. an OIDC signing key derived from the client secret
       when unset), and none of them can ever carry `_NO_DEFAULT` (only the
       two domain-provenance producers above do).
    3. When *required_names* is `None` (no declaration in scope at all),
       every var falls back to the same provenance-aware check: `domain`
       keys on `_NO_DEFAULT`, everything else keys on a plain `None` default
       — unchanged from before this fix for every non-domain provenance.
    """
    if required_names is not None:
        if var.name in required_names:
            return True
        if var.provenance != "domain":
            return False
    if var.provenance == "domain":
        return var.default is _NO_DEFAULT
    return var.default is None


def _is_empty_default(default: object) -> bool:
    """Whether *default* carries no usable value to display.

    ``None`` obviously, but also any empty container or string: rendering
    ``OIDC_REQUIRED_SCOPES``'s ``()`` or a ``default: ""`` as an empty cell
    produces exactly the bare blank both display paths promise never to emit.
    Numeric and boolean falsiness is deliberately excluded — ``0`` and
    ``False`` are real, displayable defaults. `_NO_DEFAULT` (a domain field
    with no default declared at all) counts as empty too, same as `None` —
    `_md_default_cell` falls back to the documented default or `(none)`
    either way, never the sentinel's own `repr()`.
    """
    if default is None or default is _NO_DEFAULT:
        return True
    if isinstance(default, (bool, int, float)):
        return False
    return not default


def _documented_default(var: Var, documented_defaults: Mapping[str, str]) -> str | None:
    """The effective default *var* falls back to at runtime, if one is declared.

    A var's dataclass default is not always the value an operator actually
    gets. ``KV_STORE_URL``'s field default is ``None``, but core resolves an
    unset value to ``file:///data/state`` inside its own store builder — so
    "what happens if you set nothing" is a concrete path, not "nothing". A
    ``Default`` cell reading ``(none)`` beside a description that names the
    fallback contradicts itself, and a registry manifest that omits the value
    loses something a client could pre-fill; both were regressions against the
    hand-written tables this generator replaced.

    The declared value need not be a literal the operator could paste in
    verbatim. It may instead be a short descriptive label standing in for a
    derived or dynamic fallback that has no fixed string to print —
    ``OIDC_JWT_SIGNING_KEY`` declares ``derived`` for exactly this reason:
    the actual value is computed from the client secret at runtime, and the
    ``Default`` cell reading ``derived`` is a pointer to the description,
    which carries the full explanation, rather than a value anyone would
    set verbatim.

    Declared in `config-presentation.yml`'s `documented_defaults:` map, keyed
    by full var name, because core's field metadata carries no such key. That
    makes it a claim this template asserts about core's behaviour, so it must
    be re-verified against core on every dependency bump — the same standing
    obligation `required_vars:` carries, and for the same reason.
    """
    return documented_defaults.get(var.name)


# ---------------------------------------------------------------------------
# JSON array splicing (`server.json`'s registry manifest)
# ---------------------------------------------------------------------------

# The package shapes `config-presentation.yml`'s `packaging:` map may name,
# and that a `kind: json-splice` array entry selects one of. These are the
# `registryType` values `server.json` declares; a var is listed against the
# packagings it is actually *relevant to*, which is not the same set for
# each — a stdio/uvx install has no HTTP listener and no OAuth callback, so
# the auth and persistence knobs are inert there.
_ALL_PACKAGING_IDS = frozenset({"pypi", "oci"})

# `Var.type_name` base token -> the schema's `Input.format` enum value.
# Anything absent (`str` and friends) omits the key and takes the schema's
# own `"string"` default rather than restating it.
_JSON_INPUT_FORMATS: dict[str, str] = {
    "bool": "boolean",
    "int": "number",
    "float": "number",
    "path": "filepath",
}


def _packaging_ids(var: Var, packaging: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """Which package shapes *var* is relevant to.

    *packaging* is `config-presentation.yml`'s `packaging:` map (already
    ``{PREFIX}``-substituted), keyed by full var name. It is a keyed-by-name
    override map rather than a tag because a core var's ``tags`` belong to
    core — the same reason `examples`/`wizard_labels`/`wizard_help` are
    keyed that way.

    Resolution deliberately mirrors `_is_required`'s three steps:

    1. An explicit entry wins — this template's own source of truth for the
       vars it can enumerate.
    2. Otherwise a ``domain``-provenance var, whose full name this
       template-owned map cannot know ahead of time, is relevant to *every*
       packaging. A project's own config is relevant wherever that project
       runs, and this is what makes a downstream's `ProjectConfig` fields
       appear in its registry manifest without anyone hand-writing them.
    3. Any other unlisted var is relevant nowhere. `server.json`'s arrays
       are a curated per-packaging selection, not a dump of the whole
       surface, so an omission here is a decision rather than an oversight;
       a var that should appear gets an entry.
    """
    declared = packaging.get(var.name)
    if declared is not None:
        return frozenset(declared)
    if var.provenance == "domain":
        return _ALL_PACKAGING_IDS
    return frozenset()


# The keyed-by-full-var-name maps in `config-presentation.yml`. Each exists
# because core owns a var's own metadata and this template cannot add to it,
# so every one of them is a place a name can be misspelled.
_VAR_KEYED_MAPS = ("packaging", "choices", "documented_defaults", "examples")
_VAR_KEYED_LISTS = ("required_vars",)


def validate_presentation_keys(
    presentation: Mapping[str, Any], vars_: Sequence[Var]
) -> None:
    """Reject any keyed-by-var-name entry naming a var that does not exist.

    `_validate_packaging_map` guards the *values* of one map. This guards the
    *keys* of all of them, and for the same reason: a typo is byte-for-byte
    indistinguishable from a deliberate omission, so "an omission here is a
    decision" is only true if a misspelling cannot masquerade as one. Each map
    fails differently and silently — a `packaging:` typo drops the var from
    the registry manifest, a `choices:` typo downgrades a client's picker to a
    free-text box, a `required_vars:` typo moves a var from the Required table
    to the Optional one, a `documented_defaults:` typo restores the
    self-contradicting `(none)` cell. ``--check`` catches none of it, because
    it compares generated output against generated output: with the typo in
    place, the wrong form *is* the expected form.

    The valid-name set is deliberately wider than *vars_*: it also includes
    every name declared under `vars:` whose `when_answer` gate is currently
    false. `{PREFIX}_ACL_PATH` and the `AUTHZ_*` pair are absent from a
    render with `enable_authorization: false` — which `template-ci` runs on
    every push — yet they are legitimately declared in these maps for the
    renders where they do exist. Validating against collected vars alone
    would fail every gate-off render, which is a worse defect than the gap it
    closes.

    Domain vars are not enumerable here and need no entry in any of these
    template-owned maps, so they neither widen nor narrow the check.
    """
    known = {v.name for v in vars_}
    known.update(str(raw["name"]) for raw in presentation.get("vars", ()))

    problems: list[str] = []
    for map_name in _VAR_KEYED_MAPS:
        entries = presentation.get(map_name) or {}
        unknown = sorted(name for name in entries if name not in known)
        if unknown:
            problems.append(f"{map_name}: {unknown!r}")
    for list_name in _VAR_KEYED_LISTS:
        entries = presentation.get(list_name) or ()
        unknown = sorted(name for name in entries if name not in known)
        if unknown:
            problems.append(f"{list_name}: {unknown!r}")

    if problems:
        raise SystemExit(
            "ERROR: config-presentation.yml names config vars that do not "
            "exist (check for a typo in the var name) — "
            + "; ".join(problems)
            + ". Every key in these maps must match a collected var, or a var "
            "declared under `vars:` whose `when_answer` gate is currently off."
        )


def _validate_packaging_map(packaging: Mapping[str, Any]) -> None:
    """Reject any `packaging:` value outside the known vocabulary, loudly.

    Mirrors `_validate_wizard_hint`, and for the same reason: an unrecognised
    token that is merely *ignored* silently changes what ships. A var whose
    packaging is misspelled (``[ocl]``) resolves to no packaging at all and
    vanishes from every `server.json` array with exit 0 and no diagnostic —
    indistinguishable from a deliberate omission, since an unlisted var is
    legitimately relevant nowhere.

    A non-list value is rejected rather than coerced: ``list("oci")`` silently
    explodes a scalar into ``["o", "c", "i"]`` instead of raising, the same
    trap `_wizard_guard` already guards its ``when`` against.

    Every offender is named in one message, not just the first — fixing one
    typo only to have the next run report its sibling is what makes a
    one-line correction cost several rounds.
    """
    problems: list[str] = []
    for name, value in packaging.items():
        if not isinstance(value, list):
            problems.append(
                f"{name!r} has a non-list value {value!r} (expected a list, e.g. [oci])"
            )
            continue
        unknown = [item for item in value if item not in _ALL_PACKAGING_IDS]
        if unknown:
            problems.append(f"{name!r} names unknown packaging(s) {unknown!r}")
    if problems:
        raise SystemExit(
            "ERROR: config-presentation.yml `packaging:` is invalid — "
            + "; ".join(problems)
            + f". Known packagings are {sorted(_ALL_PACKAGING_IDS)!r}."
        )


def _json_input_format(var: Var) -> str | None:
    """The schema's ``Input.format`` for *var*, or ``None`` to omit the key.

    The 2025-12-11 `server.schema.json` types ``format`` as an enum of
    ``string``/``number``/``boolean``/``filepath``. Derived from the declared
    type via the same `_normalize_type_name` the wizard's question-type
    mapping uses, so the two destinations agree about what an annotation is
    even though they present it differently — the wizard has no ``filepath``
    control, this schema does.
    """
    return _JSON_INPUT_FORMATS.get(_normalize_type_name(var.type_name))


def _json_default(var: Var, documented_defaults: Mapping[str, str]) -> str | None:
    """*var*'s ``default`` for a `server.json` entry, or ``None`` to omit the key.

    The fourth destination for a var's default, with its own correct answer;
    the other three are `_format_value` (env file: falls back to `var.example`,
    because a reader wants something fillable), `_md_default_cell` (markdown
    table: renders the literal ``(none)``, because the column states what
    happens if the operator sets nothing), and this one.

    Two rules follow from the schema rather than from taste. ``Input.default``
    is typed ``string``, so a bool/int default is *rendered* via
    `_format_default` rather than emitted as a JSON bool/number. And a var
    with no real default omits the key entirely instead of emitting ``""`` —
    including an empty ``set``/``list``/``tuple`` default (``OIDC_REQUIRED_
    SCOPES``'s ``()``, meaning "no restriction"), which would otherwise
    render as an empty string that reads as "defaults to nothing" rather than
    "has no default". An example placeholder never appears here at all: the
    schema has a dedicated ``placeholder`` field for exactly that, and says
    so — see `_render_json_env_entry`.
    """
    documented = _documented_default(var, documented_defaults)
    if _is_empty_default(var.default):
        return documented
    return _format_default(var.default)


def _render_json_env_entry(
    var: Var,
    *,
    sub: Callable[[str], str],
    choices: Mapping[str, Sequence[str]],
    documented_defaults: Mapping[str, str],
) -> dict[str, Any]:
    """One `server.json` ``environmentVariables`` entry, as a schema ``Input``.

    Key insertion order is fixed by this dict-literal construction and never
    by iterating a `set`/`frozenset` — `template-ci` renders the template
    twice and diffs the results, so the serialised text must be byte-identical
    across processes regardless of ``PYTHONHASHSEED``.

    ``description`` is `var.help` as `_clean_help` left it, deliberately *not*
    `_clean_help_for_markdown_table`'s Vale-normalised variant: that
    normalisation exists because a spliced docs table lands in ``docs/``,
    which every downstream lints at ``MinAlertLevel = error``. JSON is linted
    by nothing, so the original prose (spaced em dash, ``e.g.`` and all) is
    both correct and closer to what core wrote.

    ``placeholder`` carries `var.example` when there is no real default. The
    schema documents that field as the place for "input examples or guidance"
    and says to use it *instead of* ``default`` — so the split that had to be
    hand-enforced for the markdown table (an example is a fill-in suggestion,
    not a statement of runtime behaviour) is native here. Emitting
    ``your-signing-key`` as ``OIDC_JWT_SIGNING_KEY``'s ``default`` would tell
    a client the server uses that literal string unless changed, contradicting
    the same entry's own description.

    ``choices`` comes from `config-presentation.yml`'s `choices:` map: it is
    not a field on the `Var` record, so it is a declared affordance rather
    than a special case in code for whichever var happens to need one today.

    ``isRequired`` is deliberately **never** emitted, even though the schema
    defines it and `required_vars:` looks like exactly the input for it. That
    list means "required for the feature this var belongs to" — the docs
    tables that consume it sit under an ``## OIDC`` heading, which supplies
    the missing half of the sentence. A package-level manifest has no such
    context, and the schema gives ``isRequired`` no feature-conditional
    scoping, so marking the four OIDC vars required would assert something
    false: a container with none of them set starts fine and serves
    unauthenticated, which this project documents as supported. A conformant
    registry client enforcing the flag would refuse to install without a
    public base URL and full OIDC client credentials. The requirement is
    genuine but conditional, so it stays in the prose ``description`` where
    the condition can be stated, and this is the fourth destination-specific
    split of a shared formatter rather than a fourth reuse of one.
    """
    entry: dict[str, Any] = {"name": var.name}
    if var.help:
        entry["description"] = var.help
    input_format = _json_input_format(var)
    if input_format is not None:
        entry["format"] = input_format
    default = _json_default(var, documented_defaults)
    if default is not None:
        entry["default"] = sub(default)
    elif var.example:
        entry["placeholder"] = sub(var.example)
    var_choices = choices.get(var.name)
    if var_choices:
        entry["choices"] = [str(choice) for choice in var_choices]
    if var.wizard.get("secret"):
        entry["isSecret"] = True
    return entry


def _json_array_container(
    data: Any, path: Sequence[Any], rel_path: str
) -> tuple[Any, Any]:
    """Walk *path*'s parent steps and return ``(container, last_key)``.

    Returning the container rather than the array itself is what keeps the
    replacement surgical: the caller assigns one key and every other key in
    the document — ``version`` and the OCI ``identifier`` above all, both
    owned by `stamp_manifests.py` — is left byte-untouched by construction
    rather than by remembering to copy it.

    Every way the walk can fail (a missing key, an out-of-range index, a
    scalar where a container was expected, or a final key that does not
    already hold a JSON array) raises `SystemExit` naming *rel_path* and the
    offending path. A `kind: json-splice` target is a hand-authored file
    whose structure this generator does not own, so a path that no longer
    resolves means the file was restructured under it — silently creating
    the missing key would bury that.
    """
    cursor = data
    for step in path[:-1]:
        try:
            cursor = cursor[step]
        except (KeyError, IndexError, TypeError) as exc:
            raise SystemExit(
                f"ERROR: {rel_path}: cannot resolve array path {list(path)!r} — "
                f"failed at {step!r} ({exc.__class__.__name__}: {exc})."
            ) from exc
    last_key = path[-1]
    try:
        existing = cursor[last_key]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(
            f"ERROR: {rel_path}: cannot resolve array path {list(path)!r} — "
            f"failed at {last_key!r} ({exc.__class__.__name__}: {exc})."
        ) from exc
    if not isinstance(existing, list):
        raise SystemExit(
            f"ERROR: {rel_path}: array path {list(path)!r} holds a "
            f"{type(existing).__name__}, not a JSON array."
        )
    return cursor, last_key


def _assert_packaging_matches_container(
    container: Any, packaging_id: str, path: Sequence[Any], rel_path: str
) -> None:
    """Fail when a positional `path:` resolved to the wrong package.

    `_json_array_container` covers every way the walk can *fail*; this covers
    the one way it can succeed *wrongly*. An `arrays:` entry locates its target
    by index (``[packages, 0, environmentVariables]``), so inserting or
    reordering a package silently swaps the two selections: the container
    package would receive the stdio set and the uvx package would receive the
    HTTP set, secrets and ``PUID``/``PGID`` included. That output is
    still schema-valid and still self-consistent, so neither the registry
    schema nor ``--check`` catches it.

    The `packaging:` ids are deliberately spelled the same as `server.json`'s
    own ``registryType`` values, which makes the binding self-checking: when
    the resolved parent object declares a ``registryType``, it must equal the
    declared packaging. `scripts/stamp_manifests.py` — the other tool that
    rewrites this file — already locates packages by ``registryType`` rather
    than by index, so this also stops the two tools disagreeing about which
    package is which.

    A parent with no ``registryType`` at all is left alone: `path:` is a
    general JSON path and its target need not be a registry package.
    """
    if not isinstance(container, dict):
        return
    declared = container.get("registryType")
    if declared is None or declared == packaging_id:
        return
    raise SystemExit(
        f"ERROR: {rel_path}: array path {list(path)!r} resolved to a package "
        f"whose registryType is {declared!r}, but the entry declares "
        f"packaging {packaging_id!r}. The packages were probably reordered or "
        "one was inserted. Restore the declared order (pypi first, oci "
        "second) in this file; editing config-presentation.yml instead will "
        "not survive, since copier update overwrites it. Left unfixed, the "
        "two packages' environment sets are swapped."
    )


@dataclass(frozen=True)
class PresentationContext:
    """The per-run inputs every splice renderer draws on.

    `write_artifacts` builds exactly one of these per run and hands it to
    each `kind: splice` / `kind: json-splice` renderer, so the renderers
    take "the run's presentation and answers" as one argument instead of
    re-growing a parameter list every time a renderer needs one more
    presentation-derived lookup. The properties resolve the same
    `config-presentation.yml` keys `write_artifacts` used to resolve inline
    — absent-key defaults included — so a presentation without them behaves
    exactly as before.
    """

    presentation: Mapping[str, Any]
    answers: Mapping[str, object]
    # The project's config-presentation.domain.yml content; only the wizard
    # renderer reads it today, but it is per-run input like the other two.
    domain_presentation: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    # The merged `files:` map (template + domain overlay) — lets a renderer
    # resolve a cross-entry reference such as claude-plugin-env's
    # `fields_from:` without re-merging.
    files: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def required_names(self) -> Collection[str]:
        return self.presentation.get("required_vars", ())

    @property
    def vocabulary(self) -> Mapping[str, str]:
        return self.presentation.get("markdown_vocabulary", {}) or {}

    @property
    def documented_defaults(self) -> Mapping[str, str]:
        return self.presentation.get("documented_defaults", {}) or {}


def render_json_splice_file(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Render one `kind: json-splice` artifact's full on-disk text.

    JSON has no comment syntax, so a `GENERATED-*` marker pair — the
    mechanism `kind: splice` uses for Markdown — has nowhere to live. This
    kind splices *structurally* instead: each declared array is located by
    its `path:` and replaced wholesale, and every other key in the document
    survives untouched because it is never read, rewritten or reordered.
    That matters most for ``server.json``'s ``version`` and its OCI
    ``identifier`` suffix, which `stamp_manifests.py` rewrites on every
    release and this generator must never touch.

    Like `kind: splice` and unlike a whole-file artifact, the target must
    already exist — a missing file is a `SystemExit` naming *rel_path*, since
    this generator owns one array per package and none of the surrounding
    manifest.

    Serialisation is ``json.dump``-equivalent with ``indent=2``,
    ``ensure_ascii=False`` and exactly one trailing newline — byte-identical
    to `stamp_manifests.py`'s own write of this same file. A mismatch would
    make the two tools reformat the file against each other on alternate
    runs: permanent churn, and `template-ci`'s render-twice ``diff -r``
    would fail.

    Presentation-level validation of the whole `packaging:` map (every
    declared entry, not just the ones a var resolves to) is a separate,
    broader check layered on top of this function rather than inside it.
    """
    target = project_root / rel_path
    if not target.exists():
        raise SystemExit(
            f"ERROR: {target} does not exist — a `kind: json-splice` artifact "
            "must already exist; this generator only replaces the declared "
            "array(s) inside it, never the surrounding file."
        )
    raw = target.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: {rel_path} is not valid JSON: {exc}.") from exc

    sub = _name_substituter(ctx.answers)
    packaging: Mapping[str, Sequence[str]] = ctx.presentation.get("packaging") or {}
    _validate_packaging_map(packaging)
    choices: Mapping[str, Sequence[str]] = ctx.presentation.get("choices") or {}
    documented_defaults = ctx.documented_defaults

    for array_spec in file_spec.get("arrays", ()):
        packaging_id = array_spec["packaging"]
        if packaging_id not in _ALL_PACKAGING_IDS:
            raise SystemExit(
                f"ERROR: config-presentation.yml files[{rel_path!r}] declares "
                f"unknown packaging {packaging_id!r} — known packagings are "
                f"{sorted(_ALL_PACKAGING_IDS)!r}."
            )
        container, last_key = _json_array_container(data, array_spec["path"], rel_path)
        _assert_packaging_matches_container(
            container, packaging_id, array_spec["path"], rel_path
        )
        container[last_key] = [
            _render_json_env_entry(
                var,
                sub=sub,
                choices=choices,
                documented_defaults=documented_defaults,
            )
            for var in vars_
            if packaging_id in _packaging_ids(var, packaging)
        ]

    text = json.dumps(data, indent=2, ensure_ascii=False)
    return f"{text}\n"


# mcpb `user_config` field types this generator can emit. `directory` and
# `file` are host-picker types no Python annotation maps to — they are only
# reachable via an explicit `type:` override in a field spec.
_MCPB_TYPES = frozenset({"string", "boolean", "number", "directory", "file"})
_MCPB_TYPE_BY_PYTHON = {
    "str": "string",
    "bool": "boolean",
    "int": "number",
    "float": "number",
}
_MCPB_FIELD_SPEC_KEYS = frozenset(
    {"id", "title", "description", "type", "required", "default", "sensitive"}
)
# mcpb config ids become `${user_config.<id>}` references; keep them to the
# conservative snake_case shape every existing manifest uses.
_MCPB_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _mcpb_field_type(var: Var, spec: Mapping[str, Any], rel_path: str) -> str:
    """The mcpb `type` for one field: explicit override, else from the annotation."""
    explicit = spec.get("type")
    if explicit is not None:
        if explicit not in _MCPB_TYPES:
            raise SystemExit(
                f"ERROR: files[{rel_path!r}] field for {var.name!r} declares "
                f"unknown mcpb type {explicit!r} — expected one of "
                f"{sorted(_MCPB_TYPES)!r}."
            )
        return str(explicit)
    return _MCPB_TYPE_BY_PYTHON.get(_normalize_type_name(var.type_name), "string")


def _mcpb_user_config_entry(
    var: Var, spec: Mapping[str, Any], rel_path: str
) -> dict[str, Any]:
    """One `user_config` object value, derived from the var + its field spec.

    Everything falls back to the var's own metadata (wizard-style label,
    `_clean_help`-cleaned help text, declared default) so a field spec only
    states what the install screen should present *differently* — an
    override, not a second copy of the surface.
    """
    entry: dict[str, Any] = {
        "type": _mcpb_field_type(var, spec, rel_path),
        "title": str(spec.get("title") or _wizard_label(var)),
        "description": str(spec.get("description") or var.help),
        "required": bool(spec.get("required", False)),
    }
    default = spec.get("default", var.default)
    if default is _NO_DEFAULT:
        default = None
    if default is not None:
        entry["default"] = default
    if spec.get("sensitive"):
        entry["sensitive"] = True
    return entry


def _mcpb_field_id(name: str, spec: Mapping[str, Any], rel_path: str) -> str:
    """Validate one field spec's shape and return its snake_case id."""
    unknown_keys = sorted(set(spec) - _MCPB_FIELD_SPEC_KEYS)
    if unknown_keys:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] field for {name!r} has unknown "
            f"keys {unknown_keys!r} — expected a subset of "
            f"{sorted(_MCPB_FIELD_SPEC_KEYS)!r}."
        )
    field_id = spec.get("id")
    if not isinstance(field_id, str) or not _MCPB_ID_RE.match(field_id):
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] field for {name!r} needs a "
            "snake_case `id:` — it becomes the ${user_config.<id>} "
            "reference in mcp_config.env."
        )
    return field_id


def _mcpb_screen_from_fields(
    fields: Mapping[str, Any],
    var_by_name: Mapping[str, Var],
    rel_path: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build the (`user_config`, `mcp_config.env`) pair from a fields map.

    One pass produces both objects, which is the drift-proofing itself: a
    screen field and its env wiring cannot disagree because neither exists
    without the other. A `None` spec is a field a domain overlay removed.
    """
    user_config: dict[str, Any] = {}
    env: dict[str, str] = {}
    id_owner: dict[str, str] = {}
    for name, spec in fields.items():
        if spec is None:
            continue
        field_id = _mcpb_field_id(name, spec, rel_path)
        if field_id in id_owner:
            raise SystemExit(
                f"ERROR: files[{rel_path!r}] declares id {field_id!r} for "
                f"both {id_owner[field_id]!r} and {name!r}."
            )
        id_owner[field_id] = name
        user_config[field_id] = _mcpb_user_config_entry(
            var_by_name[name], spec, rel_path
        )
        env[name] = "${user_config." + field_id + "}"
    return user_config, env


def render_mcpb_user_config_file(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Render one `kind: mcpb-user-config` artifact's full on-disk text.

    Structurally splices a Claude Desktop mcpb manifest the same way
    `kind: json-splice` splices ``server.json``: exactly two objects are
    replaced wholesale — top-level ``user_config`` and
    ``server.mcp_config.env`` — and every other key survives untouched,
    including the ``${VERSION}`` placeholders the release flow substitutes
    with ``envsubst``. Both objects derive from the same ``fields:`` map, so
    a config field and its env wiring cannot drift apart, and a field that
    exists nowhere else in the config surface cannot be invented here
    (every key must name a collected var). Membership is explicit
    curation — the install screen shows what `fields:` declares, nothing
    more — with the project's ``config-presentation.domain.yml`` able to
    add, override or remove fields via the domain files overlay (see
    `_merged_files`).

    The unused *ctx* keeps this renderer call-compatible with the other
    file kinds dispatched from `write_artifacts`.
    """
    del ctx
    target = project_root / rel_path
    if not target.exists():
        raise SystemExit(
            f"ERROR: {target} does not exist — a `kind: mcpb-user-config` "
            "artifact must already exist; this generator only replaces its "
            "`user_config` and `server.mcp_config.env` objects, never the "
            "surrounding manifest."
        )
    raw = target.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: {rel_path} is not valid JSON: {exc}.") from exc

    fields: Mapping[str, Any] = file_spec.get("fields") or {}
    if not fields:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] (kind: mcpb-user-config) declares no "
            "`fields:` — an mcpb bundle with an empty install screen would "
            "silently drop its existing user_config; remove the files entry "
            "instead if that is really intended."
        )
    var_by_name = {v.name: v for v in vars_}
    unknown = sorted(name for name in fields if name not in var_by_name)
    if unknown:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] names config vars that do not exist "
            f"(check for a typo, or a var whose gate is off): {unknown!r}."
        )

    user_config, env = _mcpb_screen_from_fields(fields, var_by_name, rel_path)

    server = data.get("server")
    mcp_config = server.get("mcp_config") if isinstance(server, dict) else None
    if not isinstance(mcp_config, dict):
        raise SystemExit(
            f"ERROR: {rel_path} has no `server.mcp_config` object to hold the "
            "generated env mapping — is this really an mcpb manifest?"
        )
    data["user_config"] = user_config
    mcp_config["env"] = env

    text = json.dumps(data, indent=2, ensure_ascii=False)
    return f"{text}\n"


def _resolve_fields_from(
    file_spec: Mapping[str, Any], rel_path: str, ctx: PresentationContext
) -> Mapping[str, Any]:
    """Resolve a `fields_from:` reference to another entry's merged fields.

    `kind: claude-plugin-env` derives its env mapping from the SAME fields
    map its `kind: claude-plugin-user-config` sibling renders — declared
    once, referenced here — so the plugin screen and its env wiring cannot
    drift apart even though they live in two files.
    """
    source_path = file_spec.get("fields_from")
    if not isinstance(source_path, str):
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] (kind: claude-plugin-env) needs a "
            "`fields_from:` naming the claude-plugin-user-config entry whose "
            "fields drive this env mapping."
        )
    source = ctx.files.get(source_path)
    if source is None or source.get("kind") != _CLAUDE_PLUGIN_UC_KIND:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] fields_from={source_path!r} does not "
            "name a declared `kind: claude-plugin-user-config` entry."
        )
    return source.get("fields") or {}


def _screen_fields_or_die(
    fields: Mapping[str, Any],
    vars_: Sequence[Var],
    rel_path: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a fields map against the collected vars and build the pair."""
    if not fields:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] declares no `fields:` — remove the "
            "files entry instead if an empty screen is really intended."
        )
    var_by_name = {v.name: v for v in vars_}
    unknown = sorted(name for name in fields if name not in var_by_name)
    if unknown:
        raise SystemExit(
            f"ERROR: files[{rel_path!r}] names config vars that do not exist "
            f"(check for a typo, or a var whose gate is off): {unknown!r}."
        )
    return _mcpb_screen_from_fields(fields, var_by_name, rel_path)


def _load_json_target(project_root: Path, rel_path: str, kind: str) -> Any:
    """Load a structurally-spliced JSON target that must already exist."""
    target = project_root / rel_path
    if not target.exists():
        raise SystemExit(
            f"ERROR: {target} does not exist — a `kind: {kind}` artifact "
            "must already exist; this generator only replaces its declared "
            "objects, never the surrounding file."
        )
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: {rel_path} is not valid JSON: {exc}.") from exc


def render_claude_plugin_user_config_file(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Render one `kind: claude-plugin-user-config` artifact (plugin.json).

    Replaces exactly the top-level ``userConfig`` object of a Claude Code
    plugin manifest; everything else — identity, the release-flow-owned
    ``version`` — survives untouched. Claude Code's `userConfig` schema uses
    the same field vocabulary as mcpb's `user_config` (string / number /
    boolean / directory / file, plus title / description / required /
    default / sensitive), so the field specs and their fallbacks are shared
    with `kind: mcpb-user-config`. The env side of the pairing lives in the
    sibling `kind: claude-plugin-env` entry, which references this entry's
    fields via `fields_from:`.
    """
    del ctx
    data = _load_json_target(project_root, rel_path, _CLAUDE_PLUGIN_UC_KIND)
    fields: Mapping[str, Any] = file_spec.get("fields") or {}
    user_config, _env = _screen_fields_or_die(fields, vars_, rel_path)
    data["userConfig"] = user_config
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return f"{text}\n"


def render_claude_plugin_env_file(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Render one `kind: claude-plugin-env` artifact (the plugin .mcp.json).

    Replaces the ``env`` object of every server entry in the plugin's
    `.mcp.json` with ``{ENV_VAR: "${user_config.<id>}"}`` derived from the
    `fields_from:` sibling's fields. Exec-form substitution is the only
    context Claude Code allows `${user_config.*}` in, which is why the
    scaffold's `.mcp.json` is exec-form to begin with. `command`, `args`
    (including the release-flow-owned version pin) and any other keys
    survive untouched.
    """
    fields = _resolve_fields_from(file_spec, rel_path, ctx)
    _user_config, env = _screen_fields_or_die(fields, vars_, rel_path)
    data = _load_json_target(project_root, rel_path, _CLAUDE_PLUGIN_ENV_KIND)
    servers = [v for v in data.values() if isinstance(v, dict)]
    if not servers:
        raise SystemExit(
            f"ERROR: {rel_path} has no server entries to hold the generated "
            "env mapping — is this really a plugin .mcp.json?"
        )
    for server in servers:
        server["env"] = dict(env)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return f"{text}\n"


def _md_variable_cell(
    var: Var,
    _required_names: Collection[str] | None,
    _vocabulary: Mapping[str, str],
    _documented_defaults: Mapping[str, str],
) -> str:
    return f"`{var.name}`"


# A spaced em dash, optionally followed by a coordinating conjunction that
# opens the next clause. Capturing the conjunction (group 1, may be absent)
# and the first character of whatever follows it (group 2) lets the
# replacement decide, per occurrence, whether the two clauses read better
# joined by a semicolon or split into two sentences.
_EM_DASH_CONJUNCTION_RE = re.compile(
    r" — (?:(but|and|or|so|yet|nor) )?(\S)", re.IGNORECASE
)
# Anything the pattern above did not reshape (a spaced dash at end of string,
# or one followed by whitespace) still has to go: a single leaked ` — ` is a
# hard CI failure in every downstream, so this path guarantees the invariant
# rather than relying on having enumerated every prose shape core might use.
# Named escapes rather than the literal glyphs: an en dash is visually
# indistinguishable from a hyphen in most editors, and ruff flags the
# ambiguity (RUF001).
_EM_DASH = "\N{EM DASH}"
_EN_DASH = "\N{EN DASH}"
_RESIDUAL_EM_DASH_RE = re.compile(rf"\s*[{_EM_DASH}{_EN_DASH}]\s*")
# A separator left stranded at the end of a cell once a trailing dash was
# rewritten (`Value continues —` would otherwise become `Value continues; `).
_TRAILING_SEPARATOR_RE = re.compile(r"[;,\s]+$")
# `e.g. FRAGMENT.`/`e.g. FRAGMENT;` -> ` (FRAGMENT).`/` (FRAGMENT);` — a
# parenthetical aside instead of the Latin abbreviation. FRAGMENT is
# whatever sits between `e.g.` and the clause's own terminator: a `;`, or a
# `.` that is itself followed by whitespace or end-of-string. The lookahead
# on the `.` terminator matters — an `e.g.` example is frequently a URL
# (`https://mcp.example.com`) whose own periods must NOT end the match; only
# a period followed by whitespace/EOS is a genuine sentence boundary.
_EG_CLAUSE_RE = re.compile(r",\s*e\.g\.,?\s+(.+?)(\.(?=\s|$)|;)", re.IGNORECASE)
# `e.g.` outside the comma-clause shape above — sentence-initial (`E.g. do X`)
# or inside an existing parenthetical (`(e.g. \`url\`)`), where wrapping the
# fragment in another pair of parentheses would read wrongly. Sentence-initial
# is handled first so it can take a capitalised replacement.
_EG_SENTENCE_INITIAL_RE = re.compile(r"(?:^|(?<=\.\s))E\.g\.,?\s+(\w)", re.IGNORECASE)
_EG_RESIDUAL_RE = re.compile(r"\be\.g\.,?\s+", re.IGNORECASE)
_IE_RE = re.compile(r"\bi\.e\.,?\s+", re.IGNORECASE)


def _dedash_for_markdown_table(match: re.Match[str]) -> str:
    """`_EM_DASH_CONJUNCTION_RE` replacement: see that pattern's comment."""
    conjunction, first_char = match.group(1), match.group(2)
    if conjunction is not None:
        # Two full clauses joined by "; but" reads stiffly — split into two
        # sentences instead and drop the now-redundant conjunction; the
        # sentence break already carries the same contrast.
        return f". {first_char.upper()}"
    return f"; {first_char}"


def _clean_help_for_markdown_table(
    text: str, vocabulary: Mapping[str, str] | None = None
) -> str:
    """Normalise *text* for a Vale-checked Markdown destination (a spliced
    docs table cell) — applied only on this path, never to the env-file or
    wizard-spec text, both of which keep the original `_clean_help`-cleaned
    prose unmodified.

    Core's field-metadata help text is written for a plain-text env-file
    comment, which Vale never lints. A spliced table cell lands in `docs/`,
    which every downstream lints with `MinAlertLevel = error`, and some of
    that prose trips rules a comment never faces. This is not a one-off fix
    for the vars that happen to need it today — any future core help text
    carrying the same patterns would break every downstream's Vale job
    again — so the normalisation lives here, at the one destination that
    needs it, rather than as a hand-patch of each offending sentence:

    Every rule below was probed against the live Vale binary with this
    template's own ruleset. Three of the results contradict what the rule
    names suggest, so they are recorded here rather than re-derived:

    - **Dashes.** `ai-tells.EmDashUsage` flags an em dash at *any* spacing,
      and an en dash too: `A—B` with no surrounding spaces is an error just
      as `A — B` is. So keying on the spaced form alone is not enough. A
      spaced dash opening a clause with a coordinating conjunction (`but`,
      `and`, `or`, `so`, `yet`, `nor`) becomes a sentence break (see
      `_dedash_for_markdown_table`); every other dash of either kind, at any
      spacing, collapses to `; `. A dash left at the very end would strand
      that separator, so a trailing one is trimmed.
    - **`e.g.`** (`Google.Latin`). Mid-clause it becomes a parenthetical
      (`, e.g. X.` -> ` (X).`); anything else becomes `such as `.
      Sentence-initially the abbreviation is dropped and the next word
      capitalised, because there is no usable connective: both
      `For example,` *and* `For instance,` trip
      `ai-tells.FormalTransitions`. Measured, not assumed — an earlier
      version of this function emitted `For example, ` and would have hard-
      failed every downstream the first time core shipped that shape.
    - **`i.e. `** becomes `that is, `, with no `ai-tells` conflict.

    A vocabulary map handles words Vale's spell-check rejects; see
    `config-presentation.yml`'s `markdown_vocabulary:` for why that cannot
    live in the Vale accept list.
    """
    # Collapse first: a table cell is one physical line by construction. A
    # newline anywhere in the prose would terminate the row and leak the
    # remainder into the page as body text, and `var.help` is genuinely
    # multi-line-capable — the env-file path splits it on `splitlines()`, and
    # the README DOMAIN region renders help a downstream author wrote, which
    # no test here can enumerate ahead of time.
    text = " ".join(text.split())
    text = _EM_DASH_CONJUNCTION_RE.sub(_dedash_for_markdown_table, text)
    text = _RESIDUAL_EM_DASH_RE.sub("; ", text)
    text = _EG_CLAUSE_RE.sub(lambda m: f" ({m.group(1)}){m.group(2)}", text)
    text = _EG_SENTENCE_INITIAL_RE.sub(lambda m: m.group(1).upper(), text)
    text = _EG_RESIDUAL_RE.sub("such as ", text)
    text = _IE_RE.sub("that is, ", text)
    for term, replacement in (vocabulary or {}).items():
        text = text.replace(term, replacement)
    return _TRAILING_SEPARATOR_RE.sub("", text)


def _md_description_cell(
    var: Var,
    _required_names: Collection[str] | None,
    vocabulary: Mapping[str, str],
    _documented_defaults: Mapping[str, str],
) -> str:
    return _clean_help_for_markdown_table(var.help, vocabulary)


def _md_default_cell(
    var: Var,
    _required_names: Collection[str] | None,
    _vocabulary: Mapping[str, str],
    documented_defaults: Mapping[str, str],
) -> str:
    """The `default` column cell: the real default, else the placeholder text `(none)`.

    Deliberately does **not** mirror `_format_value`'s fallback to
    `var.example` — the two destinations answer different questions.
    `_format_value` (the env-file path) asks "what fillable value should
    this line show a copy-pasting operator", where an example placeholder
    is exactly the right answer. This column asks "what happens if the
    operator sets nothing at all", where the example is flatly wrong: it's
    a fill-in suggestion, not a statement of runtime behaviour, and for a
    var like `OIDC_JWT_SIGNING_KEY` — whose example is a literal
    `your-signing-key` placeholder — showing it under "Default" tells an
    operator the system uses that string unless they change it, directly
    contradicting the same row's description ("derived from the client
    secret when unset").

    So: a real, non-empty default renders via `_format_default`. Anything
    else — `None`, or an empty `set`/`frozenset`/`list`/`tuple` such as
    `OIDC_REQUIRED_SCOPES`'s `()` — falls back to the var's declared
    `documented_defaults:` entry if it has one, and otherwise renders the
    literal `(none)`. A bare empty cell is never emitted: next to a
    description that names a fallback it reads as a contradiction.

    A rendered value is wrapped in backticks. That is better typography for a
    literal, and it also makes the whole column immune to `Vale.Spelling`:
    `openid`, `stdio`, a URL scheme and most other real defaults are not
    English words, and every downstream lints `docs/` at
    `MinAlertLevel = error`. Vale skips code spans, so this closes the class
    instead of adding a vocabulary entry per offending value. `(none)` stays
    bare, being prose standing in for the absence of a value rather than a
    value anyone could set.

    This is scoped to this function alone: `_format_default`/`_format_value`
    (the env-file rendering path) are untouched and keep using the example
    fallback, correctly.
    """
    if not _is_empty_default(var.default):
        return f"`{_format_default(var.default)}`"
    documented = _documented_default(var, documented_defaults)
    return f"`{documented}`" if documented else "(none)"


def _md_required_cell(
    var: Var,
    required_names: Collection[str] | None,
    _vocabulary: Mapping[str, str],
    _documented_defaults: Mapping[str, str],
) -> str:
    return "**Yes**" if _is_required(var, required_names) else "No"


_MD_COLUMN_RENDERERS: dict[
    str,
    Callable[[Var, Collection[str] | None, Mapping[str, str], Mapping[str, str]], str],
] = {
    "variable": _md_variable_cell,
    "default": _md_default_cell,
    "description": _md_description_cell,
    "required": _md_required_cell,
}


def render_md_table(
    vars_: Sequence[Var],
    columns: Sequence[str],
    *,
    required_names: Collection[str] | None = None,
    vocabulary: Mapping[str, str] | None = None,
    documented_defaults: Mapping[str, str] | None = None,
) -> str:
    """Render *vars_* as a Markdown table with the given *columns*, no trailing newline.

    *columns* is a sequence of column ids (``variable``, ``default``,
    ``description``, ``required``) — see `_MD_COLUMN_TITLES` for the full
    vocabulary; an unrecognised id fails loudly rather than silently
    rendering an empty or mistitled column. Every cell is pipe-escaped (see
    `_escape_pipes`) since a value containing a literal ``|`` would otherwise
    split the row. *required_names* is forwarded to the ``required`` column
    (see `_is_required`); omit it when no spliced region's `required_vars:`
    vocabulary is in scope. The caller (`render_splice_file`) owns embedding
    this inside a `GENERATED-ENV-TABLE-*` region; this function itself never
    touches a marker or a file.
    """
    unknown = [c for c in columns if c not in _MD_COLUMN_TITLES]
    if unknown:
        raise SystemExit(
            f"ERROR: render_md_table got unknown column(s) {unknown!r} — "
            f"known columns are {sorted(_MD_COLUMN_TITLES)!r}."
        )

    header = "| " + " | ".join(_MD_COLUMN_TITLES[c] for c in columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, separator]
    for var in vars_:
        cells = [
            _escape_pipes(
                _MD_COLUMN_RENDERERS[column](
                    var, required_names, vocabulary or {}, documented_defaults or {}
                )
            )
            for column in columns
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _generated_region_markers(region_id: str) -> tuple[str, str]:
    """The START/END HTML-comment marker pair for one spliced region.

    ``GENERATED-`` is deliberately distinct from the `DOMAIN-*`/`PROJECT-*`/
    `CONFIG-*` marker prefixes used elsewhere in this template: those mark
    hand-owned content that copier update preserves untouched. A
    ``GENERATED-`` region means the opposite — it is machine-owned and
    rewritten by this script on every run, so the START marker says so
    in-file; nothing between the two markers should ever be hand-edited.
    """
    start = (
        f"<!-- GENERATED-ENV-TABLE-{region_id}-START — generated by "
        "scripts/gen_config_surface.py; do not edit -->"
    )
    end = f"<!-- GENERATED-ENV-TABLE-{region_id}-END -->"
    return start, end


def splice_region(text: str, region_id: str, body: str, *, source: str) -> str:
    """Replace the content between one region's START/END markers, return the whole file text.

    Pure: never reads or writes a file — the caller owns all I/O (a spliced
    file must already exist; `render_splice_file` is what enforces that).
    *source* is the file this *text* came from (a project-relative path,
    e.g. ``"docs/deployment/oidc.md"``) — used only to name the offending
    file in an error message, never to read or write anything here.

    Raises `SystemExit` naming both *source* and *region_id* when: the
    START marker is missing, the END marker is missing, either marker
    appears more than once, or the END marker appears before the START
    marker — each of those would otherwise either silently no-op the splice
    or corrupt the file rather than fail loudly. Naming *source* matters
    concretely: the same region ids (``OIDC-REQUIRED`` / ``OIDC-OPTIONAL``)
    are declared in more than one file, so a region-id-only message can't
    tell an operator which of those files to fix — a marker broken in
    either one used to raise byte-identical text.
    """
    start_marker, end_marker = _generated_region_markers(region_id)
    start_matches = [m.start() for m in re.finditer(re.escape(start_marker), text)]
    end_matches = [m.start() for m in re.finditer(re.escape(end_marker), text)]

    if not start_matches:
        raise SystemExit(
            f"ERROR: {source}: region {region_id!r} is missing its START "
            f"marker ({start_marker!r})."
        )
    if not end_matches:
        raise SystemExit(
            f"ERROR: {source}: region {region_id!r} is missing its END "
            f"marker ({end_marker!r})."
        )
    if len(start_matches) > 1 or len(end_matches) > 1:
        raise SystemExit(
            f"ERROR: {source}: region {region_id!r} has a duplicated START "
            "or END marker — expected exactly one of each."
        )

    start_pos = start_matches[0]
    end_pos = end_matches[0]
    if end_pos < start_pos:
        raise SystemExit(
            f"ERROR: {source}: region {region_id!r}'s END marker appears "
            "before its START marker."
        )

    newline_pos = text.find("\n", start_pos)
    start_line_end = newline_pos + 1 if newline_pos != -1 else len(text)
    before = text[:start_line_end]
    after = text[end_pos:]
    return f"{before}{body}\n{after}" if body else f"{before}{after}"


def _select_region_vars(
    vars_: Sequence[Var],
    region: Mapping[str, Any],
    required_names: Collection[str] | None = None,
) -> list[Var]:
    """Select the vars_ one spliced region's table should render.

    First narrows to vars whose tags intersect the region's own `tags` —
    the same intersection test an env-file section's `tags` already uses —
    in the same order `vars_` already carries (contractual, see
    `collect_vars`'s ordering guarantee). The region's optional `required`
    key then further restricts that set using `_is_required` — the same
    resolution `_md_required_cell` uses for the `required` table column, so
    a region's filter and its own `Required` column (were it to declare
    one) can never disagree about which vars count as required. Omitting
    `required` entirely (the key absent, not merely falsy — checked via
    `is None`, not truthiness) keeps every tag-matched var, unchanged from
    before this filter existed; this is what lets one `tags:` selector
    split cleanly into a "required" and an "optional" region for the same
    file, each claiming a disjoint half of the same tag-matched set whose
    union is the unfiltered set.
    """
    region_tags = set(region.get("tags", ()))
    matched = [v for v in vars_ if region_tags & set(v.tags)]
    required = region.get("required")
    if required is None:
        return matched
    return [v for v in matched if _is_required(v, required_names) == bool(required)]


def render_splice_file(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Render one `kind: splice` artifact's full on-disk text.

    Unlike a whole-file `kind: env`/`kind: wizard` artifact, a spliced
    file's surrounding prose is hand-authored and must already exist on
    disk — a missing file is a `SystemExit` naming *rel_path*, since this
    generator only rewrites the marked region inside an existing file and
    never creates the file itself. Each declared region's vars are chosen
    by `_select_region_vars` (tag intersection, then an optional `required`
    filter, both driven by *ctx*'s `required_vars:` list) and rendered via
    `render_md_table` using the region's declared `columns` (forwarding the
    same list too, so a region that declares a `required` table column
    agrees with its own filter). Regions are spliced one after another,
    each pass operating on the previous pass's output, so multiple regions
    in one file compose correctly regardless of their relative marker
    positions.
    """
    target = project_root / rel_path
    if not target.exists():
        raise SystemExit(
            f"ERROR: {target} does not exist — a `kind: splice` artifact "
            "must already exist; this generator only rewrites the marked "
            "region inside it, never the surrounding file."
        )
    text = target.read_text(encoding="utf-8")
    for region in file_spec.get("regions", ()):
        region_vars = _select_region_vars(vars_, region, ctx.required_names)
        table = render_md_table(
            region_vars,
            region["columns"],
            required_names=ctx.required_names,
            vocabulary=ctx.vocabulary,
            documented_defaults=ctx.documented_defaults,
        )
        text = splice_region(text, region["id"], table, source=rel_path)
    return text


# `kind` -> renderer, dispatched per `config-presentation.yml` `files` entry.
# `env` renderers take that entry's own file spec; `wizard` takes the whole
# presentation (it has no per-file content of its own — see `files:` in
# `config-presentation.yml`, where `docs/javascripts/config-wizard/wizard-
# spec.json` declares only `kind: wizard` and nothing else); `splice`
# rewrites a marked region inside an otherwise hand-authored file — see
# `render_splice_file`. Both splice renderers additionally take the run's
# `PresentationContext` (the `packaging:`/`choices:` maps, the answers for
# `{PROJECT_NAME}` substitution inside a placeholder, and the Markdown
# table lookups) — see `render_json_splice_file` / `render_splice_file`.
_ENV_KIND = "env"
_WIZARD_KIND = "wizard"
_SPLICE_KIND = "splice"
_JSON_SPLICE_KIND = "json-splice"
_MCPB_KIND = "mcpb-user-config"
_CLAUDE_PLUGIN_UC_KIND = "claude-plugin-user-config"
_CLAUDE_PLUGIN_ENV_KIND = "claude-plugin-env"
_KNOWN_FILE_KINDS = frozenset(
    {
        _ENV_KIND,
        _WIZARD_KIND,
        _SPLICE_KIND,
        _JSON_SPLICE_KIND,
        _MCPB_KIND,
        _CLAUDE_PLUGIN_UC_KIND,
        _CLAUDE_PLUGIN_ENV_KIND,
    }
)


def _env_destinations(
    presentation: Mapping[str, Any], vars_: Sequence[Var], answers: Mapping[str, object]
) -> dict[str, set[str]]:
    """Map each var name to the set of `kind: env` sections that would place it.

    Mirrors `render_env_file`'s own `when_answer` / `tags` / `exclude`
    matching per section, but asks a different question: not "which section
    claims this var first within one file" (render_env_file's job, since a
    var must render exactly once per file), but "does *any* section in *any*
    env file want this var at all" — the question `write_artifacts` needs to
    catch a var that would be silently dropped from every env artifact.
    Empty for a var with no `kind: env` destination anywhere.
    """
    destinations: dict[str, set[str]] = {v.name: set() for v in vars_}
    for rel_path, file_spec in presentation.get("files", {}).items():
        if file_spec.get("kind") != _ENV_KIND:
            continue
        for section in file_spec.get("sections", ()):
            when_answer = section.get("when_answer")
            if when_answer is not None and not answers.get(when_answer):
                continue
            section_tags = set(section.get("tags", ()))
            excluded = set(section.get("exclude", ()))
            for var in vars_:
                if var.name in excluded or var.name not in destinations:
                    continue
                if section_tags & set(var.tags):
                    destinations[var.name].add(rel_path)
    return destinations


def _assert_every_var_has_an_env_destination(
    presentation: Mapping[str, Any], vars_: Sequence[Var], answers: Mapping[str, object]
) -> None:
    """Raise loudly for any collected `Var` that lands in zero env artifacts.

    `render_env_file` only ever places a var whose tags intersect some
    section's `tags` — nothing upstream of it ever verified that every
    collected var actually matches *some* section, so a var whose tags (or
    missing `tags:`) match no declared section is silently absent from every
    env artifact with exit 0 and no warning. `--check` cannot catch this
    either, since the generated-vs-on-disk comparison it runs is blind to a
    var neither side ever mentions. This is that missing check, run once
    after every var is collected and before any artifact is written.
    """
    destinations = _env_destinations(presentation, vars_, answers)
    unplaced = [v for v in vars_ if not destinations[v.name]]
    if not unplaced:
        return
    known_tags = sorted(
        {
            tag
            for file_spec in presentation.get("files", {}).values()
            if file_spec.get("kind") == _ENV_KIND
            for section in file_spec.get("sections", ())
            for tag in section.get("tags", ())
        }
    )
    offenders = ", ".join(f"{v.name!r} (tags={list(v.tags)!r})" for v in unplaced)
    raise SystemExit(
        "ERROR: the following config vars match no env-file section tags "
        f"and would be silently dropped from every env artifact: {offenders}. "
        f"Known section tags: {known_tags!r} — add one of those to the var's "
        "`tags`, or add a new section that covers it."
    )


# One renderer per file kind, all normalised to the same five-argument
# call shape `_render_one_artifact` dispatches with; the two lambdas adapt
# the env/wizard renderers, which predate `PresentationContext` and take
# their inputs directly.
_ARTIFACT_RENDERERS: dict[str, Callable[..., str]] = {
    _ENV_KIND: lambda _root, _rel, spec, vars_, ctx: render_env_file(
        spec, vars_, ctx.answers
    ),
    _WIZARD_KIND: lambda _root, _rel, _spec, vars_, ctx: render_wizard_spec(
        ctx.presentation, vars_, ctx.answers, domain_pres=ctx.domain_presentation
    ),
    _SPLICE_KIND: render_splice_file,
    _JSON_SPLICE_KIND: render_json_splice_file,
    _MCPB_KIND: render_mcpb_user_config_file,
    _CLAUDE_PLUGIN_UC_KIND: render_claude_plugin_user_config_file,
    _CLAUDE_PLUGIN_ENV_KIND: render_claude_plugin_env_file,
}


def _render_one_artifact(
    project_root: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
    vars_: Sequence[Var],
    ctx: PresentationContext,
) -> str:
    """Dispatch one `files:` entry to its kind's renderer.

    An unrecognised ``kind`` fails loudly instead of either silently
    producing nothing or raising a bare `KeyError`.
    """
    kind = file_spec.get("kind")
    renderer = _ARTIFACT_RENDERERS.get(kind) if isinstance(kind, str) else None
    if renderer is None:
        raise SystemExit(
            f"ERROR: config-presentation.yml files[{rel_path!r}] has "
            f"unknown kind {kind!r} — expected one of "
            f"{sorted(_KNOWN_FILE_KINDS)!r}."
        )
    return renderer(project_root, rel_path, file_spec, vars_, ctx)


def _merged_files(
    presentation: Mapping[str, Any], domain_presentation: Mapping[str, Any]
) -> dict[str, Any]:
    """Overlay the domain presentation's `files:` onto the template's.

    Two distinct powers, matching what a downstream may legitimately own:

    - A rel_path only the domain file declares is taken wholesale — this is
      how a project declares an install channel the template does not ship
      (a Claude Code plugin manifest, say) while reusing the generator's
      kinds and the `--check` gate.
    - A rel_path both declare merges at the `fields:` level only: a domain
      entry adds fields, overrides a template field's spec, or removes one
      by mapping its var name to `null`. Every other key (`kind:` above
      all) stays template-owned — a domain overlay must curate a template
      channel's fields, not repoint or re-kind the artifact.

    Declaration order is preserved: template fields first, domain additions
    after, so a curated screen leads with the template's baseline unless the
    domain entry overrides those fields too.
    """
    merged: dict[str, Any] = dict(presentation.get("files") or {})
    for rel_path, domain_spec in (domain_presentation.get("files") or {}).items():
        base = merged.get(rel_path)
        if base is None:
            merged[rel_path] = domain_spec
            continue
        extra = sorted(set(domain_spec) - {"fields"})
        if extra:
            raise SystemExit(
                f"ERROR: config-presentation.domain.yml files[{rel_path!r}] "
                f"may only contribute `fields:` to a template-declared file — "
                f"remove {extra!r}; kind and layout are template-owned."
            )
        fields = dict(base.get("fields") or {})
        for name, field_spec in (domain_spec.get("fields") or {}).items():
            if field_spec is None:
                fields.pop(name, None)
            else:
                fields[name] = field_spec
        combined = dict(base)
        combined["fields"] = fields
        merged[rel_path] = combined
    return merged


def write_artifacts(
    project_root: Path,
    *,
    check: bool,
    vars_: Sequence[Var] | None = None,
) -> list[str]:
    """Render and write (or, with ``check=True``, just compare) the artifacts.

    Every artifact this generator produces is driven off `config-
    presentation.yml`'s ``files`` mapping — adding or removing a `files:`
    entry there changes what gets generated, with no second list to keep in
    sync (YAML mapping order is insertion order, so iterating ``files``
    directly is as deterministic as the fixed tuple it replaces). The
    project's ``config-presentation.domain.yml`` may overlay that mapping —
    contribute whole entries for its own artifacts, or curate a template
    entry's ``fields:`` — see `_merged_files`. An unrecognised ``kind``
    fails loudly instead of either silently producing nothing or raising a
    bare `KeyError`.

    *vars_*, when given, is used as-is instead of calling `collect_vars`
    internally — `collect_vars` re-imports `fastmcp_pvl_core`, reloads and
    re-substitutes both presentation YAMLs, and re-runs
    `_discover_domain_vars` (which imports the project's own `config` module
    and can print a warning to stderr), so calling it twice in one process —
    once by a caller that already needed the surface for its own reasons,
    once again here — doubles that cost and, worse, duplicates any warning
    `_discover_domain_vars` prints, making one problem read as two. Pass
    ``None`` (the default) to keep collecting internally, unchanged from
    before this parameter existed — every pre-existing call site still works
    with no change.

    Returns the relative paths that are missing or whose on-disk content
    differs from the freshly rendered text — with ``check=False`` those are
    the paths actually written; an already-current file is left untouched
    (not even its mtime is bumped) and omitted from the result either way.
    """
    project_root = Path(project_root)
    answers = load_answers(project_root)
    env_prefix = _require_env_prefix(answers)
    presentation_root = _presentation_root(project_root)
    presentation = load_presentation(presentation_root, env_prefix)
    # Loaded here for its wizard_routing/wizard_guards sections; collect_vars
    # loads it independently for its vars — the file is a few hundred bytes,
    # and neither load prints anything, so the double read costs nothing.
    domain_presentation = _load_domain_presentation(presentation_root, env_prefix)
    if vars_ is None:
        vars_ = collect_vars(project_root, answers)

    validate_presentation_keys(presentation, vars_)
    merged_files = _merged_files(presentation, domain_presentation)
    ctx = PresentationContext(
        presentation=presentation,
        answers=answers,
        domain_presentation=domain_presentation,
        files=merged_files,
    )

    artifacts: list[tuple[str, str]] = [
        (rel_path, _render_one_artifact(project_root, rel_path, file_spec, vars_, ctx))
        for rel_path, file_spec in merged_files.items()
    ]

    # Checked after every file kind is known-good (so a genuinely malformed
    # `files:` entry above still gets its own, more specific error) but
    # before anything is written to disk — a var that would land nowhere is
    # a config-presentation bug, not something partial output should mask.
    _assert_every_var_has_an_env_destination(presentation, vars_, answers)

    changed: list[str] = []
    for rel_path, text in artifacts:
        target = project_root / rel_path
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        changed.append(rel_path)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------
# Self-bootstrap
# ---------------------------------------------------------------------------


def _core_constraint(project_root: Path) -> str:
    """Parse the full `fastmcp-pvl-core` version constraint from pyproject.toml.

    Returns the constraint exactly as the project declares it (e.g.
    `>=4.11.0,<5`), so the bootstrap re-exec resolves the same core version
    `uv sync` resolves for the project venv — pinning only the floor made
    copy-time generation and check-time regeneration disagree whenever a
    newer core changed any surface text (#335). Tolerates an extras marker
    (`fastmcp-pvl-core[redis]>=4.5.0,<5`) and a floor with fewer than three
    components (`>=4.5`) — both appear in real rendered `pyproject.toml`s.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise SystemExit(f"ERROR: {pyproject_path} not found.")
    text = pyproject_path.read_text(encoding="utf-8")
    match = _CORE_CONSTRAINT_RE.search(text)
    if match is None:
        raise SystemExit(
            f"ERROR: no 'fastmcp-pvl-core>=X.Y.Z' dependency found in {pyproject_path}"
        )
    return match.group(1).strip()


def _core_importable() -> bool:
    """Whether `fastmcp_pvl_core` is importable AND new enough for this generator.

    Checks for the specific symbols this generator imports at runtime, not
    merely that the package imports. On `copier update`, the project's
    existing virtualenv may still hold the pre-update `fastmcp-pvl-core`,
    which imports fine but lacks a symbol a newer generator needs
    (``domain_env_surface`` landed in core 4.6.0). A bare
    ``import fastmcp_pvl_core`` would succeed there, so `ensure_core_available`
    would skip the re-exec and the generator would then hit a hard
    ``ImportError`` mid-update (#306). Probing the symbols instead makes a
    too-old core count as "not available", so the bootstrap re-execs under
    ``uv run`` with the pyproject floor pinned and the generation succeeds.
    Keep this list in step with the ``from fastmcp_pvl_core import ...`` names
    the generator relies on at their newest floor.
    """
    try:
        from fastmcp_pvl_core import (  # noqa: F401
            domain_env_surface,
            server_config_surface,
        )
    except ImportError:
        return False
    return True


def _yaml_importable() -> bool:
    """Whether `yaml` (PyYAML) can be imported in the current interpreter."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_core_available(
    project_root: Path, argv: Sequence[str] | None = None
) -> None:
    """Re-exec under `uv run` if fastmcp-pvl-core or PyYAML is not importable.

    copier's ``_tasks`` run before any virtualenv exists for the freshly
    rendered project, so this script cannot assume its dependencies are
    installed. When either import fails, re-exec the whole process under
    ``uv run --no-project`` with the core library constrained exactly as the
    project's pyproject.toml declares it — the same resolution ``uv sync``
    performs later, so copy-time generation and a venv regeneration cannot
    disagree (#335) — and PyYAML added ad hoc. This must NOT create a
    persistent virtualenv, since template-ci renders the template twice and
    diffs the results.

    ``_GEN_CONFIG_BOOTSTRAPPED`` guards against re-exec'ing more than once:
    if the dependencies are still missing right after a re-exec, that is a
    real, unrecoverable problem (not something a second re-exec would fix),
    so this raises a clear error instead of looping forever.

    *argv* is the script's own arguments (excluding argv[0]) to preserve
    across the re-exec; defaults to ``sys.argv[1:]``.
    """
    if _core_importable() and _yaml_importable():
        return

    if os.environ.get("_GEN_CONFIG_BOOTSTRAPPED") == "1":
        raise SystemExit(
            "ERROR: fastmcp-pvl-core and/or PyYAML are still not importable "
            "after re-executing under `uv run` — check that `uv` is on PATH "
            "and that both packages are resolvable from this environment."
        )

    # Resolved to an absolute path (rather than letting exec search PATH) so a
    # missing `uv` fails with the install pointer below instead of a bare
    # FileNotFoundError, and so the exec target is unambiguous (ruff S607).
    uv_path = shutil.which("uv")
    if uv_path is None:
        raise SystemExit(
            "ERROR: `uv` is not on PATH — install `uv` "
            "(https://docs.astral.sh/uv/) or run this script inside an "
            "environment that already has fastmcp-pvl-core and PyYAML "
            "installed."
        )

    constraint = _core_constraint(project_root)
    script = str(Path(__file__).resolve())
    extra_argv = list(sys.argv[1:] if argv is None else argv)
    args = [
        "uv",
        "run",
        "--no-project",
        "--with",
        f"fastmcp-pvl-core{constraint}",
        "--with",
        "pyyaml",
        "python",
        script,
        *extra_argv,
    ]
    env = dict(os.environ)
    env["_GEN_CONFIG_BOOTSTRAPPED"] = "1"
    try:
        # A no-shell exec is deliberate: every element of `args` is built
        # here, none comes from outside input, and adding a shell would only
        # add quoting/injection surface. S606 (start-process-without-shell)
        # is per-file-ignored for this script in pyproject.toml — a `noqa`
        # here would trip RUF100 on the whole-repo lint pass, where `S` is
        # not selected.
        os.execvpe(uv_path, args, env)
    except OSError as exc:
        raise SystemExit(
            f"ERROR: could not re-exec under `uv run` ({exc}) — install `uv` "
            "(https://docs.astral.sh/uv/) or run this script inside an "
            "environment that already has fastmcp-pvl-core and PyYAML "
            "installed."
        ) from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """The generated project's root — this script lives at <root>/scripts/."""
    return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 on success.

    Without ``--check``, renders and writes every artifact `write_artifacts`
    knows about, then reports which paths were written (nothing, if they
    were already current). With ``--check``, compares only — nothing is ever
    written on this path — and returns 1 if any artifact is missing or
    stale, after printing each such path to stderr with a pointer to the
    command that fixes it; this is what a copier ``_tasks`` entry and CI both
    rely on to fail loudly instead of silently shipping stale files.

    The config surface is collected exactly once here and threaded into
    `write_artifacts` via its *vars_* parameter, so both the count line
    below and the rendered artifacts share that one computation instead of
    each calling `collect_vars` independently. `collect_vars` re-imports the
    project's own `config` module as part of domain discovery, and that
    import can print a warning to stderr on a broken `config.py`;
    collecting twice per invocation used to print that warning twice for
    one problem.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the generated artifacts are up to date without writing them.",
    )
    args = parser.parse_args(argv)

    project_root = _project_root()
    ensure_core_available(project_root, argv)

    answers = load_answers(project_root)
    variables = collect_vars(project_root, answers)
    print(
        f"Collected {len(variables)} config variables for {answers.get('env_prefix')}."
    )

    if args.check:
        stale = write_artifacts(project_root, check=True, vars_=variables)
        if not stale:
            return 0
        for path in stale:
            print(
                f"STALE: {path} is missing or out of date — run: "
                "python scripts/gen_config_surface.py",
                file=sys.stderr,
            )
        return 1

    written = write_artifacts(project_root, check=False, vars_=variables)
    if written:
        for path in written:
            print(f"Wrote {path}.")
    else:
        print("All config artifacts already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
