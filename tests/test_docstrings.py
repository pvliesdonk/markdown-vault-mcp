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


def test_collection_public_methods_have_docstrings():
    """Every public Collection method must have a docstring."""
    from markdown_vault_mcp.collection import Collection

    missing = [
        name
        for name, member in _public_members(Collection)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"Collection methods missing docstrings: {missing}"


def test_gitwritestrategy_public_methods_have_docstrings():
    """Every public GitWriteStrategy method must have a docstring."""
    from markdown_vault_mcp.git import GitWriteStrategy

    missing = [
        name
        for name, member in _public_members(GitWriteStrategy)
        if callable(member) and member.__doc__ is None
    ]
    assert not missing, f"GitWriteStrategy methods missing docstrings: {missing}"
