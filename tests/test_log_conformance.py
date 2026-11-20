"""The AST conformance check over a source tree (#2340).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_log_conformance.py``. Its
``test_public_export`` pinned pvl-core's exports, and
``test_pvl_core_follows_its_own_grammar`` is what
``tests/test_logging_standard.py`` does for this project's ``src/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_vault_mcp._log_conformance import find_nonconforming_log_calls

PREAMBLE = "import logging\n\nlogger = logging.getLogger(__name__)\n\n"


def write(tmp_path: Path, body: str, name: str = "mod.py") -> Path:
    path = tmp_path / name
    path.write_text(PREAMBLE + body)
    return path


def reasons(tmp_path: Path) -> list[str]:
    return [v.reason for v in find_nonconforming_log_calls(tmp_path)]


def test_conforming_calls_produce_no_violations(tmp_path):
    write(
        tmp_path,
        'logger.info("server_started port=%d", 8000)\n'
        'logger.debug("cache_hit key=%s ttl=%d", "k", 60)\n'
        'logger.warning("epo_ops status=configured")\n',
    )
    assert find_nonconforming_log_calls(tmp_path) == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ('logger.info(f"started on {8000}")', "f-string"),
        ("logger.info(MESSAGE)", "non-literal-message"),
        ('logger.info("event " + "tail")', "non-literal-message"),
        ("logger.info()", "no-arguments"),
        ('logger.info("event key=%s", *args)', "starred-arguments"),
        ('logger.info("Service started")', "non-conforming-template"),
        ('logger.info("event key=%s")', "argument-count-mismatch"),
        ('logger.info("event key=%s", 1, 2)', "argument-count-mismatch"),
    ],
)
def test_each_violation_kind_is_reported(tmp_path, body, reason):
    write(tmp_path, body + "\n")
    assert reasons(tmp_path) == [reason]


def test_keyword_arguments_are_not_counted_as_values(tmp_path):
    write(tmp_path, 'logger.error("event key=%s", "v", exc_info=True)\n')
    assert find_nonconforming_log_calls(tmp_path) == []


def test_every_level_method_is_checked(tmp_path):
    write(
        tmp_path,
        "\n".join(
            f'logger.{level}("Prose here")'
            for level in ("debug", "info", "warning", "error", "exception", "critical")
        )
        + "\n",
    )
    assert reasons(tmp_path) == ["non-conforming-template"] * 6


def test_implicit_string_concatenation_is_one_literal(tmp_path):
    write(
        tmp_path,
        'logger.info(\n    "event key=%s"\n    " other=%s",\n    1,\n    2,\n)\n',
    )
    assert find_nonconforming_log_calls(tmp_path) == []


def test_calls_on_other_receivers_are_ignored(tmp_path):
    write(
        tmp_path,
        "import click\n"
        'click.echo("Anything at all")\n'
        'typer.secho("Prose is fine here")\n'
        'self.console.info("Prose here too")\n',
    )
    assert find_nonconforming_log_calls(tmp_path) == []


def test_any_name_bound_to_getlogger_is_a_receiver(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import logging\n\n_audit = logging.getLogger('audit')\n\n"
        "_audit.info('Prose here')\n"
    )
    assert reasons(tmp_path) == ["non-conforming-template"]


def test_annotated_receiver_is_a_receiver(tmp_path):
    """``logger: logging.Logger = logging.getLogger(__name__)`` is the
    spec-mandated receiver form with a type annotation. ``ast.AnnAssign`` is
    a different node type from ``ast.Assign`` with a single ``target``
    rather than a ``targets`` list; before handling it, a file using only
    this form was silently skipped in full — a false negative in a build
    gate.
    """
    (tmp_path / "mod.py").write_text(
        "import logging\n\n"
        "logger: logging.Logger = logging.getLogger(__name__)\n\n"
        'logger.info("Prose here")\n'
    )
    assert reasons(tmp_path) == ["non-conforming-template"]


def test_violation_carries_path_line_and_template(tmp_path):
    path = write(tmp_path, '\nlogger.info("Service started")\n')
    (violation,) = find_nonconforming_log_calls(tmp_path)
    assert violation.path == path
    assert violation.line == 6
    assert violation.template == "Service started"


def test_results_are_sorted_by_path_then_line(tmp_path):
    """The final ``sorted(...)`` in the implementation is load-bearing, not
    decorative. ``ast.walk`` is breadth-first: for a module with a call
    nested inside a function defined *before* a later module-level call, it
    yields the module-level call first even though it has the higher line
    number. A test built only from module-level calls (as this test used to
    be) can't tell a real sort from a no-op, because ``ast.walk`` already
    hands those back in line order on its own.
    """
    body = (
        "def helper():\n"
        '    logger.info("prose in function")\n'
        "\n\n"
        'logger.info("prose at module level")\n'
    )
    write(tmp_path, body, name="b.py")
    write(tmp_path, body, name="a.py")
    found = find_nonconforming_log_calls(tmp_path)
    assert [(v.path.name, v.line) for v in found] == [
        ("a.py", 6),
        ("a.py", 9),
        ("b.py", 6),
        ("b.py", 9),
    ]


def test_scans_subdirectories(tmp_path):
    (tmp_path / "pkg").mkdir()
    write(tmp_path / "pkg", 'logger.info("Prose here")\n', name="deep.py")
    assert reasons(tmp_path) == ["non-conforming-template"]


def test_non_python_files_are_ignored(tmp_path):
    (tmp_path / "notes.txt").write_text('logger.info("Prose here")\n')
    assert find_nonconforming_log_calls(tmp_path) == []


def test_syntax_error_propagates(tmp_path):
    (tmp_path / "broken.py").write_text("def (:\n")
    with pytest.raises(SyntaxError):
        find_nonconforming_log_calls(tmp_path)


def test_missing_root_raises(tmp_path):
    """A build gate calls this expecting ``== []``. A wrong or stale path
    must not silently scan nothing and report clean.
    """
    with pytest.raises(NotADirectoryError):
        find_nonconforming_log_calls(tmp_path / "does-not-exist")


def test_root_that_is_a_file_raises(tmp_path):
    path = tmp_path / "not_a_dir.py"
    path.write_text('logger.info("Prose here")\n')
    with pytest.raises(NotADirectoryError):
        find_nonconforming_log_calls(path)


def test_non_ascii_source_is_read_independent_of_locale(tmp_path, monkeypatch):
    """The checker must decode source the way the interpreter does (bytes
    through ``ast.parse``, honouring PEP 263 / a BOM), not via
    ``Path.read_text()``, which decodes with the *locale* encoding. Under a
    non-UTF-8 locale (e.g. ``LC_ALL=C``), a file containing a non-ASCII
    identifier or string would otherwise raise ``UnicodeDecodeError``.

    Reproducing a real non-UTF-8 locale from inside a running interpreter
    isn't reliable (the encoding is resolved once at process start, and
    Python's C-locale coercion silently upgrades ``LC_ALL=C`` back to
    UTF-8 on many platforms — see PEP 538). Instead, this makes
    ``Path.read_text`` itself explode, which proves this code path never
    calls it: with the pre-fix ``path.read_text()`` implementation, this
    test fails with the injected error instead of returning a violation.
    """

    def read_text_must_not_be_called(_self, *_args, **_kwargs):
        raise UnicodeDecodeError(
            "ascii", b"", 0, 1, "read_text must not be used to decode source"
        )

    write(tmp_path, 'logger.info("café_event key=%s", 1)\n')
    monkeypatch.setattr(Path, "read_text", read_text_must_not_be_called)

    assert reasons(tmp_path) == ["non-conforming-template"]
