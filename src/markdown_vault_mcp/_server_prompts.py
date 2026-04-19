"""MCP prompt registrations for the markdown-vault-mcp server.

Call :func:`register_prompts` after constructing the
:class:`~fastmcp.FastMCP` instance in
:func:`~markdown_vault_mcp.mcp_server.create_server`.
"""

from __future__ import annotations

import importlib.resources
import keyword
import logging
import re
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

import frontmatter
from fastmcp import FastMCP

from ._icons import _TOOL_ICONS

_BUILTIN_PROMPTS_DIR = importlib.resources.files("markdown_vault_mcp").joinpath(
    "static/prompts"
)

logger = logging.getLogger(__name__)

# Only valid Python identifiers are allowed as argument names in user prompts.
# This prevents exec() injection via malicious frontmatter argument names.
_VALID_IDENT = re.compile(r"^[a-zA-Z_]\w*$")

# Argument names that would shadow variables injected into the exec() namespace,
# causing silent wrong output (tmpl) or TypeError at invocation (_Template).
_RESERVED_EXEC_NAMES = frozenset({"tmpl", "_Template"})


def _load_user_prompt_defs(prompts_folder: str | None) -> dict[str, dict[str, Any]]:
    """Scan *prompts_folder* and return a dict of prompt definitions.

    Args:
        prompts_folder: Relative or absolute path to a directory of ``.md``
            prompt files, or ``None``.

    Returns:
        A mapping of prompt name (file stem) to a dict with keys
        ``description`` (str), ``arguments`` (list[dict]), ``tags``
        (list[str]), and ``content`` (str).  Returns an empty dict when
        *prompts_folder* is ``None`` or the directory does not exist.
    """
    if prompts_folder is None:
        return {}

    folder = Path(prompts_folder)
    if not folder.exists() or not folder.is_dir():
        logger.warning(
            "PROMPTS_FOLDER=%r does not exist or is not a directory — "
            "user-defined prompts will not be loaded",
            str(folder),
        )
        return {}

    defs: dict[str, dict[str, Any]] = {}
    for md_file in sorted(folder.glob("*.md")):
        name = md_file.stem
        try:
            post = frontmatter.loads(md_file.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "Failed to parse user prompt file %r — skipping",
                str(md_file),
                exc_info=True,
            )
            continue

        description: str = post.get("description", "") or ""
        raw_arguments = post.get("arguments") or []
        if not isinstance(raw_arguments, list):
            raw_arguments = []
        arguments: list[dict[str, Any]] = []
        for arg in raw_arguments:
            if isinstance(arg, dict) and "name" in arg:
                arguments.append(
                    {
                        "name": str(arg["name"]),
                        "description": str(arg.get("description", "")),
                        "required": bool(arg.get("required", False)),
                    }
                )

        raw_tags = post.get("tags") or []
        tags: list[str] = (
            [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
        )

        defs[name] = {
            "description": description,
            "arguments": arguments,
            "tags": tags,
            "content": post.content,
        }
        logger.debug("Loaded user prompt definition: %s (from %s)", name, md_file.name)

    return defs


def _register_one_user_prompt(mcp: FastMCP, name: str, defn: dict[str, Any]) -> None:
    """Register a single user-defined prompt on *mcp*.

    Builds a function with the correct signature via :func:`exec` so that
    FastMCP can introspect the arguments.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance.
        name: Prompt name (file stem).
        defn: Prompt definition dict with keys ``description``,
            ``arguments``, ``tags``, and ``content``.
    """
    description: str = defn.get("description", "")
    arg_defs: list[dict[str, Any]] = defn.get("arguments", [])
    tags: set[str] = set(defn.get("tags", []))
    content_template: str = defn["content"]

    if not arg_defs:
        # No arguments — simple no-arg function.
        def _make_no_arg(tmpl: str) -> Any:
            def prompt_fn() -> str:
                return tmpl

            return prompt_fn

        fn = _make_no_arg(content_template)
    else:
        # Validate all argument names before building the exec'd function.
        # Malicious frontmatter could inject arbitrary Python via exec() if
        # names are not restricted to valid identifiers, or if names happen
        # to be reserved keywords (which would cause a SyntaxError in exec).
        for arg in arg_defs:
            arg_name = arg["name"]
            if (
                not _VALID_IDENT.match(arg_name)
                or keyword.iskeyword(arg_name)
                or arg_name in _RESERVED_EXEC_NAMES
            ):
                logger.warning(
                    "User prompt %r has invalid or reserved argument name %r — skipping prompt",
                    name,
                    arg_name,
                )
                return

        # Build a function with the correct typed signature via exec so
        # FastMCP can introspect argument names and required status.
        # Template substitution uses string.Template ($var / ${var}) rather
        # than str.format() to prevent attribute-traversal format-string attacks.
        param_parts: list[str] = []
        for arg in arg_defs:
            if arg.get("required", False):
                param_parts.append(f"{arg['name']}: str")
            else:
                param_parts.append(f'{arg["name"]}: str = ""')
        params_str = ", ".join(param_parts)
        sub_args = ", ".join(f"{a['name']}={a['name']}" for a in arg_defs)
        fn_src = (
            f"def prompt_fn({params_str}) -> str:\n"
            f"    return _Template(tmpl).safe_substitute({sub_args})\n"
        )
        local_ns: dict[str, Any] = {"tmpl": content_template, "_Template": Template}
        try:
            exec(fn_src, local_ns)
        except SyntaxError:
            logger.warning(
                "User prompt %r generated invalid function signature — skipping prompt",
                name,
                exc_info=True,
            )
            return
        fn = local_ns["prompt_fn"]

    fn.__name__ = name
    fn.__doc__ = description or f"User-defined prompt: {name}"

    decorator_kwargs: dict[str, Any] = {}
    if tags:
        decorator_kwargs["tags"] = tags

    mcp.prompt(**decorator_kwargs)(fn)
    logger.debug("Registered user-defined prompt: %s", name)


def _load_builtin_prompt(name: str) -> dict[str, Any] | None:
    """Load a built-in prompt definition from ``static/prompts/{name}.md``."""
    try:
        text = _BUILTIN_PROMPTS_DIR.joinpath(f"{name}.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Built-in prompt file %s.md not found — skipping", name)
        return None
    try:
        post = frontmatter.loads(text)
    except Exception:
        logger.warning(
            "Failed to parse built-in prompt file %s.md — skipping",
            name,
            exc_info=True,
        )
        return None
    raw_arguments = post.get("arguments") or []
    arguments: list[dict[str, Any]] = []
    for arg in raw_arguments:
        if isinstance(arg, dict) and "name" in arg:
            arguments.append(
                {
                    "name": str(arg["name"]),
                    "description": str(arg.get("description", "")),
                    "required": bool(arg.get("required", False)),
                }
            )
    return {
        "description": post.get("description", "") or "",
        "arguments": arguments,
        "tags": [str(t) for t in post.get("tags", [])],
        "icons": str(post.get("icons", "")),
        "content": post.content,
    }


def _register_one_builtin_prompt(mcp: FastMCP, name: str, defn: dict[str, Any]) -> None:
    """Register a single built-in prompt loaded from a static .md file."""
    description: str = defn["description"]
    arg_defs: list[dict[str, Any]] = defn["arguments"]
    tags: set[str] = set(defn.get("tags", []))
    icon_key: str = defn.get("icons", "")
    content_template: str = defn["content"]

    decorator_kwargs: dict[str, Any] = {}
    if tags:
        decorator_kwargs["tags"] = tags
    if icon_key and icon_key in _TOOL_ICONS:
        decorator_kwargs["icons"] = _TOOL_ICONS[icon_key]

    if not arg_defs:
        fn: Any = lambda: content_template  # noqa: E731
    else:
        # Build a function with correct signature via exec (same pattern as
        # user-defined prompts) so FastMCP can introspect argument names.
        param_parts: list[str] = []
        for arg in arg_defs:
            if arg.get("required", False):
                param_parts.append(f"{arg['name']}: str")
            else:
                param_parts.append(f'{arg["name"]}: str = ""')
        params_str = ", ".join(param_parts)

        # Some prompts compute derived variables (e.g. research: topic_slug).
        # Add a pre-compute block for known derivations.
        # SYNC: the research block below is coupled to static/prompts/research.md
        # which uses ${topic_slug}.  If that template drops the variable, remove
        # the pre-compute and extra arg here as well.
        pre_compute = ""
        if name == "research":
            pre_compute = "    topic_slug = _re_sub(r'[^\\w\\-]', '-', topic.lower()).strip('-')\n"

        all_args = [a["name"] for a in arg_defs]
        if name == "research":
            all_args.append("topic_slug")
        sub_args = ", ".join(f"{a}={a}" for a in all_args)

        fn_src = (
            f"def prompt_fn({params_str}) -> str:\n"
            f"{pre_compute}"
            f"    return _Template(tmpl).safe_substitute({sub_args})\n"
        )
        local_ns: dict[str, Any] = {
            "tmpl": content_template,
            "_Template": Template,
            "_re_sub": re.sub,
        }
        try:
            exec(fn_src, local_ns)
        except SyntaxError:
            logger.warning(
                "Built-in prompt %r generated invalid function signature — skipping",
                name,
                exc_info=True,
            )
            return
        fn = local_ns["prompt_fn"]

    fn.__name__ = name
    fn.__doc__ = description

    mcp.prompt(**decorator_kwargs)(fn)
    logger.debug("Registered built-in prompt: %s", name)


def register_prompts(
    mcp: FastMCP,
    templates_folder: str | None,
    prompts_folder: str | None = None,
) -> None:
    """Register all built-in MCP prompts on *mcp*, with user-defined overrides.

    User-defined prompts from *prompts_folder* take priority.  Any built-in
    whose name matches a user-defined prompt is skipped.  User-defined prompts
    are registered after the built-ins.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register prompts on.
        templates_folder: The configured templates folder path, or ``None``
            when templates are not configured.  Passed in from
            :func:`~markdown_vault_mcp.mcp_server.create_server` so that
            ``create_from_template`` can close over this value without
            re-reading environment variables.
        prompts_folder: Path to a directory of user-defined ``.md`` prompt
            files.  ``None`` disables user-defined prompt loading.
    """
    # --- Pass 1: collect user-defined prompt names (for override semantics) ---
    user_prompt_defs = _load_user_prompt_defs(prompts_folder)
    if user_prompt_defs:
        logger.info(
            "User-defined prompts found in %r: %s",
            prompts_folder,
            sorted(user_prompt_defs),
        )

    # --- Pass 2: register built-ins from static/prompts/*.md ---
    #
    # Each .md file has frontmatter (description, arguments, tags, icons) and
    # a body that uses $var / ${var} (string.Template) substitution.
    # ``create_from_template`` has path-traversal safety logic and stays inline.

    for md_name in [
        "summarize",
        "research",
        "discuss",
        "related",
        "compare",
        "propose-links",
    ]:
        if md_name in user_prompt_defs:
            continue
        defn = _load_builtin_prompt(md_name)
        if defn is not None:
            _register_one_builtin_prompt(mcp, md_name, defn)

    if "create_from_template" not in user_prompt_defs:

        @mcp.prompt(tags={"write"}, icons=_TOOL_ICONS["write"])
        def create_from_template(template_name: str | None = None) -> str:
            """Create a new note from a vault template. Pass template_name (e.g. "meeting-notes" or "meeting-notes.md") to skip discovery, or omit to browse available templates first."""
            template_hint = "None" if template_name is None else repr(template_name)
            template_name_clean = (
                (template_name or "").strip().replace("\\", "/").lstrip("/")
            )
            if template_name_clean:
                resolved: list[str] = []
                for part in PurePosixPath(template_name_clean).parts:
                    if part in ("", "."):
                        continue
                    elif part == "..":
                        if resolved:
                            resolved.pop()
                    else:
                        resolved.append(part)
                template_name_clean = str(PurePosixPath(*resolved)) if resolved else ""
            template_path = (
                str(PurePosixPath(templates_folder) / template_name_clean)
                if template_name_clean and templates_folder is not None
                else ""
            )
            return (
                "## Role\n"
                "You are a note assistant that creates new notes from vault templates.\n\n"
                "## Context\n"
                f"- Templates folder: `{templates_folder}`\n"
                f"- Requested template_name: {template_hint}\n"
                "- Templates are normal markdown files. Do not use server-side variable substitution.\n\n"
                "## Task\n"
                "Guide the user through this workflow: discover template -> read template -> gather values -> write the new note.\n\n"
                "## Format\n"
                "Follow these exact steps in order:\n"
                "1. If `template_name` is missing, call `list_documents(folder=<templates folder>)`, "
                "show available templates, and ask the user to pick one.\n"
                "2. Resolve template path and call `read(path=<template path>)`.\n"
                f"   If a name is already provided, start with `read(path='{template_path or '<templates_folder>/<template_name>'}')`.\n"
                "3. Present the template structure and ask the user for missing values.\n"
                "4. Propose a target note path. Prefer frontmatter convention if present; otherwise ask the user.\n"
                "5. Call `write(path=..., content=..., frontmatter=...)` with the filled note.\n\n"
                "## Constraints\n"
                "- Use only vault tools (`list_documents`, `read`, `write`) for this flow.\n"
                "- Never overwrite an existing file without explicit user confirmation.\n"
                "- If the selected template does not exist, return a clear error and ask for another template.\n"
                "- Keep paths relative to the vault root.\n"
                "- Repeat: discover -> read -> fill -> write.\n"
            )

    # --- Pass 3: register user-defined prompts ---
    for name, defn in user_prompt_defs.items():
        _register_one_user_prompt(mcp, name, defn)
