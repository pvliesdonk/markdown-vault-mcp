"""Subtree exports relocate body links without changing source bytes."""

import io
import zipfile

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_subtree_relocates_and_reports(tmp_path):
    (tmp_path / "guides").mkdir()
    original = b"---\ntype: Note\nresource: /guides/b.md\n---\n[a](/guides/b.md?x=1#h) [b](b.md) [c](/other.md)\n"
    (tmp_path / "guides/a.md").write_bytes(original)
    (tmp_path / "guides/b.md").write_text("# B\n")
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert set(archive.namelist()) == {"a.md", "b.md"}
            assert archive.read("a.md") == original.replace(
                b"/guides/b.md?x=1#h", b"/b.md?x=1#h"
            )
        assert result.report.transformed == ["a.md"]
        assert result.report.dependencies[0].target == "/other.md"
        assert (tmp_path / "guides/a.md").read_bytes() == original
    finally:
        vault.close()


def test_collision_refused_or_externalized(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("[x](/b.md#part)")
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, config, folder="guides")
        result = build_okf_bundle(
            vault, config, folder="guides", external_base="https://example.org/vault/"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[x](https://example.org/vault/b.md#part)"
    finally:
        vault.close()


def test_reference_image_and_code(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(
        "[ref]: /guides/b.md\n\n![image](/guides/b.md) `[code](/guides/b.md)`\n"
    )
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md")
                == b"[ref]: /b.md\n\n![image](/b.md) `[code](/guides/b.md)`\n"
            )
    finally:
        vault.close()


@pytest.mark.parametrize("encoded,name", [("%23", "#"), ("%3F", "?")])
def test_encoded_delimiters_in_filename(tmp_path, encoded, name):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(f"[x](/guides/b{encoded}c.md?q=1#h)")
    (tmp_path / f"guides/b{name}c.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md").decode() == f"[x](/b{encoded}c.md?q=1#h)"
    finally:
        vault.close()


def _export_body(tmp_path, body, **kwargs):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_bytes(body)
    (tmp_path / "guides/b.md").write_bytes(b"# B\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides", **kwargs
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            return archive.read("a.md"), result.report
    finally:
        vault.close()


@pytest.mark.parametrize(
    "body",
    [
        b"    [x](/guides/b.md)\n",
        b"```markdown\n[x](/guides/b.md)\n",
        b"<!-- [x](/guides/b.md) -->\n",
        b"    [x](/b.md)\n",
    ],
)
def test_code_and_comments_preserved(tmp_path, body):
    output, report = _export_body(tmp_path, body)
    assert output == body
    assert not report.transformed and not report.dependencies


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            b"[link][ref]\r\n\r\n[ref]: /guides/b.md\r\n",
            b"[link][ref]\r\n\r\n[ref]: /b.md\r\n",
        ),
        (
            b"[/guides/b.md]\n\n[/guides/b.md]: /guides/b.md\n",
            b"[/guides/b.md]\n\n[/guides/b.md]: /b.md\n",
        ),
        (b"[x](../guides/b.md)", b"[x](b.md)"),
        (b"[x](/../guides/b.md)", b"[x](/b.md)"),
    ],
)
def test_reference_and_reentry(tmp_path, body, expected):
    output, _ = _export_body(tmp_path, body)
    assert output == expected


def test_clamped_collision(tmp_path):
    (tmp_path / "b.md").write_text("# Outside")
    with pytest.raises(ValueError, match="collision"):
        _export_body(tmp_path, b"[x](../../b.md)")


@pytest.mark.parametrize(
    "base",
    [
        "relative",
        "/root",
        "//host/path",
        "https://",
        "",
        "https://example.org/base?x=1",
        "https://example.org/base#frag",
        "https://example.org:",
        "https://example.org:bad",
    ],
)
def test_invalid_external_base(tmp_path, base):
    with pytest.raises(ValueError, match="external_base"):
        _export_body(tmp_path, b"[x](/b.md)", external_base=base)


def test_scope_index_version_and_transformed_wikilink(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "guides/index.md").write_text('---\nokf_version: "0.1"\n---\n')
    (tmp_path / "guides/a.md").write_text("[[b]]\n")
    (tmp_path / "guides/b.md").write_text("# B\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("index.md") == b'---\nokf_version: "0.1"\n---\n'
            assert archive.read("a.md") == b"[b](/b.md)\n"
        assert result.report.transformed == ["a.md"]
    finally:
        vault.close()


def test_omitted_member_dependency_and_no_root_version(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "guides/a.md").write_text("[hidden](/guides/.hidden.md)")
    (tmp_path / "guides/.hidden.md").write_text("# hidden")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.namelist() == ["a.md"]
            assert archive.read("a.md") == b"[hidden](/guides/.hidden.md)"
        assert [d.target for d in result.report.dependencies] == ["/guides/.hidden.md"]
        assert not result.report.transformed
    finally:
        vault.close()


def test_deep_relative_reentry(tmp_path):
    (tmp_path / "guides/s").mkdir(parents=True)
    (tmp_path / "guides/s/a.md").write_text("[x](../../guides/b.md?raw=1#part)")
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("s/a.md") == b"[x](../b.md?raw=1#part)"
    finally:
        vault.close()


def test_external_anchor_pointy_and_footnote(tmp_path):
    body = b"[^note]: words\n\n[x](https://example.org/a) [anchor](#part) [pointy](</guides/b.md>)\n"
    output, report = _export_body(tmp_path, body)
    assert (
        output
        == b"[^note]: words\n\n[x](https://example.org/a) [anchor](#part) [pointy](</b.md>)\n"
    )
    assert not report.dependencies


@pytest.mark.parametrize(
    "body,expected",
    [
        ("> ```\n> [x](/guides/b.md)\n> ```\n", "> ```\n> [x](/guides/b.md)\n> ```\n"),
        ("```\n<!--\n```\n\n[x](/guides/b.md)\n", "```\n<!--\n```\n\n[x](/b.md)\n"),
        ("`inline\n[x](/guides/b.md)`\n", "`inline\n[x](/guides/b.md)`\n"),
        ("`<!--` [x](/guides/b.md)\n", "`<!--` [x](/b.md)\n"),
        ("<pre>\n[x](/guides/b.md)\n</pre>\n", "<pre>\n[x](/guides/b.md)\n</pre>\n"),
        ("- item\n\n    - [x](/guides/b.md)\n", "- item\n\n    - [x](/b.md)\n"),
        ("- item\n\n      [x](/guides/b.md)\n", "- item\n\n      [x](/guides/b.md)\n"),
        (
            "- item\n  ```\n  <!--\n  ```\n  [x](/guides/b.md)\n",
            "- item\n  ```\n  <!--\n  ```\n  [x](/b.md)\n",
        ),
        ("[![i](/guides/b.md)](/guides/b.md)\n", "[![i](/b.md)](/b.md)\n"),
        ("![a [b](/guides/b.md)](/guides/b.md)\n", "![a [b](/b.md)](/b.md)\n"),
    ],
)
def test_replan_markdown_forms(tmp_path, body, expected):
    output, report = _export_body(tmp_path, body.encode())
    assert output == expected.encode()
    assert report.transformed == (["a.md"] if body != expected else [])
    assert not report.dependencies
    assert (tmp_path / "guides/a.md").read_text() == body


def test_replan_spelling_and_report_paths(tmp_path):
    (tmp_path / "guides").mkdir()
    body = "[a](/guides/é.md) [b](</guides/my file.md>) [c](/missing.md)"
    (tmp_path / "guides/a.md").write_text(body)
    (tmp_path / "guides/é.md").write_text("é")
    (tmp_path / "guides/my file.md").write_text("b")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md").decode()
                == "[a](/é.md) [b](</my file.md>) [c](/missing.md)"
            )
        assert result.report.transformed == ["a.md"]
        assert [(d.path, d.target) for d in result.report.dependencies] == [
            ("a.md", "/missing.md")
        ]
        assert (tmp_path / "guides/a.md").read_text() == body
    finally:
        vault.close()


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "<script>\n[x](/guides/b.md)\n</script>\n",
            "<script>\n[x](/guides/b.md)\n</script>\n",
        ),
        (
            "<style>\n[x](/guides/b.md)\n</style>\n",
            "<style>\n[x](/guides/b.md)\n</style>\n",
        ),
        (
            "<textarea>\n[x](/guides/b.md)\n</textarea>\n",
            "<textarea>\n[x](/guides/b.md)\n</textarea>\n",
        ),
        ("- item\n\n\t- [x](/guides/b.md)\n", "- item\n\n\t- [x](/b.md)\n"),
        ("1. item\n\n    - [x](/guides/b.md)\n", "1. item\n\n    - [x](/b.md)\n"),
        ("- item\n\n    [x](/guides/b.md)\n", "- item\n\n    [x](/b.md)\n"),
        ("- item\n  text\n[x](/guides/b.md)\n", "- item\n  text\n[x](/b.md)\n"),
        ("1. item\n\n       [x](/b.md)\n", "1. item\n\n       [x](/b.md)\n"),
        ("- item\n\n\t  [x](/b.md)\n", "- item\n\n\t  [x](/b.md)\n"),
        ("-     [x](/b.md)\n", "-     [x](/b.md)\n"),
        (
            "- parent\n  - child\n\n        [x](/b.md)\n\n- next\n\n      [y](/b.md)\n",
            "- parent\n  - child\n\n        [x](/b.md)\n\n- next\n\n      [y](/b.md)\n",
        ),
        (
            "<!--\n[x](/guides/b.md)\n-->\n[x](/guides/b.md)\n",
            "<!--\n[x](/guides/b.md)\n-->\n[x](/b.md)\n",
        ),
        (
            "<!-- [x](/guides/b.md) --> [y](/guides/b.md)\n",
            "<!-- [x](/guides/b.md) --> [y](/b.md)\n",
        ),
        ("> ```\n> [x](/b.md)\n> ```\n", "> ```\n> [x](/b.md)\n> ```\n"),
        ('[r]: /guides/b.md "see /guides/b.md"\n', '[r]: /b.md "see /guides/b.md"\n'),
    ],
)
def test_replan_container_cases(tmp_path, body, expected):
    output, report = _export_body(tmp_path, body.encode())
    assert output == expected.encode()
    assert report.transformed == (["a.md"] if body != expected else [])
    assert not report.dependencies
    assert (tmp_path / "guides/a.md").read_text() == body


def test_replan_unmatched_backticks_are_bounded(tmp_path):
    import time

    body = " ".join("`" * i for i in range(1, 600))
    started = time.monotonic()
    output, _ = _export_body(tmp_path, body.encode())
    assert output == body.encode()
    assert time.monotonic() - started < 5


def test_replan_nested_images_with_attachment_and_collision(tmp_path):
    (tmp_path / "guides").mkdir()
    body = "[![i](/guides/i.png)](/guides/b.md) ![a [b](/guides/b.md)](/guides/i.png)"
    (tmp_path / "guides/a.md").write_text(body)
    (tmp_path / "guides/b.md").write_text("# B")
    (tmp_path / "guides/i.png").write_bytes(b"image")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(attachment_extensions=["png"])
    )
    try:
        config = ProjectConfig(source_dir=tmp_path, attachment_extensions=("png",))
        result = build_okf_bundle(vault, config, folder="guides")
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md") == b"[![i](/i.png)](/b.md) ![a [b](/b.md)](/i.png)"
            )
        assert result.report.transformed == ["a.md"]
        assert not result.report.dependencies
        (tmp_path / "guides/a.md").write_text("[![i](/i.png)](/guides/b.md)")
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, config, folder="guides")
    finally:
        vault.close()


def test_replan_invalid_utf8_is_copied(tmp_path):
    body = b"\xff[x](/guides/b.md)"
    output, report = _export_body(tmp_path, body)
    assert output == body
    assert not report.transformed and not report.dependencies
    assert any(f.reason == "invalid_encoding" for f in report.format_findings)


def test_replan_escaped_backtick_does_not_open_code(tmp_path):
    body = b"\\` [x](/guides/b.md) `\n"
    output, report = _export_body(tmp_path, body)
    assert output == b"\\` [x](/b.md) `\n"
    assert report.transformed == ["a.md"]


def test_replan_wikilinks_in_code_remain_literal(tmp_path):
    (tmp_path / "guides").mkdir()
    body = "[[b]]\n\n```\n[[b]]\n```\n"
    (tmp_path / "guides/a.md").write_text(body)
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[b](/b.md)\n\n```\n[[b]]\n```\n"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "body, expected",
    [
        (
            "> ```\n> [x](/guides/b.md)\n\n[y](/guides/b.md)\n",
            "> ```\n> [x](/guides/b.md)\n\n[y](/b.md)\n",
        ),
        (
            "<pre>[x](/guides/b.md)</pre>\n\n[y](/guides/b.md)\n",
            "<pre>[x](/guides/b.md)</pre>\n\n[y](/b.md)\n",
        ),
        ("text <!-- unclosed [x](/guides/b.md)\n", "text <!-- unclosed [x](/b.md)\n"),
    ],
)
def test_replan_protected_boundaries(tmp_path, body, expected):
    output, _ = _export_body(tmp_path, body.encode())
    assert output == expected.encode()
