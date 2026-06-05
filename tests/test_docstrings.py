import inspect

import markdown_vault_mcp


def _public_members(obj):
    """Return (name, member) pairs for public non-dunder members defined directly on obj."""
    return [
        (name, member)
        for name, member in vars(obj).items()
        if not name.startswith("_") and not inspect.ismodule(member)
    ]


def test_all_exported_symbols_have_docstrings():
    """Every class and function in __all__ must have a docstring."""
    missing = []
    for name in markdown_vault_mcp.__all__:
        obj = getattr(markdown_vault_mcp, name)
        # Only check classes and functions; bare type aliases (Callable[...] etc.)
        # cannot carry docstrings in the traditional sense
        if not (inspect.isclass(obj) or inspect.isfunction(obj)):
            continue
        if obj.__doc__ is None:
            missing.append(name)
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
