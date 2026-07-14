import importlib
import inspect

# The public API submodules (the package root is a bare skeleton since #903;
# consumers import from these directly). Every public class/function they
# export must carry a docstring.
_PUBLIC_API_MODULES = (
    "markdown_vault_mcp.config",
    "markdown_vault_mcp.exceptions",
    "markdown_vault_mcp.git",
    "markdown_vault_mcp.types",
    "markdown_vault_mcp.vault",
)


def _public_members(obj):
    """Return (name, member) pairs for public non-dunder members defined directly on obj."""
    return [
        (name, member)
        for name, member in vars(obj).items()
        if not name.startswith("_") and not inspect.ismodule(member)
    ]


def test_all_exported_symbols_have_docstrings():
    """Every public class/function in the public-API submodules has a docstring."""
    missing = []
    for module_name in _PUBLIC_API_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in _public_members(module):
            # Only check classes and functions; bare type aliases (Callable[...]
            # etc.) cannot carry docstrings.
            if not (inspect.isclass(obj) or inspect.isfunction(obj)):
                continue
            # Own this symbol if it is defined in this module OR a subpackage of
            # it (``markdown_vault_mcp.git`` is a package that re-exports
            # GitWriteStrategy / git_write_strategy from ``git.strategy``), so a
            # bare ``__module__ == module_name`` filter would drop them. Symbols
            # merely imported from an unrelated module are checked where defined.
            owner = getattr(obj, "__module__", "") or ""
            if owner != module_name and not owner.startswith(module_name + "."):
                continue
            if obj.__doc__ is None:
                missing.append(f"{module_name}.{name}")
    assert not missing, f"Missing docstrings: {missing}"


def test_vault_public_methods_have_docstrings():
    """Every public Vault method must have a docstring."""
    from markdown_vault_mcp.vault import Vault

    missing = [
        name
        for name, member in _public_members(Vault)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"Vault methods missing docstrings: {missing}"


def test_gitwritestrategy_public_methods_have_docstrings():
    """Every public GitWriteStrategy method must have a docstring."""
    from markdown_vault_mcp.git import GitWriteStrategy

    missing = [
        name
        for name, member in _public_members(GitWriteStrategy)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"GitWriteStrategy methods missing docstrings: {missing}"


def test_all_mcp_tools_have_icons():
    """Every @mcp.tool decorator in _server_*.py must include icons=."""
    import ast
    from pathlib import Path

    server_dir = Path(__file__).parent.parent / "src" / "markdown_vault_mcp"
    paths = sorted(server_dir.glob("_server_*.py"))
    assert paths, f"No _server_*.py files found under {server_dir.resolve()}"
    missing = []
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    continue
                has_icons = any(kw.arg == "icons" for kw in decorator.keywords)
                if not has_icons:
                    missing.append(f"{path.name}::{node.name}")
    assert not missing, f"@mcp.tool decorators missing icons=: {missing}"
