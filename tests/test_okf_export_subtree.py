"""Subtree exports relocate body links without changing source bytes."""

import io
import zipfile

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.okf_export_paths import ExportPaths
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_subtree_relocates_and_reports(tmp_path):
    (tmp_path / "guides").mkdir()
    original = "---\ntype: Note\nresource: /guides/b.md\n---\n[a](/guides/b.md?x=1#h) [b](./b.md) [c](/other.md) [d](/guides/é.md) [e](</guides/my file.md>)\n".encode()
    (tmp_path / "guides/a.md").write_bytes(original)
    (tmp_path / "guides/b.md").write_text("# B\n")
    (tmp_path / "guides/é.md").write_text("é")
    (tmp_path / "guides/my file.md").write_text("b")
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert set(archive.namelist()) == {"a.md", "b.md", "é.md", "my file.md"}
            assert archive.read("a.md") == original.replace(
                b"/guides/b.md?x=1#h", b"/b.md?x=1#h"
            ).replace("/guides/é.md".encode(), "/é.md".encode()).replace(
                b"/guides/my file.md", b"/my file.md"
            )
        assert result.report.transformed == ["a.md"]
        assert [(d.path, d.target) for d in result.report.dependencies] == [
            ("a.md", "/other.md")
        ]
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
        "[ref]: /guides/b.md\n\n[ref] ![image](/guides/b.md) `[code](/guides/b.md)`\n"
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
                == b"[ref]: /b.md\n\n[ref] ![image](/b.md) `[code](/guides/b.md)`\n"
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
        b"```markdown\r\n[x](/guides/b.md)\r\n",
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
            b"[/guides/b.md]\r\r[/guides/b.md]: /guides/b.md\r",
            b"[/guides/b.md]\r\r[/guides/b.md]: /b.md\r",
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
        "https://exa mple.org",
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


def test_omitted_member_dependency_and_scope_index_version(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "guides/a.md").write_text("[hidden](/guides/.hidden.md)")
    (tmp_path / "guides/.hidden.md").write_text("# hidden")
    (tmp_path / "guides/index.md").write_text('---\nokf_version: "0.1"\n---\n')
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert set(archive.namelist()) == {"a.md", "index.md"}
            assert archive.read("index.md") == b'---\nokf_version: "0.1"\n---\n'
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
        (
            "> ```\r\n> [x](/guides/b.md)\r\n> ```\r\n",
            "> ```\r\n> [x](/guides/b.md)\r\n> ```\r\n",
        ),
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
def test_markdown_forms(body, expected):
    paths = ExportPaths("guides", {"guides/a.md", "guides/b.md"}, None)
    output, dependencies = paths.relocate(body, "guides/a.md")
    assert output == expected
    assert not dependencies


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
        (
            '[r]\n\n[r]: /guides/b.md "see /guides/b.md"\n',
            '[r]\n\n[r]: /b.md "see /guides/b.md"\n',
        ),
    ],
)
def test_container_cases(body, expected):
    paths = ExportPaths("guides", {"guides/a.md", "guides/b.md"}, None)
    output, dependencies = paths.relocate(body, "guides/a.md")
    assert output == expected
    assert not dependencies


def test_unmatched_backticks_remain_literal():
    body = " ".join("`" * i for i in range(1, 600))
    output, dependencies = ExportPaths("guides", {"guides/a.md"}, None).relocate(
        body, "guides/a.md"
    )
    assert output == body
    assert not dependencies


def test_nested_images_with_attachment_and_collision(tmp_path):
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
            assert "i.png" in archive.namelist()
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


def test_invalid_utf8_link_is_relocated(tmp_path):
    body = b"\xff[x](/guides/b.md)"
    output, report = _export_body(tmp_path, body)
    assert output == b"\xff[x](/b.md)"
    assert report.transformed == ["a.md"] and not report.dependencies
    assert any(f.reason == "invalid_encoding" for f in report.format_findings)


def test_escaped_backtick_does_not_open_code(tmp_path):
    body = b"\\` [x](/guides/b.md) `\n"
    output, report = _export_body(tmp_path, body)
    assert output == b"\\` [x](/b.md) `\n"
    assert report.transformed == ["a.md"]


def test_wikilinks_in_code_remain_literal(tmp_path):
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
def test_protected_boundaries(tmp_path, body, expected):
    output, _ = _export_body(tmp_path, body.encode())
    assert output == expected.encode()


@pytest.mark.parametrize("prefix", [b"> ", b"> > "])
def test_quoted_definition_relocation_and_collision(tmp_path, prefix):
    body = b"[ref]\r\n\r\n" + prefix + b'[ref]: </guides/b.md?x=1#h> "title"\r\n'
    output, report = _export_body(tmp_path, body)
    assert output == b"[ref]\r\n\r\n" + prefix + b'[ref]: </b.md?x=1#h> "title"\r\n'
    assert not report.dependencies
    assert (tmp_path / "guides/a.md").read_bytes() == body


@pytest.mark.parametrize("base", [None, "https://example.org/vault"])
def test_quoted_definition_outside_collision(tmp_path, base):
    body = b"[ref]\r\n\r\n> > [ref]: /b.md#part 'title'\r\n"
    if base is None:
        with pytest.raises(ValueError, match="collision"):
            _export_body(tmp_path, body)
    else:
        output, report = _export_body(tmp_path, body, external_base=base)
        assert (
            output
            == b"[ref]\r\n\r\n> > [ref]: https://example.org/vault/b.md#part 'title'\r\n"
        )
        assert [d.target for d in report.dependencies] == ["/b.md#part"]


@pytest.mark.parametrize("prefix", [b"- ", b"- item\n  - ", b"> - "])
@pytest.mark.parametrize("target", [b"/guides/b.md", b"/b.md"])
def test_list_fence_ends_with_container(tmp_path, prefix, target):
    quote = b"> " if prefix.startswith(b">") else b""
    indent = b"    " if b"item" in prefix else b"  "
    body = (
        prefix
        + b"```\n"
        + quote
        + indent
        + b"code\n"
        + quote
        + b"outside [x]("
        + target
        + b")\n"
    )
    if target == b"/b.md":
        with pytest.raises(ValueError, match="collision"):
            _export_body(tmp_path, body)
    else:
        output, _ = _export_body(tmp_path, body)
        assert output == body.replace(target, b"/b.md")
        assert (tmp_path / "guides/a.md").read_bytes() == body


def test_unicode_separator_is_not_line_end(tmp_path):
    body = "intro\u2028```\n[a](/guides/b.md)\n".encode()
    output, _ = _export_body(tmp_path, body)
    assert output == body.replace(b"/guides/b.md", b"/b.md")
    assert (tmp_path / "guides/a.md").read_bytes() == body


def test_list_fence_blank_lines_remain_literal(tmp_path):
    body = b"- ```\n\n\n  [x](/guides/b.md)\n  ```\noutside [x](/guides/b.md)\n"
    output, _ = _export_body(tmp_path, body)
    assert output == body.replace(b"outside [x](/guides/b.md)", b"outside [x](/b.md)")


@pytest.mark.parametrize(
    "body, files, expected",
    [
        ("[[guides/b]]", [], "[[b]]"),
        ("[[b]]", [], "[[b]]"),
        ("[[outside/b]]", ["guides/outside/b.md"], "[[outside/b]]"),
        ("[[b]]", ["guides/nested/b.md"], "[[b]]"),
        ("[[../guides/b#head|B]] `[[guides/b]]`", [], "[[b#head|B]] `[[guides/b]]`"),
    ],
)
def test_unindexed_wikilink_targets(tmp_path, body, files, expected):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(body)
    (tmp_path / "guides/b.md").write_text("# B")
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# target")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md").decode() == expected
    finally:
        vault.close()


@pytest.mark.parametrize("outside", ["outside/b.md", "x/b.md"])
def test_unindexed_wikilink_outside_winner_refused_or_externalized(tmp_path, outside):
    (tmp_path / "guides/nested").mkdir(parents=True)
    (tmp_path / "guides/outside").mkdir()
    (tmp_path / "guides/a.md").write_text(
        "[[outside/b]]" if outside.startswith("outside") else "[[b]]"
    )
    (tmp_path / "guides/outside/b.md").write_text("# B")
    (tmp_path / "guides/nested/b.md").write_text("# B")
    path = tmp_path / outside
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# outside")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, config, folder="guides")
        result = build_okf_bundle(
            vault, config, folder="guides", external_base="https://example.org/vault"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md").decode()
                == f"[{'outside/b' if outside.startswith('outside') else 'b'}](https://example.org/vault/{outside})"
            )
    finally:
        vault.close()


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"\xff [x](/guides/b.md)", b"\xff [x](/b.md)"),
        (b"\xff [x](/caf\xe9.md)", b"\xff [x](https://example.org/caf%E9.md)"),
        (b"\xff plain", b"\xff plain"),
    ],
)
def test_invalid_utf8_body_relocated_losslessly(tmp_path, body, expected):
    output, report = _export_body(tmp_path, body, external_base="https://example.org")
    assert output == expected
    assert any(f.reason == "invalid_encoding" for f in report.format_findings)
    assert bool(report.transformed) == (body != expected)


@pytest.mark.parametrize(
    "body",
    [
        b"\xff\xfe[\x00x\x00]",
        "[x](/guides/b.md)".encode("utf-16le"),
        b"text\x00[x](/guides/b.md)",
    ],
)
def test_uninspectable_body_copied_and_reported(tmp_path, body):
    output, report = _export_body(tmp_path, body)
    assert output == body
    assert report.uninspected["guides/a.md"] == "body_encoding"


def test_named_wikilink_nested_winner_and_outside_dependency(tmp_path):
    (tmp_path / "guides/nested").mkdir(parents=True)
    (tmp_path / "guides/a.md").write_text("[[b]] [[outside/c]]")
    (tmp_path / "guides/nested/b.md").write_text("# B")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/c.md").write_text("# C")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[[nested/b]] [[outside/c]]"
        assert [(d.path, d.target) for d in result.report.dependencies] == [
            ("a.md", "outside/c.md")
        ]
    finally:
        vault.close()


def test_relative_wikilinks_preserve_suffixes_and_collision():
    paths = ExportPaths(
        "guides", {"guides/s/a.md", "guides/b.md", "guides/s/img.png"}, None
    )
    body = "[[../b#heading|B]] ![[./img.png|image]] [[../../guides/b\\|table]]\n`[[../../guides/b]]` <!-- [[../../guides/b]] -->"
    output, dependencies = paths.relocate(body, "guides/s/a.md")
    assert (
        output
        == "[[../b#heading|B]] ![[./img.png|image]] [[b\\|table]]\n`[[../../guides/b]]` <!-- [[../../guides/b]] -->"
    )
    assert not dependencies
    with pytest.raises(ValueError, match="collision"):
        paths.relocate("[[../../b]]", "guides/s/a.md")


def test_inventory_is_lazy_and_refusal_names_link(tmp_path, monkeypatch):
    from dataclasses import replace

    from markdown_vault_mcp.bundle_inventory import BundleEntry

    (tmp_path / "guides").mkdir()
    note = tmp_path / "guides/a.md"
    note.write_text("ordinary text")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        original = vault.reader.inspect_bundle_tree
        calls = []

        def inspect(request):
            calls.append(request.root)
            page = original(request)
            if not request.root:
                failed = BundleEntry(
                    "outside", role="directory", state="unavailable", reason="denied"
                )
                return replace(page, enumeration="unavailable", entries=(failed,))
            return page

        monkeypatch.setattr(vault.reader, "inspect_bundle_tree", inspect)
        config = ProjectConfig(source_dir=tmp_path)
        build_okf_bundle(vault, config, folder="guides")
        assert calls == ["guides"]
        note.write_text("[[b]] [[c]]")
        with pytest.raises(
            ValueError, match=r"\[\[b\]\].*guides/a.md.*inventory unavailable.*outside"
        ):
            build_okf_bundle(vault, config, folder="guides")
    finally:
        vault.close()


def test_wikilink_resolution_agrees_with_index(tmp_path):
    for name in [
        "guides/a.md",
        "guides/b.md",
        "x/b.md",
        "outside/c.md",
        "long/d.md",
        "x/d.md",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Note\n")
    (tmp_path / "guides/a.md").write_text("[[b]] [[outside/c]] [[d]]")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        outlinks = vault.graph.get_outlinks("guides/a.md")
        resolved = {link.raw_target: link.target_path for link in outlinks}
        candidates = {
            "guides/a.md",
            "guides/b.md",
            "x/b.md",
            "outside/c.md",
            "long/d.md",
            "x/d.md",
        }
        paths = ExportPaths(
            "guides", {"guides/a.md", "guides/b.md"}, None, lambda: candidates
        )
        for name in ("b", "outside/c", "d"):
            assert (
                paths._wiki_match(name + ".md", "guides/a.md", candidates)
                == resolved[name]
            )
    finally:
        vault.close()


def test_wikilink_inventory_ignores_symlinks(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("[[b]]")
    (tmp_path / "guides/b.md").write_text("# B")
    (tmp_path / "b.md").symlink_to(tmp_path / "guides/b.md")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[[b]]"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "state", ["searchable", "non_admitted", "index_excluded", "stale"]
)
def test_wikilink_relocation_independent_of_search_state(tmp_path, state):
    (tmp_path / "guides/s").mkdir(parents=True)
    note = tmp_path / "guides/s/a.md"
    note.write_text(
        "---\ntitle: A\n---\n[[../../guides/b|B]]"
        if state == "searchable"
        else "[[../../guides/b|B]]"
    )
    (tmp_path / "guides/b.md").write_text("---\ntitle: B\n---\n# B")
    settings = VaultSettings(
        required_frontmatter=["title"],
        exclude_patterns=["guides/s/a.md"] if state == "index_excluded" else [],
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        if state == "stale":
            note.write_text("---\ntitle: A\n---\nold text")
        vault.index.build_index()
        if state == "stale":
            note.write_text("---\ntitle: A\n---\n[[../../guides/b|B]]")
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            include_index_excluded=True,
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            body = archive.read("s/a.md").decode()
        assert "[[b|B]]" in body or "[B](/b.md)" in body
    finally:
        vault.close()


def test_index_excluded_outside_wikilink_candidate(tmp_path):
    (tmp_path / "guides/outside").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "guides/a.md").write_text("[[outside/b]]")
    (tmp_path / "guides/outside/b.md").write_text("# selected")
    (tmp_path / "outside/b.md").write_text("# excluded")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(exclude_patterns=["outside/b.md"])
    )
    try:
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), folder="guides")
    finally:
        vault.close()


def test_named_wikilink_inventory_loaded_once():
    calls = []

    def inventory():
        calls.append(1)
        return {"guides/a.md", "guides/b.md", "guides/c.md"}

    paths = ExportPaths(
        "guides", {"guides/a.md", "guides/b.md", "guides/c.md"}, None, inventory
    )
    output, _ = paths.relocate("[[b]] [[c]]", "guides/a.md")
    assert output == "[[b]] [[c]]"
    assert calls == [1]


def test_indexed_outside_wikilink_collision(tmp_path):
    (tmp_path / "guides/outside").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "guides/a.md").write_text("[[outside/b]]")
    (tmp_path / "guides/outside/b.md").write_text("# selected")
    (tmp_path / "outside/b.md").write_text("# outside")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), folder="guides")
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            external_base="https://example.org",
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert "https://example.org/outside/b.md" in archive.read("a.md").decode()
    finally:
        vault.close()


def test_missing_wikilink_collision_and_invalid_dependency_text():
    paths = ExportPaths("guides", {"guides/a.md", "guides/b.md"}, None, lambda: set())
    with pytest.raises(ValueError, match="collision"):
        paths.relocate("[[b]]", "guides/a.md")
    output, dependencies = paths.relocate("[x](/caf\udce9.md)", "guides/a.md")
    assert output == "[x](/caf\udce9.md)"
    assert dependencies[0].target == r"/caf\udce9.md"


def test_relative_attachment_and_external_wikilinks():
    members = {"guides/s/a.md", "guides/b.png", "guides/b.md"}
    paths = ExportPaths("guides", members, "https://example.org/vault")
    output, dependencies = paths.relocate(
        "![[../../guides/b.png#fragment|B]] [[../../outside/b|B]] [[https://example.org]] [[#section]]",
        "guides/s/a.md",
    )
    assert (
        output
        == "![[b.png#fragment|B]] [B](https://example.org/vault/outside/b.md) [[https://example.org]] [[#section]]"
    )
    assert [d.target for d in dependencies] == ["outside/b.md"]


def test_wikilink_inventory_reads_all_pages_once(tmp_path, monkeypatch):
    from dataclasses import replace

    from markdown_vault_mcp.bundle_inventory import BundleTreeRequest

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("[[b]] [[b]]")
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        original = vault.reader.inspect_bundle_tree
        roots = []
        full_page = original(BundleTreeRequest(root=""))

        def inspect(request):
            if request.root:
                return original(request)
            roots.append(request.cursor)
            if request.cursor is None:
                return replace(
                    full_page, entries=full_page.entries[:1], next_cursor="page-two"
                )
            return replace(full_page, entries=full_page.entries[1:], next_cursor=None)

        monkeypatch.setattr(vault.reader, "inspect_bundle_tree", inspect)
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[[b]] [[b]]"
        assert roots == [None, "page-two"]
    finally:
        vault.close()


@pytest.mark.parametrize("indexed", [False, True])
def test_externalized_wikilinks_are_markdown_links_and_images(tmp_path, indexed):
    from markdown_it import MarkdownIt

    (tmp_path / "guides").mkdir()
    (tmp_path / "outside").mkdir()
    body = "[[outside/b#Head|Read B]] ![[outside/pic.png|Picture]]\n\n| [[outside/b\\|Cell]] |\n"
    (tmp_path / "guides/a.md").write_text(body)
    (tmp_path / "outside/b.md").write_text("# B")
    (tmp_path / "outside/pic.png").write_bytes(b"image")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        if indexed:
            vault.index.build_index()
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            external_base="https://example.org/vault",
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            text = archive.read("a.md").decode()
        html = MarkdownIt().render(text)
        assert 'href="https://example.org/vault/outside/b.md#Head"' in html
        assert 'src="https://example.org/vault/outside/pic.png"' in html
        assert "[Cell](https://example.org/vault/outside/b.md)" in text
        assert "[[https:" not in text
    finally:
        vault.close()


@pytest.mark.parametrize(
    "usage", ["[Ref Name]", "[text][REF   NAME]", "[Ref Name][]", "![alt][REF NAME]"]
)
def test_only_first_used_reference_definition_is_relocated(usage):
    body = (
        usage
        + '\n\n[ref name]: /guides/b.md "title"\n[REF  NAME]: /b.md\n[unused]: /b.md\n'
    )
    expected = body.replace('/guides/b.md "title"', '/b.md "title"')
    paths = ExportPaths("guides", {"guides/a.md", "guides/b.md"}, None)
    output, dependencies = paths.relocate(body, "guides/a.md")
    assert output == expected
    assert not dependencies


def test_hidden_wikilink_winner_is_selection_independent(tmp_path):
    (tmp_path / "guides/nested").mkdir(parents=True)
    (tmp_path / ".x").mkdir()
    (tmp_path / "guides/a.md").write_text("[[b]]")
    (tmp_path / "guides/nested/b.md").write_text("selected")
    (tmp_path / ".x/b.md").write_text("outside")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        assert vault.graph.get_outlinks("guides/a.md")[0].target_path == ".x/b.md"
        # Remove the indexed source so only a new, unindexed note is exported.
        (tmp_path / "guides/a.md").unlink()
        (tmp_path / "guides/new.md").write_text("[[b]]")
        for include_hidden in (False, True):
            with pytest.raises(ValueError, match="collision"):
                build_okf_bundle(
                    vault,
                    ProjectConfig(source_dir=tmp_path),
                    folder="guides",
                    include_hidden=include_hidden,
                )
    finally:
        vault.close()


@pytest.mark.parametrize("indexed", [False, True])
def test_wikilink_text_in_markdown_title_is_literal(tmp_path, indexed):
    (tmp_path / "guides").mkdir()
    original = '[x](/guides/b.md "[[outside]]")'
    (tmp_path / "guides/a.md").write_text(original)
    (tmp_path / "guides/b.md").write_text("B")
    (tmp_path / "outside.md").write_text("outside")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        if indexed:
            vault.index.build_index()
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            external_base="https://example.org",
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b'[x](/b.md "[[outside]]")'
        assert not result.report.dependencies
        assert (tmp_path / "guides/a.md").read_text() == original
    finally:
        vault.close()


@pytest.mark.parametrize(
    "body, expected, members, expected_targets",
    [
        (
            '[ref]\n\n[ref]: /guides/b.md "[[outside]]"\n',
            '[ref]\n\n[ref]: /b.md "[[outside]]"\n',
            {"guides/a.md", "guides/b.md"},
            [],
        ),
        (
            "[x](/guides/[[b]].md)",
            "[x](/[[b]].md)",
            {"guides/a.md", "guides/[[b]].md", "guides/b.md"},
            [],
        ),
        *[
            (body, expected, {"guides/a.md", "guides/b.md"}, targets)
            for body, expected, targets in [
                (
                    '[u]: /guides/b.md "[[outside]]"\n',
                    '[u]: /guides/b.md "[[outside]]"\n',
                    [],
                ),
                (
                    '[r]: /guides/b.md\n[r]: /x.md "[[outside]]"\n\n[r]\n',
                    '[r]: /b.md\n[r]: /x.md "[[outside]]"\n\n[r]\n',
                    [],
                ),
                ("[u]: /guides/[[outside]].md\n", "[u]: /guides/[[outside]].md\n", []),
                (
                    '[u]: </guides/[[outside]].md> "t"\n',
                    '[u]: </guides/[[outside]].md> "t"\n',
                    [],
                ),
                (
                    "[x](/guides/[[outside]].md)",
                    "[x](https://example.org/guides/%5B%5Boutside%5D%5D.md)",
                    ["/guides/[[outside]].md"],
                ),
                (
                    "[x](</guides/[[outside]] a.md>)",
                    "[x](<https://example.org/guides/%5B%5Boutside%5D%5D%20a.md>)",
                    ["</guides/[[outside]] a.md>"],
                ),
                ("[x](/guides/b.md ([[outside]]))", "[x](/b.md ([[outside]]))", []),
                ("[x](/guides/b.md '[[outside]]')", "[x](/b.md '[[outside]]')", []),
                (
                    '[r]: /guides/b.md "[[outside]]"\n\n[r]\n',
                    '[r]: /b.md "[[outside]]"\n\n[r]\n',
                    [],
                ),
                ('![i](/guides/b.md "[[outside]]")', '![i](/b.md "[[outside]]")', []),
            ]
        ],
        *[
            (
                body.replace("\n", newline),
                expected.replace("\n", newline),
                {"guides/a.md", "guides/b.md"},
                [],
            )
            for newline in ("\n", "\r\n")
            for body, expected in [
                (
                    '[u]: /guides/b.md\n  "[[outside]]"\n\n[u]\n',
                    '[u]: /b.md\n  "[[outside]]"\n\n[u]\n',
                ),
                (
                    '[u]: /guides/b.md\n  "[[outside]]"\n',
                    '[u]: /guides/b.md\n  "[[outside]]"\n',
                ),
                ('[x](/guides/b.md\n"[[outside]]")', '[x](/b.md\n"[[outside]]")'),
                ('[x](/guides/b.md "[[outside]]"\n)', '[x](/b.md "[[outside]]"\n)'),
                ("[x](\n/guides/b.md\n)", "[x](\n/b.md\n)"),
                ("[x](/guides/b.md\n'[[outside]]')", "[x](/b.md\n'[[outside]]')"),
                (
                    "para\n[x](/guides/b.md\n  ([[outside]]))",
                    "para\n[x](/b.md\n  ([[outside]]))",
                ),
                (
                    "[a](/guides/b.md\nfollowing text)",
                    "[a](/guides/b.md\nfollowing text)",
                ),
                ("[a]\n(/guides/b.md)", "[a]\n(/guides/b.md)"),
            ]
        ],
    ],
)
def test_wikilink_text_in_markdown_title_or_destination_is_literal(
    body, expected, members, expected_targets
):
    paths = ExportPaths(
        "guides", members, "https://example.org", lambda: members | {"outside.md"}
    )
    output, dependencies = paths.relocate(body, "guides/a.md")
    assert output == expected
    assert [dependency.target for dependency in dependencies] == expected_targets


def test_exact_selected_wikilink_needs_no_unrelated_inventory(tmp_path, monkeypatch):
    from dataclasses import replace

    from markdown_vault_mcp.bundle_inventory import BundleEntry

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("[[guides/b]]")
    (tmp_path / "guides/b.md").write_text("target")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        inspect = vault.reader.inspect_bundle_tree
        roots = []

        def unavailable_root(request):
            roots.append(request.root)
            page = inspect(request)
            if not request.root:
                denied = BundleEntry(
                    "unrelated", role="directory", state="unavailable", reason="denied"
                )
                return replace(page, enumeration="unavailable", entries=(denied,))
            return page

        monkeypatch.setattr(vault.reader, "inspect_bundle_tree", unavailable_root)
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[[b]]"
        assert not result.report.dependencies
        assert roots == ["guides"]
    finally:
        vault.close()


@pytest.mark.parametrize("target", ["outside/real.md", "guides/real.md"])
def test_unindexed_alias_without_filename_match_remains_unresolved(tmp_path, target):
    (tmp_path / "guides").mkdir()
    target_path = tmp_path / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("---\naliases: [Alias]\n---\n# Target\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        (tmp_path / "guides/a.md").write_text("[[Alias]] [missing](/nowhere.md)")
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            external_base="https://example.org",
        )
        vault.index.reindex()
        wikilinks = [
            link
            for link in vault.graph.get_outlinks("guides/a.md")
            if link.link_type == "wikilink"
        ]
        assert wikilinks[0].target_path == target
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md")
                == b"[[Alias]] [missing](https://example.org/nowhere.md)"
            )
        assert [d.target for d in result.report.dependencies] == [
            "Alias.md",
            "/nowhere.md",
        ]
    finally:
        vault.close()
