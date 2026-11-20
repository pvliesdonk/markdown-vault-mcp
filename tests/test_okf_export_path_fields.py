"""Path-valued frontmatter relocates without reserializing the document."""

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.okf_export import export_note


def export(tmp_path, text, *, source="a.md", **kwargs):
    (tmp_path / "guides").mkdir(exist_ok=True)
    (tmp_path / "guides/sub").mkdir(exist_ok=True)
    (tmp_path / "guides/empty").mkdir(exist_ok=True)
    (tmp_path / "guides/hidden").mkdir(exist_ok=True)
    (tmp_path / "guides/hidden/.secret").write_text("hidden")
    (tmp_path / "guides/sub/c.md").write_text("# C\n")
    output, report = export_note(
        tmp_path, text.encode(errors="surrogateescape"), source=source, **kwargs
    )
    return output.decode(errors="surrogateescape"), report


def test_fields_preserve_yaml_and_source(tmp_path):
    text = """---
# keep comment
resource: '/guides/b.md' # keep
computation: /guides/b.md
executor: {resource: "/guides/b.md", extra: 42}
attester:
  resource: ./b.md
sources:
  - resource: /guides/b.md
  - resource: https://example.org/x
  - resource: {scope: references/}
unknown: /guides/b.md
---
Body [link](/guides/b.md)
"""
    output, report = export(tmp_path, text)
    expected = (
        text.replace("resource: '/guides/b.md'", "resource: '/b.md'")
        .replace("computation: /guides/b.md", "computation: /b.md")
        .replace('resource: "/guides/b.md"', 'resource: "/b.md"')
        .replace("- resource: /guides/b.md", "- resource: /b.md")
        .replace("[link](/guides/b.md)", "[link](/b.md)")
    )
    assert output == expected
    assert report.transformed == ["a.md"]
    assert (tmp_path / "guides/a.md").read_text() == text


def test_ambiguous_and_outside(tmp_path):
    text = "---\nresource: references/b.md\nsources:\n  - resource: /outside.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert not report.ambiguous_path
    assert [(x.path, x.target) for x in report.dependencies] == [
        ("a.md", "references/b.md"),
        ("a.md", "/outside.md"),
    ]


def test_unrelated_alias_does_not_block_relocation(tmp_path):
    text = "---\nunknown: &keep value\ncopy: *keep\nresource: /guides/b.md\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text.replace("resource: /guides/b.md", "resource: /b.md")


def test_shared_path_alias_preserves_unknown_value(tmp_path):
    text = "---\nresource: &path /guides/b.md\nunknown: *path\n---\n"
    output, _ = export(tmp_path, text)
    import yaml

    metadata = yaml.safe_load(output.split("---")[1])
    assert metadata["resource"] == "/b.md"
    assert metadata["unknown"] == "/guides/b.md"


def test_deep_metadata_copied_and_reported(tmp_path):
    large = "---\ntags:\n" + "".join(f"  - tag{i}\n" for i in range(4100)) + "---\n"
    assert export(tmp_path, large)[0] == large
    text = "---\nunknown: " + "[" * 1100 + "x" + "]" * 1100 + "\nresource: /b.md\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_alias_expansion_over_budget_is_copied(tmp_path):
    lines = ["v0: &v0 [x]"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 13))
    lines += ["defaults: &d {resource: /guides/b.md, extra: *v12}", "executor: *d"]
    text = "---\n" + "\n".join(lines) + "\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_recursive_alias_copied_and_reported(tmp_path):
    text = "---\ndefaults: &d {resource: /guides/b.md, extra: *d}\nexecutor: *d\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_alias_graph_traversal_visits_nodes_linearly(tmp_path):
    import yaml

    from markdown_vault_mcp.okf_export_paths import _relevant_nodes

    text = "a0: &a0 [x]\n" + "".join(
        f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]\n" for i in range(1, 15)
    )
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    assert isinstance(root, yaml.nodes.MappingNode)
    visits = [0]

    class CountingList(list):
        def __iter__(self):
            visits[0] += 1
            return super().__iter__()

    seen = set()

    def instrument(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, yaml.nodes.MappingNode):
            for key, value in node.value:
                instrument(key)
                instrument(value)
        elif isinstance(node, yaml.nodes.SequenceNode):
            for child in node.value:
                instrument(child)
            node.value = CountingList(node.value)

    instrument(root)
    _relevant_nodes(root, set())
    assert visits[0] < 200
    lines = ["v0: &v0 {resource: /guides/b.md}"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 15))
    text = "---\n" + "\n".join([*lines, "sources: *v14"]) + "\n---\n"
    assert export(tmp_path, text)[0] == text


@pytest.mark.parametrize(
    "written", ["/guides/my%20file.md?x=1#s", "/guides/my file.md?x=1#s"]
)
def test_uri_selected_path_and_suffix(tmp_path, written):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/my file.md").write_text("B")
    output, report = export(tmp_path, f'---\nresource: "{written}"\n---\n')
    expected = "/my%20file.md?x=1#s" if "%" in written else "/my file.md?x=1#s"
    assert expected in output
    assert not report.dependencies


def test_collision_is_checked_before_over_budget_copy(tmp_path):
    lines = ["v0: &v0 [x]"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 13))
    lines += ["defaults: &d {resource: /b.md, extra: *v12}", "executor: *d"]
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, "---\n" + "\n".join(lines) + "\n---\n")


def test_merge_cycle_is_copied(tmp_path):
    text = "---\ndefaults: &d {<<: *d}\n<<: *d\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_merge_dag_with_no_change_stays_identical(tmp_path, monkeypatch):
    from markdown_vault_mcp import okf_export_paths

    original = okf_export_paths._mapping_values
    calls = [0]

    def count(*args):
        calls[0] += 1
        assert calls[0] < 1000
        return original(*args)

    monkeypatch.setattr(okf_export_paths, "_mapping_values", count)
    lines = ["v0: &v0 {other: value}"]
    lines.extend(f"v{i}: &v{i} {{<<: [*v{i - 1}, *v{i - 1}]}}" for i in range(1, 16))
    lines.append("<<: *v15")
    text = "---\n" + "\n".join(lines) + "\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text
    assert calls[0] < 1000


def test_malformed_yaml_is_copied(tmp_path):
    text = "---\nresource: /guides/b.md\nbad: [\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_unencodable_field_is_reported(tmp_path):
    text = '---\nresource: "/outside/\\ud800.md"\ncomputation: /guides/b.md\n---\n'
    output, report = export(tmp_path, text, external_base="https://example.org/v")
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_encoding"


def test_scalar_token_boundaries_are_indexed_linearly(monkeypatch):
    import yaml

    from markdown_vault_mcp.okf_export_paths import _splice_fields

    text = "\n".join(f"k{i}: /guides/b.md" for i in range(1000))
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    assert isinstance(root, yaml.MappingNode)
    changes = [(value, "/b.md") for _, value in root.value]
    original_scan = yaml.scan
    count = [0]

    class CountMark:
        def __init__(self, mark):
            self.mark = mark

        @property
        def index(self):
            count[0] += 1
            return self.mark.index

    def scan(source):
        for token in original_scan(source):
            if isinstance(token, yaml.ScalarToken):
                token.end_mark = CountMark(token.end_mark)
            yield token

    monkeypatch.setattr(yaml, "scan", scan)
    output = _splice_fields(text, changes)
    assert output.count("/b.md") == 1000
    assert count[0] <= 2000


@pytest.mark.parametrize(
    "field",
    [
        "resource: {}",
        "computation: {}",
        "executor: {{resource: {}}}",
        "attester: {{resource: {}}}",
        "sources: [{{resource: {}}}]",
    ],
)
@pytest.mark.parametrize(
    "source,written,expected",
    [
        ("a.md", "/guides/", "/"),
        ("a.md", "/guides", "/"),
        ("a.md", "/guides/sub/", "/sub/"),
        ("a.md", "/guides/sub", "/sub"),
        ("a.md", "../guides/sub/?x#f", "./sub/?x#f"),
        ("sub/c.md", ".", "."),
        ("sub/c.md", "./", "./"),
        ("sub/c.md", "..", ".."),
    ],
)
def test_directory_fields(tmp_path, field, source, written, expected):
    text = "---\n" + field.format(f'"{written}"') + "\n---\n[x](/guides/)\n"
    output, report = export(tmp_path, text, source=source)
    assert output == text.replace(
        field.format(f'"{written}"'), field.format(f'"{expected}"')
    )
    assert not report.ambiguous_path
    assert [(x.path, x.target) for x in report.dependencies] == [(source, "/guides/")]
    assert (source in report.transformed) == (written != expected)


@pytest.mark.parametrize("written", ["/guides/empty/", "/guides/hidden/"])
def test_unselected_directories_are_dependencies(tmp_path, written):
    text = f"---\nresource: {written}\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.dependencies[0].target == written


@pytest.mark.parametrize(
    "written,expected",
    [
        ("..", ""),
        ("../..", ""),
        ("/", ""),
        ("/other/", "other/"),
        ("/sub/", "sub/"),
        ("..#f", "#f"),
        (".?q", "guides/?q"),
    ],
)
def test_outside_directories_external(tmp_path, written, expected):
    text = f"---\nresource: {written}\n---\n"
    output, report = export(tmp_path, text, external_base="https://example.org/v/")
    if written == ".?q":
        assert output == text
        assert not report.dependencies
    else:
        assert "https://example.org/v/" + expected in output
        assert report.dependencies[0].target == written


@pytest.mark.parametrize("written", ["..", "../..", "/", "/sub/", "..#f"])
def test_outside_directory_collisions(tmp_path, written):
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, f"---\nresource: &p {written}\nunknown: *p\n---\n")


@pytest.mark.parametrize("written", ["...", "..a", ".hidden/x", "a/b"])
def test_unresolved_bare_field_is_a_dependency(tmp_path, written):
    text = f'---\nresource: "{written}"\n---\n'
    output, report = export(tmp_path, text)
    assert output == text
    assert report.dependencies[0].target == written


def test_directory_form_does_not_match_file(tmp_path):
    output, report = export(tmp_path, "---\nresource: /guides/b.md/\n---\n")
    assert output == "---\nresource: /guides/b.md/\n---\n"
    assert report.dependencies[0].target == "/guides/b.md/"
    output, report = export(tmp_path, "---\nresource: /b.md/\n---\n")
    assert output == "---\nresource: /b.md/\n---\n"
    assert report.dependencies[0].target == "/b.md/"


def test_parser_skip_reports_actual_header(tmp_path):
    for header in (
        b"resource: /guides/b.md\nbad: [\n",
        b"resource: /guides/b.md\nbad: \xff\n",
    ):
        text = b"---\n" + header + b"---\n[x](/guides/b.md)\n"
        output, report = export(tmp_path, text.decode(errors="surrogateescape"))
        assert output.encode(errors="surrogateescape") == text.replace(
            b"[x](/guides/b.md)", b"[x](/b.md)"
        )
        assert report.uninspected["guides/a.md"] == "metadata_encoding"


def test_invalid_body_does_not_report_header_skip(tmp_path):
    text = b"---\nresource: /guides/b.md\n---\nbody \xff\n".decode(
        errors="surrogateescape"
    )
    output, report = export(tmp_path, text)
    assert output == text.replace("/guides/b.md", "/b.md")
    assert report.uninspected.get("guides/a.md") != "metadata_encoding"


def test_dotfile_reentry_uses_explicit_relative_spelling():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths("guides", {"guides/a.md", "guides/.hidden/n.md"}, None)
    report = OkfExportReport()
    text = "---\nresource: ../guides/.hidden/n.md\n---\n"
    assert paths.relocate_frontmatter(text, "guides/a.md", report) == text.replace(
        "../guides/.hidden/n.md", "./.hidden/n.md"
    )


def test_whole_vault_invalid_header_has_no_local_skip(tmp_path):
    (tmp_path / "a.md").write_bytes(b"---\nresource: /x\nbad: \xff\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
        assert result.report.uninspected.get("a.md") != "metadata_encoding"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "written,expected,collision",
    [
        ("/b.md", "/b.md", True),
        ("/b%2Emd?x=1#s", "/b.md?x=1#s", True),
        ("/outside%20file.md", "/outside%20file.md", False),
        ("/outside file.md", "/outside%20file.md", False),
        ("/outside%2Ffile.md", "/outside%2Ffile.md", False),
        ("/outside%FF.md", "/outside%FF.md", False),
        ("/outside%FF file%20name%.md", "/outside%FF%20file%20name%25.md", False),
        ("/guides/a%2Fb.md", "/guides/a%2Fb.md", False),
        ("/b.md#section", "/b.md#section", True),
    ],
)
def test_external_uri_and_collision(tmp_path, written, expected, collision):
    import yaml

    alias = written == "/b.md#section"
    text = (
        f'---\nresource: {"&p " if alias else ""}"{written}"\n'
        + ("unknown: *p\n" if alias else "")
        + "---\n"
    )
    if collision:
        with pytest.raises(ValueError, match="collision"):
            export(tmp_path, text)
    else:
        output, report = export(tmp_path, text)
        assert output == text
        assert report.dependencies[0].target == written
    output, report = export(tmp_path, text, external_base="https://example.org/v")
    data = yaml.safe_load(output.split("---")[1])
    assert data["resource"] == "https://example.org/v" + expected
    if alias:
        assert data["unknown"] == written
    assert report.dependencies[0].target == written


@pytest.mark.parametrize(
    "style,blank,expected",
    [
        ("|-", "", '"/b.md"'),
        (">-", "", '"/b.md"'),
        ("|", "", '"/b.md\\n"'),
        ("|", "\n\n", '"/b.md\\n"'),
    ],
)
def test_block_scalar_and_following_key(tmp_path, style, blank, expected):
    text = f"---\nresource: {style}\n  /guides/b.md\n{blank}title: T\n---\nbody\n"
    output, _ = export(tmp_path, text)
    assert output == f"---\nresource: {expected}\n{blank}title: T\n---\nbody\n"


@pytest.mark.parametrize(
    "source,text,expected",
    [
        (
            "a.md",
            "---\nresource: ./x/../b.md\n---\n",
            "---\nresource: ./x/../b.md\n---\n",
        ),
        (
            "sub/c.md",
            "---\nresource: ../b.md?view=1#part\n---\n",
            "---\nresource: ../b.md?view=1#part\n---\n",
        ),
        (
            "a.md",
            '{\n"resource": "../guides/b.md",\n"computation": "/guides/b.md#s"\n}\nbody\n',
            '{\n"resource": "./b.md",\n"computation": "/b.md#s"\n}\nbody\n',
        ),
    ],
)
def test_relative_paths_and_json(tmp_path, source, text, expected):
    output, report = export(tmp_path, text, source=source)
    assert output == expected
    assert bool(report.transformed) == (text != expected)


@pytest.mark.parametrize(
    "text",
    [
        "----\nresource: /guides/b.md\n----\nbody\n",
        "\ufeff---\r\nresource: /guides/b.md\r\n---\r\nbody\r\n",
    ],
)
def test_frontmatter_envelope_offsets(tmp_path, text):
    output, _ = export(tmp_path, text)
    assert output.encode() == text.encode().replace(b"/guides/b.md", b"/b.md")
    assert (tmp_path / "guides/a.md").read_bytes() == text.encode()


@pytest.mark.parametrize("long_path", [True, False])
def test_unicode_and_long_paths(tmp_path, long_path):
    import yaml

    name = "😀 " + "word " * 30 + "b.md" if long_path else "b\u2028😀.md"
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / name).write_text("B")
    text = (
        f"---\nresource: /guides/{name}\n---\n"
        if long_path
        else '---\nresource: "/guides/b\\u2028😀.md"\n---\n'
    )
    output, _ = export(tmp_path, text)
    assert yaml.safe_load(output.split("---")[1])["resource"] == "/" + name
    assert f"resource: /{name}\n" in output if long_path else "\\u2028" in output


@pytest.mark.parametrize("kind", ["merge", "executor", "sources"])
def test_alias_copy_preserves_values(tmp_path, kind):
    import yaml

    if kind == "sources":
        text = "---\nsources:\n  - &s {id: 1, retrieved: 2026-11-17T10:00:00Z, resource: /guides/b.md}\n  - *s\n---\n"
        output, _ = export(tmp_path, text)
        data = yaml.safe_load(output.split("---")[1])
        assert output.count("2026-11-17T10:00:00Z") == 2
        assert all(
            list(source) == ["id", "retrieved", "resource"]
            for source in data["sources"]
        )
        assert [source["resource"] for source in data["sources"]] == ["/b.md", "/b.md"]
        from datetime import datetime

        assert all(
            isinstance(source["retrieved"], datetime) for source in data["sources"]
        )
        return
    if kind == "merge":
        text = "---\ndefaults: &d {resource: /guides/b.md, other: yes}\n<<: *d\nexecutor: *d\n---\n"
        output, _ = export(tmp_path, text)
        data = yaml.safe_load(output.split("---")[1])
        assert data["resource"] == data["executor"]["resource"] == "/b.md"
        assert data["defaults"]["resource"] == "/guides/b.md"
        assert data["executor"]["other"] is True
        return
    text = "---\ndefaults: &d {resource: /guides/b.md, retrieved: 2026-11-17T10:00:00Z, custom: !foo thing, zzz: 1, aaa: 2}\nexecutor: *d\n---\n"
    output, _ = export(tmp_path, text)
    assert "2026-11-17T10:00:00Z" in output and "!foo" in output
    root = yaml.compose(output.split("---")[1])
    assert isinstance(root, yaml.MappingNode)
    executor = next(v for k, v in root.value if k.value == "executor")
    assert isinstance(executor, yaml.MappingNode)
    assert [k.value for k, _ in executor.value] == [
        "resource",
        "retrieved",
        "custom",
        "zzz",
        "aaa",
    ]
    assert executor.value[1][1].value == "2026-11-17T10:00:00Z"
    assert executor.value[1][1].tag == "tag:yaml.org,2002:timestamp"
    assert executor.value[2][1].tag == "!foo"
    assert "defaults: &d {resource: /guides/b.md" in output


@pytest.mark.parametrize("escape", ["%2F", "%00", "%FF"])
def test_refused_uri_escape_never_matches_literal_member(tmp_path, escape):
    import yaml

    name = f"a{escape}b.md"
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / name).write_text("target")
    text = f"---\nbefore: first\nresource: /guides/{name}\nafter: last\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert [(item.path, item.target) for item in report.dependencies] == [
        ("a.md", f"/guides/{name}")
    ]
    output, report = export(tmp_path, text, external_base="https://example.org/v")
    data = yaml.safe_load(output.split("---")[1])
    assert list(data) == ["before", "resource", "after"]
    assert data == {
        "before": "first",
        "resource": f"https://example.org/v/guides/{name}",
        "after": "last",
    }
    assert [(item.path, item.target) for item in report.dependencies] == [
        ("a.md", f"/guides/{name}")
    ]
    assert (tmp_path / "guides/a.md").read_text() == text


@pytest.mark.parametrize("escape", ["%2F", "%00", "%FF"])
def test_refused_uri_escape_collision_and_literal_percent_filename(tmp_path, escape):
    name = f"a{escape}b.md"
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / name).write_text("target")
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, f"---\nresource: /{name}\n---\n")
    encoded = name.replace("%", "%25")
    text = f"---\nresource: /guides/{encoded}\n---\n"
    output, report = export(tmp_path, text)
    assert output == text.replace("/guides/", "/")
    assert not report.dependencies


@pytest.mark.parametrize("escape", ["%2F", "%00", "%FF"])
def test_refused_uri_escape_never_matches_literal_directory(tmp_path, escape):
    directory = f"dir{escape}name"
    (tmp_path / "guides" / directory).mkdir(parents=True)
    (tmp_path / "guides" / directory / "n.md").write_text("target")
    text = f"---\nresource: /guides/{directory}/\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.dependencies[0].target == f"/guides/{directory}/"


@pytest.mark.parametrize(
    "base",
    ["https://h.example/a,b/", "https://h.example/[x]/", "https://h.example/{x}/"],
)
@pytest.mark.parametrize("style", ["{}", "'{}'", '"{}"'])
@pytest.mark.parametrize(
    "field", ["resource", "computation", "executor", "attester", "sources"]
)
def test_external_base_flow_scalars_preserve_yaml_values(tmp_path, base, style, field):
    import yaml

    scalar = style.format("../outside.md")
    if field in {"resource", "computation"}:
        header = f"{{{field}: {scalar}, other: preserved}}"
        expected = {field: base + "outside.md", "other": "preserved"}
    elif field == "sources":
        header = f"sources: [{{resource: {scalar}, other: preserved}}]"
        expected = {
            "sources": [{"resource": base + "outside.md", "other": "preserved"}]
        }
    else:
        header = f"{field}: {{resource: {scalar}, other: preserved}}"
        expected = {field: {"resource": base + "outside.md", "other": "preserved"}}
    text = f"---\n{header}\n---\n"
    output, report = export(tmp_path, text, external_base=base)
    assert yaml.safe_load(output.split("---")[1]) == expected
    assert [(item.path, item.target) for item in report.dependencies] == [
        ("a.md", "../outside.md")
    ]


@pytest.mark.parametrize(
    "field", ["resource", "computation", "executor", "attester", "sources"]
)
@pytest.mark.parametrize(
    "value,is_ambiguous",
    [
        ("x://example.org/a", False),
        ("X://example.org/a?x=1#f", False),
        ("C:/notes/topic.md", False),
        (r"C:\notes\topic.md", False),
    ],
)
def test_one_letter_urls_and_drive_paths(tmp_path, field, value, is_ambiguous):
    scalar = f"'{value}'"
    if field in {"resource", "computation"}:
        header = f"{field}: {scalar}"
    elif field == "sources":
        header = f"sources: [{{resource: {scalar}}}]"
    else:
        header = f"{field}: {{resource: {scalar}}}"
    text = f"---\n{header}\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert [(item.path, item.target) for item in report.ambiguous_path] == (
        [("a.md", value)] if is_ambiguous else []
    )
    assert not report.dependencies
    assert not report.transformed


@pytest.mark.parametrize("version", [None, "0.1", "0.2"])
def test_legacy_root_fallback_is_relocated_and_reported(tmp_path, version):
    if version:
        (tmp_path / "index.md").write_text(f'---\nokf_version: "{version}"\n---\n')
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "target.md").write_text("target")
    text = "---\nresource: guides/target.md\n---\n"
    output, report = export(
        tmp_path, text, source="sub/c.md", include_index_excluded=True
    )
    assert "resource: ../target.md" in output
    assert [(x.path, x.target) for x in report.v0_2_root_fallback] == [
        ("sub/c.md", "guides/target.md")
    ]
    assert not report.dependencies
    assert not report.ambiguous_path


def test_v03_bare_fields_and_unresolved(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.3"\n---\n')
    text = "---\nresource: b.md\nsuperseded_by: b.md\ncomputation: missing.md\nsources: [{resource: {scope: all}}, {resource: https://example.org/x}]\n---\n"
    output, report = export(tmp_path, text, include_index_excluded=True)
    assert output == text
    assert [(x.path, x.target) for x in report.dependencies] == [("a.md", "missing.md")]
    assert not report.ambiguous_path


def test_v03_does_not_use_root_fallback(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.3"\n---\n')
    text = "---\nresource: guides/b.md\n---\n"
    output, report = export(
        tmp_path, text, source="sub/c.md", include_index_excluded=True
    )
    assert output == text
    assert [(x.path, x.target) for x in report.dependencies] == [
        ("sub/c.md", "guides/b.md")
    ]


def test_explicit_relative_legacy_fallback(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    text = "---\nsuperseded_by: ./guides/b.md\n---\n"
    output, report = export(
        tmp_path, text, source="sub/c.md", include_index_excluded=True
    )
    assert "superseded_by: ../b.md" in output
    assert [(x.path, x.target) for x in report.v0_2_root_fallback] == [
        ("sub/c.md", "./guides/b.md")
    ]


def test_unresolved_bare_field_refuses_strict_export(tmp_path):
    from markdown_vault_mcp.okf_export_report import PublicationRefused

    with pytest.raises(PublicationRefused) as exc:
        export(tmp_path, "---\nresource: missing.md\n---\n", strict=True)
    assert [(x.path, x.target) for x in exc.value.report.dependencies] == [
        ("a.md", "missing.md")
    ]


def test_root_license_path_uses_same_resolution():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths("", {"index.md", "LICENSE"}, None, version="0.3")
    report = OkfExportReport()
    text = "---\nlicense: LICENSE\n---\n"
    assert paths.relocate_frontmatter(text, "index.md", report) == text
    assert not report.dependencies
