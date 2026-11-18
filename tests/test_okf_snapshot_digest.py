"""Snapshot identity for full OKF reads."""

from pathlib import Path

import pytest

from markdown_vault_mcp._okf_review import concept_snapshot


def test_snapshot_tracks_content_but_not_review_metadata() -> None:
    base = "---\ntype: concept\nsources: [a]\n---\nA [[link]]\n"
    digest = concept_snapshot(base)
    assert digest != concept_snapshot("\n" + base)
    assert digest == {
        "algo": "mvmcp-concept-v1",
        "digest": "63826077ebb4499344bbb5c7d104e57a4d22ca6608c552765cd071acfe1c0287",
    }
    assert digest == concept_snapshot(
        base.replace(
            "sources: [a]",
            "sources: [a]\nverified: [{by: human:x, at: 2026-01-01}]\nx-mvmcp: {reviews: []}",
        )
    )
    assert digest == concept_snapshot(base.replace("\n", "\r\n"))
    assert digest == concept_snapshot(base.replace("\n", "\r"))
    assert digest == concept_snapshot("\ufeff" + base)
    for changed in (
        base.replace("concept", "other"),
        base.replace("[a]", "[b]"),
        base.replace("[[link]]", "[[other]]"),
    ):
        assert concept_snapshot(changed) != digest


def test_yaml_types_and_mapping_order() -> None:
    date = "---\nvalue: 2026-01-01\n---\ntext\n"
    assert concept_snapshot(date) != concept_snapshot(
        date.replace("2026-01-01", '"2026-01-01"')
    )
    integer = "---\nvalue: 12\n---\ntext\n"
    assert concept_snapshot(integer) != concept_snapshot(integer.replace("12", '"12"'))
    first = "---\na: 'hello'\nb: 3\n---\nBody\n"
    second = "---\nb: 3\na: hello\n---\nBody\n"
    assert concept_snapshot(first) == concept_snapshot(second)
    typed = "---\na: !!omap [{x: 1}]\nb: !!pairs [{y: 2}]\nc: !!set {a: null}\nd: !!binary aGk=\ne: 2026-01-01T12:00:00Z\n---\nBody\n"
    assert concept_snapshot(typed) == {
        "algo": "mvmcp-concept-v1",
        "digest": "a64e28b7fdbff230d3617382f0673d6da6d87c71345004d64b82f14041ba46d9",
    }


@pytest.mark.parametrize("changed", ["Body", "Body\n\n", " Body\n", "\nBody\n"])
def test_body_whitespace_is_part_of_snapshot(changed: str) -> None:
    base = "---\ntype: concept\n---\nBody\n"
    assert concept_snapshot(base) != concept_snapshot(base.replace("Body\n", changed))


@pytest.mark.parametrize(
    "malformed", ["---\ntype: [\n---\nBody\n", '{\n"type": [\n}\nBody\n']
)
def test_concurrent_malformed_content_reports_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: str
) -> None:
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.exceptions import DocumentUnreadableError
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (tmp_path / "note.md").write_text("---\ntype: concept\n---\nBody\n")
    vault = Vault(source_dir=tmp_path)
    monkeypatch.setattr(
        "markdown_vault_mcp.managers.document._read_text_utf8",
        lambda _path: malformed,
    )
    try:
        with pytest.raises(DocumentUnreadableError, match=r"note\.md"):
            read_payload(vault, "note.md")
    finally:
        vault.close()


def test_full_read_snapshot_wiring(tmp_path: Path) -> None:
    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (tmp_path / "note.md").write_text("---\ntype: concept\n---\nBody\n")
    vault = Vault(source_dir=tmp_path)
    try:
        payload = read_payload(vault, "note.md")
        assert payload["okf"]["snapshot"] == concept_snapshot(payload["content"])
    finally:
        vault.close()


@pytest.mark.parametrize(
    "text", ["---\ntype: incomplete\n", "---\nverified: []\n---\u2028body\n"]
)
def test_body_without_complete_frontmatter_is_hashed_verbatim(text: str) -> None:
    assert concept_snapshot(text) != concept_snapshot(
        text + "extra\n"
        if "incomplete" in text
        else text.replace("verified", "x-mvmcp")
    )


@pytest.mark.parametrize(
    "value", ["2026-01-01T00:00:00.1234561Z", "9007199254740992.0"]
)
def test_yaml_precision_changes_snapshot(value: str) -> None:
    note = f"---\nvalue: {value}\n---\nbody\n"
    assert concept_snapshot(note) != concept_snapshot(
        note.replace(
            value,
            value.replace("1Z", "2Z") if value.endswith("Z") else "9007199254740993.0",
        )
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("---\na: 1\na: 2\n---\nBody\n", "---\na: 2\na: 1\n---\nBody\n"),
        (
            "---\nkeys: {1: first, 01: second}\n---\nBody\n",
            "---\nkeys: {01: second, 1: first}\n---\nBody\n",
        ),
        ('{\n"a": 1,\n"a": 2\n}\nBody\n', '{\n"a": 2,\n"a": 1\n}\nBody\n'),
    ],
)
def test_duplicate_keys_keep_precedence(before: str, after: str) -> None:
    assert concept_snapshot(before) != concept_snapshot(after)


def test_excluded_fields_in_yaml_merge_do_not_change_snapshot() -> None:
    base = (
        "---\nbase: &b {verified: [{by: human:x}], type: concept}\n<<: *b\n---\nBody\n"
    )
    changed = base.replace(
        "<<: *b", "<<: *b\nverified: [{by: human:y}]\nx-mvmcp: {reviews: []}"
    )
    assert concept_snapshot(base) == concept_snapshot(changed)


def test_json_numbers_retain_spelling_and_type() -> None:
    note = '{\n"value": 9007199254740992.0, "items": [1, 2]\n}\nBody\n'
    assert concept_snapshot(note) == {
        "algo": "mvmcp-concept-v1",
        "digest": "81feca05bda38c66bc132a1bcb22cc85ed759cf7982378aa9f9ff633bd05f4fb",
    }
    assert concept_snapshot(note) != concept_snapshot(
        note.replace("9007199254740992.0", "9007199254740993.0")
    )
    assert concept_snapshot(note) != concept_snapshot(
        note.replace("9007199254740992.0", '"9007199254740992.0"')
    )


def test_non_yaml_json_handler_values_are_hashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise a synthetic registered TOML handler without installing extras."""
    import re
    import tomllib

    import frontmatter
    from frontmatter.default_handlers import BaseHandler

    class TomlHandler(BaseHandler):
        FM_BOUNDARY = re.compile(r"^\+{3,}\s*$", re.MULTILINE)

        def load(self, fm: str, **_kwargs: object) -> object:
            return tomllib.loads(fm)

    monkeypatch.setattr(frontmatter, "handlers", [*frontmatter.handlers, TomlHandler()])
    assert concept_snapshot("Body\n") == concept_snapshot(
        "+++\nverified = []\n+++\nBody\n"
    )
    note = '+++\ntype = "concept"\nitems = [1, 2]\nvalue = 1.5\nat = 2026-01-01T12:00:00Z\n+++\nBody\n'
    assert concept_snapshot(note) == concept_snapshot(
        note.replace('type = "concept"', 'type = "concept"\nverified = []')
    )
    assert concept_snapshot(note) != concept_snapshot(
        note.replace('"concept"', '"other"')
    )


@pytest.mark.parametrize(
    "header",
    [
        "---\n{}\n---\n",
        "---\nnull\n---\n",
        "---\nfalse\n---\n",
        "---\n0\n---\n",
        "---\n[]\n---\n",
        "{\n}\n",
        "---\nverified: []\n---\n",
        '{\n"verified": []\n}\n',
    ],
)
def test_review_only_frontmatter_matches_plain_read(header: str) -> None:
    assert concept_snapshot(header + "Body\n") == concept_snapshot("Body\n")


def test_full_read_accepts_reader_c_loader_syntax(tmp_path: Path) -> None:
    import yaml
    from frontmatter.default_handlers import SafeLoader

    from markdown_vault_mcp._cli_read import read_payload
    from markdown_vault_mcp.vault import Vault

    if not hasattr(yaml, "CSafeLoader") or SafeLoader is not yaml.CSafeLoader:
        pytest.skip("reader does not use the C YAML loader")
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\nIndex\n')
    (tmp_path / "note.md").write_text("---\nstatus:\tdraft\n---\nBody\n")
    vault = Vault(source_dir=tmp_path)
    try:
        assert (
            read_payload(vault, "note.md")["okf"]["snapshot"]["algo"]
            == "mvmcp-concept-v1"
        )
    finally:
        vault.close()
