"""Path-valued frontmatter relocates without reserializing the document."""

import sys

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
    # The copied-depth case needs the default limit; in-process mypy raises it.
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1000)
        output, _ = export(tmp_path, text)
        assert output == text
    finally:
        sys.setrecursionlimit(previous_limit)


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
    "value",
    [
        "x://example.org/a",
        "X://example.org/a?x=1#f",
        "C:/notes/topic.md",
        r"C:\notes\topic.md",
    ],
)
def test_one_letter_urls_and_drive_paths(tmp_path, field, value):
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
    assert not report.dependencies
    assert not report.transformed


@pytest.mark.parametrize(
    ("version", "field", "value", "expected"),
    [
        (None, "resource", "guides/target.md", "../target.md"),
        ("0.2", "resource", "guides/target.md", "../target.md"),
        ("0.2", "superseded_by", "./guides/b.md", "../b.md"),
        ("0.3", "resource", "guides/b.md", None),
    ],
)
def test_legacy_root_fallback_is_relocated_and_reported(
    tmp_path, version, field, value, expected
):
    if version:
        (tmp_path / "index.md").write_text(f'---\nokf_version: "{version}"\n---\n')
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "target.md").write_text("target")
    text = f"---\n{field}: {value}\n---\n"
    output, report = export(tmp_path, text, source="sub/c.md")
    if expected is None:
        assert output == text
        assert [(x.path, x.target) for x in report.dependencies] == [
            ("sub/c.md", value)
        ]
        assert not report.v0_2_root_fallback
    else:
        assert f"{field}: {expected}" in output
        assert [(x.path, x.target) for x in report.v0_2_root_fallback] == [
            ("sub/c.md", value)
        ]
        assert not report.dependencies


@pytest.mark.parametrize(
    "license_value", ["CC-BY-4.0", "MIT OR Apache-2.0", "LicenseRef-custom"]
)
def test_root_spdx_license_is_not_a_missing_path(license_value):
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    def unavailable():
        raise AssertionError("SPDX metadata must not scan inventory")

    paths = ExportPaths(
        "", {"index.md"}, None, inventory=unavailable, declared=lambda _: "0.3"
    )
    report = OkfExportReport()
    text = f"---\nlicense: {license_value}\n---\n"
    assert paths.relocate_frontmatter(text, "index.md", report) == text
    assert not report.dependencies


@pytest.mark.parametrize("license_value", ["missing.txt"])
def test_missing_root_license_path_is_reported(license_value):
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths("", {"index.md"}, None, declared=lambda _: "0.3")
    report = OkfExportReport()
    text = f"---\nlicense: {license_value}\n---\n"
    assert paths.relocate_frontmatter(text, "index.md", report) == text
    assert [(x.path, x.target) for x in report.dependencies] == [
        ("index.md", license_value)
    ]


def test_field_inventory_failure_refuses_export():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    def unavailable():
        raise ValueError("inventory unavailable")

    paths = ExportPaths("guides", {"guides/a.md"}, None, inventory=unavailable)
    with pytest.raises(ValueError, match="inventory unavailable"):
        paths.relocate_frontmatter(
            "---\nresource: missing.md\n---\n", "guides/a.md", OkfExportReport()
        )


def test_selected_relative_path_does_not_scan_vault():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    def unavailable():
        raise AssertionError("unneeded inventory")

    paths = ExportPaths(
        "guides", {"guides/a.md", "guides/b.md"}, None, inventory=unavailable
    )
    text = "---\nresource: b.md\n---\n"
    assert paths.relocate_frontmatter(text, "guides/a.md", OkfExportReport()) == text


def test_root_export_does_not_create_false_collision():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths(
        "", {"index.md", "target.md", "a.md"}, None, declared=lambda _: "0.3"
    )
    report = OkfExportReport()
    text = "---\nresource: arget.md\n---\n"
    assert paths.relocate_frontmatter(text, "a.md", report) == text
    assert [(x.path, x.target) for x in report.dependencies] == [("a.md", "arget.md")]


@pytest.mark.parametrize(
    ("value", "expected", "dependency"),
    [
        ("/guides/b.md", "/b.md", False),
        ("/outside.md", "https://example.org/v/outside.md", True),
    ],
)
def test_subtree_root_license_relocates_or_preserves(
    tmp_path, value, expected, dependency
):
    import yaml

    text = f"---\nlicense: {value}\n---\n"
    output, report = export(
        tmp_path, text, source="index.md", external_base="https://example.org/v"
    )
    assert yaml.safe_load(output.split("---")[1])["license"] == expected
    assert [(x.path, x.target) for x in report.dependencies] == (
        [("index.md", value)] if dependency else []
    )


@pytest.mark.parametrize(
    ("version", "artifact_version", "value"),
    [
        (None, None, "shared.md"),
        ("0.2", None, "shared.md"),
        (None, "0.3", "shared.md"),
        ("0.2", "0.3", "shared.md"),
        ("0.3", None, "b.md"),
        ("0.3", None, "./b.md"),
    ],
)
def test_outside_legacy_fallback_never_retargets(
    tmp_path, version, artifact_version, value
):
    if version:
        (tmp_path / "index.md").write_text(f'---\nokf_version: "{version}"\n---\n')
    (tmp_path / "guides").mkdir()
    if version != "0.3":
        (tmp_path / "shared.md").write_text("outside")
        (tmp_path / "guides" / "shared.md").write_text("selected")
    if artifact_version:
        (tmp_path / "guides" / "index.md").write_text(
            f'---\nokf_version: "{artifact_version}"\n---\n'
        )
    text = f"---\nresource: {value}\n---\n"
    if artifact_version == "0.3":
        output, report = export(tmp_path, text, source="sub/c.md")
        assert output == text
        assert report.dependencies[0].target == value
    else:
        with pytest.raises(ValueError) as exc:
            export(tmp_path, text, source="sub/c.md")
        message = str(exc.value)
        assert all(
            part in message
            for part in (
                "collision",
                "guides/sub/c.md",
                value,
                "correct the reference",
                "index.md declaration",
            )
        )
        if version != "0.3":
            output, report = export(
                tmp_path, text, source="sub/c.md", external_base="https://example.org/v"
            )
            assert "https://example.org/v/shared.md" in output
            assert report.dependencies[0].target == value


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize("scenario", ["root", "subtree", "git-prefix", "live-prefix"])
def test_git_export_uses_committed_version_in_on_mode(tmp_path, scenario):
    from tests.fixtures.git import _run_git

    header = b'---\nokf_version: "0.3"\n---\n'
    index = tmp_path / "index.md"
    index.write_bytes(
        header + (b"x" * 70000 + b"\xff" if scenario.endswith("prefix") else b"")
    )
    (tmp_path / "guides/sub").mkdir(parents=True)
    (tmp_path / "guides/b.md").write_text("target")
    selected = tmp_path / "guides/index.md"
    value = "b.md" if scenario == "subtree" else "guides/b.md"
    if scenario == "subtree":
        selected.write_bytes(header)
    (tmp_path / "guides/sub/a.md").write_text(f"---\nresource: {value}\n---\n")
    if scenario != "live-prefix":
        _run_git(tmp_path, "init", "-q")
        _run_git(tmp_path, "config", "user.name", "Fixture")
        _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _run_git(tmp_path, "add", "index.md", "guides")
        _run_git(tmp_path, "commit", "-qm", "fixture")
        if scenario in {"root", "subtree"}:
            (selected if scenario == "subtree" else index).write_text(
                '---\nokf_version: "0.2"\n---\n'
            )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="on"))
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
    finally:
        vault.close()
    assert not result.report.v0_2_root_fallback
    assert [(x.path, x.target) for x in result.report.dependencies] == [
        ("sub/a.md", value)
    ]


def test_root_directory_does_not_create_false_collision():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths("", {"a.md", "guides/b.md"}, None, declared=lambda _: "0.3")
    report = OkfExportReport()
    text = "---\nresource: uides/\n---\n"
    assert paths.relocate_frontmatter(text, "a.md", report) == text
    assert [(item.path, item.target) for item in report.dependencies] == [
        ("a.md", "uides/")
    ]


def test_sources_descriptor_preserves_resolvable_spaces():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths(
        "guides", {"guides/a.md", "guides/real file.md"}, None, declared=lambda _: "0.3"
    )
    report = OkfExportReport()
    text = "---\nresource: unresolved with spaces\nsources:\n  - resource: all queries in BigQuery project X\n  - resource: real file.md\n---\n"
    assert paths.relocate_frontmatter(text, "guides/a.md", report) == text
    assert [(item.path, item.target) for item in report.dependencies] == [
        ("a.md", "unresolved with spaces")
    ]

    def unavailable():
        raise AssertionError("descriptor must not scan the vault")

    isolated = ExportPaths("guides", {"guides/a.md"}, None, inventory=unavailable)
    descriptor = "---\nsources: [{resource: all queries in BigQuery project X}]\n---\n"
    assert (
        isolated.relocate_frontmatter(descriptor, "guides/a.md", OkfExportReport())
        == descriptor
    )
    shared = (
        "---\nresource: &ref unresolved with spaces\nsources: [{resource: *ref}]\n---\n"
    )
    shared_report = OkfExportReport()
    paths.relocate_frontmatter(shared, "guides/a.md", shared_report)
    assert [(item.path, item.target) for item in shared_report.dependencies] == [
        ("a.md", "unresolved with spaces")
    ]


def test_selected_index_version_comes_from_packed_bytes(tmp_path, monkeypatch):
    import io
    import zipfile

    from markdown_vault_mcp import okf_bundle

    (tmp_path / "index.md").write_text('---\nokf_version: "0.3"\n---\n')
    (tmp_path / "guides/sub").mkdir(parents=True)
    selected = tmp_path / "guides/index.md"
    selected.write_text('---\nokf_version: "0.3"\n---\n')
    (tmp_path / "guides/shared.md").write_text("target")
    (tmp_path / "guides/sub/a.md").write_text("---\nresource: shared.md\n---\n")
    original = okf_bundle._read_safe

    def change_after_capture(source, path):
        raw = original(source, path)
        if path == "guides/index.md":
            selected.write_text('---\nokf_version: "0.2"\n---\n')
        return raw

    monkeypatch.setattr(okf_bundle, "_read_safe", change_after_capture)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert b"0.3" in archive.read("index.md")
    assert "guides/index.md" in result.report.snapshot.inconsistent_paths
    assert result.report.dependencies[0].target == "shared.md"


def test_unselected_declaration_read_is_bounded(tmp_path, monkeypatch):
    from markdown_vault_mcp import okf_bundle

    (tmp_path / "index.md").write_bytes(
        b'---\nokf_version: "0.3"\n---\n' + b"x" * 1_000_000
    )
    (tmp_path / "guides").mkdir()
    (tmp_path / "target.md").write_text("target")
    (tmp_path / "guides/a.md").write_text("---\nresource: target.md\n---\n")
    original = okf_bundle.read_regular_nofollow
    limits = []

    def observe(parent, name, limit):
        limits.append(limit)
        return original(parent, name, limit)

    monkeypatch.setattr(okf_bundle, "read_regular_nofollow", observe)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), folder="guides")
    finally:
        vault.close()
    assert limits and max(limits) <= 4 * 65536 + 4


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize("extensions", [None, ("*",)])
@pytest.mark.parametrize("committed", [False, True])
def test_r4_outside_extensionless_license_precedes_spdx(
    tmp_path, extensions, committed
):
    import io
    import zipfile

    import yaml

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (tmp_path / "MIT").write_text("custom license")
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/index.md").write_text("---\nlicense: MIT\n---\n")
    if committed:
        from tests.fixtures.git import _run_git

        _run_git(tmp_path, "init", "-q")
        _run_git(tmp_path, "config", "user.name", "Fixture")
        _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _run_git(tmp_path, "add", "index.md", "MIT", "guides")
        _run_git(tmp_path, "commit", "-qm", "fixture")
    settings = VaultSettings(okf_mode="off", attachment_extensions=extensions)
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path, attachment_extensions=extensions),
            folder="guides",
            external_base="https://example.org/v",
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        header = archive.read("index.md").decode().split("---")[1]
    assert yaml.safe_load(header)["license"] == "https://example.org/v/MIT"
    assert [(item.path, item.target) for item in result.report.dependencies] == [
        ("index.md", "MIT")
    ]
    assert [(item.path, item.target) for item in result.report.v0_2_root_fallback] == [
        ("index.md", "MIT")
    ]


@pytest.mark.parametrize("descriptor", ["production", "BigQuery"])
def test_r4_single_word_sources_descriptor_passes_strict(tmp_path, descriptor):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(
        f"---\ntype: Note\nsources: [{{resource: {descriptor}}}]\n---\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides", strict=True
        )
    finally:
        vault.close()
    assert not result.report.dependencies


def test_r4_malformed_declaration_refuses_version_dependent_fallback(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.3"\nbad: [\n---\n')
    (tmp_path / "guides/sub").mkdir(parents=True)
    (tmp_path / "guides/b.md").write_text("target")
    (tmp_path / "guides/sub/a.md").write_text("---\nresource: guides/b.md\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        with pytest.raises((ValueError, OSError)):
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), folder="guides")
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["invalid_utf8", "malformed_yaml", "unreadable"])
def test_r4_unusable_omitted_root_without_fields_can_export(
    tmp_path, monkeypatch, failure
):
    from markdown_vault_mcp import okf_bundle

    raw = {
        "invalid_utf8": b"\xff\xfe invalid",
        "malformed_yaml": b'---\nokf_version: "0.3"\nbad: [\n---\n',
        "unreadable": b'---\nokf_version: "0.3"\n---\n',
    }[failure]
    (tmp_path / "index.md").write_bytes(raw)
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("---\ntype: Note\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    if failure == "unreadable":

        def deny(*_args):
            raise PermissionError("fixture unreadable root declaration")

        monkeypatch.setattr(okf_bundle, "read_regular_nofollow", deny)
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
    finally:
        vault.close()
    assert result.data.startswith(b"PK")


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize(
    "scenario",
    [
        (committed, mode, selected_root)
        for committed in (False, True)
        for mode in ("off", "auto")
        for selected_root in (False, True)
    ],
)
@pytest.mark.parametrize("kind", ["invalid", "malformed", "overlong"])
@pytest.mark.parametrize("reference", ["none", "selected", "unresolved", "fallback"])
def test_replan2_declaration_is_read_only_for_fallback(
    tmp_path, scenario, kind, reference
):
    committed, mode, selected_root = scenario
    from tests.fixtures.git import _run_git

    raw = {
        "invalid": b"\xff invalid",
        "malformed": b'---\nokf_version: "0.3"\nbad: [\n---\n',
        "overlong": b'---\nokf_version: "0.3"\nbig: ' + b"x" * 70000 + b"\n---\n",
    }[kind]
    (tmp_path / "index.md").write_bytes(raw)
    (tmp_path / "target.md").write_text("target")
    folder = "" if selected_root else "guides"
    directory = tmp_path / ("sub" if selected_root else "guides/sub")
    directory.mkdir(parents=True)
    (directory / "local.md").write_text("target")
    value = {
        "none": "",
        "selected": "local.md",
        "unresolved": "missing.md",
        "fallback": "target.md",
    }[reference]
    resource = f"resource: {value}\n" if value else ""
    (directory / "a.md").write_text(f"---\ntype: Note\n{resource}---\n")
    if committed:
        _run_git(tmp_path, "init", "-q")
        _run_git(tmp_path, "config", "user.name", "Fixture")
        _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _run_git(
            tmp_path,
            "add",
            "index.md",
            "target.md",
            "sub" if selected_root else "guides",
        )
        _run_git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode=mode))
    try:
        if reference == "fallback":
            with pytest.raises((ValueError, OSError), match=r"index\.md"):
                build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder=folder
                )
        else:
            result = build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder=folder
            )
            assert result.data.startswith(b"PK")
    finally:
        vault.close()


@pytest.mark.parametrize("value", ["b.md", "tables/b.md", "my%20note.md", "b.md?x=1"])
def test_replan2_path_shaped_source_is_a_dependency(tmp_path, value):
    from markdown_vault_mcp.okf_export_report import PublicationRefused

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(
        f"---\ntype: Note\nsources: [{{resource: '{value}'}}]\n---\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder="guides", strict=True
            )
        assert [(d.path, d.target) for d in exc.value.report.dependencies] == [
            ("a.md", value)
        ]
    finally:
        vault.close()


@pytest.mark.parametrize("version", [None, "unversioned", "0.2", "0.3", "9.0"])
def test_replan2_absent_unversioned_and_unknown_declaration(tmp_path, version):
    if version is not None:
        header = (
            "title: Index" if version == "unversioned" else f'okf_version: "{version}"'
        )
        (tmp_path / "index.md").write_text(f"---\n{header}\n---\n")
    (tmp_path / "target.md").write_text("target")
    text = "---\nresource: target.md\n---\n"
    output, report = export(tmp_path, text, source="sub/c.md")
    if version in (None, "unversioned", "0.2"):
        assert [(d.path, d.target) for d in report.v0_2_root_fallback] == [
            ("sub/c.md", "target.md")
        ]
        assert report.dependencies[0].target == "target.md"
    else:
        assert output == text
        assert not report.v0_2_root_fallback
        assert report.dependencies[0].target == "target.md"


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("mode", ["off", "auto"])
def test_replan2_selected_bad_index_only_refuses_archive_fallback(
    tmp_path, fallback, committed, mode
):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.3"\n---\n')
    (tmp_path / "guides/sub").mkdir(parents=True)
    (tmp_path / "guides/index.md").write_text("---\nbad: [\n---\n")
    (tmp_path / "guides/target.md").write_text("target")
    field = "resource: target.md\n" if fallback else ""
    (tmp_path / "guides/sub/a.md").write_text(f"---\ntype: Note\n{field}---\n")
    if committed:
        from tests.fixtures.git import _run_git

        _run_git(tmp_path, "init", "-q")
        _run_git(tmp_path, "config", "user.name", "Fixture")
        _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _run_git(tmp_path, "add", "index.md", "guides")
        _run_git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode=mode))
    try:
        if fallback:
            with pytest.raises(ValueError, match=r"guides/index\.md"):
                build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder="guides"
                )
        else:
            assert build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder="guides"
            ).data.startswith(b"PK")
    finally:
        vault.close()


def test_replan2_license_probe_does_not_follow_symlink(tmp_path):
    import io
    import zipfile

    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("custom")
    (root / "MIT").symlink_to(outside)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    (root / "guides").mkdir()
    (root / "guides/index.md").write_text("---\nlicense: MIT\n---\n")
    vault = Vault(source_dir=root, settings=VaultSettings(okf_mode="off"))
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=root),
            folder="guides",
            external_base="https://example.org/v",
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert b"license: MIT" in archive.read("index.md")
    assert not result.report.dependencies


@pytest.mark.parametrize("version", ["0.2", "0.3"])
def test_replan2_whole_vault_same_name_uses_local_target(tmp_path, version):
    import io
    import zipfile

    (tmp_path / "index.md").write_text(f'---\nokf_version: "{version}"\n---\n')
    for folder in ("first", "second"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "b.md").write_text(f"{folder} target")
    original = "---\ntype: Note\nresource: b.md\n---\n"
    (tmp_path / "first/a.md").write_text(original)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode="off"))
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("first/a.md").decode() == original
    assert not result.report.dependencies
    assert not result.report.v0_2_root_fallback


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("mode", ["off", "auto"])
@pytest.mark.parametrize("fallback", [False, True])
def test_replan2_unreadable_omitted_root_only_refuses_fallback(
    tmp_path, monkeypatch, committed, mode, fallback
):
    from markdown_vault_mcp import okf_bundle
    from tests.fixtures.git import _run_git

    root_index = tmp_path / "index.md"
    if committed:
        root_index.symlink_to("unreadable-target")
    else:
        root_index.write_text('---\nokf_version: "0.3"\n---\n')
    (tmp_path / "target.md").write_text("target")
    (tmp_path / "guides/sub").mkdir(parents=True)
    field = "resource: target.md\n" if fallback else ""
    (tmp_path / "guides/sub/a.md").write_text(f"---\ntype: Note\n{field}---\n")
    if committed:
        _run_git(tmp_path, "init", "-q")
        _run_git(tmp_path, "config", "user.name", "Fixture")
        _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _run_git(tmp_path, "add", "index.md", "target.md", "guides")
        _run_git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(okf_mode=mode))
    if not committed:

        def deny(*_args):
            raise PermissionError("fixture inaccessible declaration")

        monkeypatch.setattr(okf_bundle, "read_regular_nofollow", deny)
    try:
        if fallback:
            with pytest.raises(OSError, match=r"index\.md"):
                build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder="guides"
                )
        else:
            assert build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder="guides"
            ).data.startswith(b"PK")
    finally:
        vault.close()


def test_replan2_selected_extensionless_license_is_included(tmp_path):
    import io
    import zipfile

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/index.md").write_text("---\nlicense: MIT\n---\n")
    (tmp_path / "guides/MIT").write_text("custom license")
    settings = VaultSettings(okf_mode="off", attachment_extensions=["*"])
    vault = Vault(source_dir=tmp_path, settings=settings)
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path, attachment_extensions=("*",)),
            folder="guides",
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("MIT") == b"custom license"
        assert b"license: MIT" in archive.read("index.md")
    assert not result.report.dependencies


@pytest.mark.usefixtures("isolated_git")
@pytest.mark.parametrize("mode", ["off", "on", "auto"])
@pytest.mark.parametrize("kind", ["invalid", "symlink", "inspection"])
def test_r5_git_admission_preserves_inherited_policy(tmp_path, monkeypatch, mode, kind):
    from markdown_vault_mcp import okf_bundle
    from tests.fixtures.git import _run_git

    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("---\ntitle: Fixture\n---\n")
    if kind == "symlink":
        (tmp_path / "index.md").symlink_to("missing")
    else:
        (tmp_path / "index.md").write_bytes(
            b"\xff invalid" if kind == "invalid" else b"---\nokf_version: '0.2'\n---\n"
        )
    _run_git(tmp_path, "init", "-q")
    _run_git(tmp_path, "config", "user.name", "Fixture")
    _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-qm", "fixture")
    original = okf_bundle._inspect_page

    def inspect(vault, folder, snapshot, *args, **kwargs):
        if kind == "inspection" and snapshot and set(snapshot.entries) == {"index.md"}:
            raise ValueError("denied root inspection")
        return original(vault, folder, snapshot, *args, **kwargs)

    monkeypatch.setattr(okf_bundle, "_inspect_page", inspect)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode=mode, required_frontmatter=["title"]),
    )
    try:
        if kind == "inspection" and mode == "auto":
            with pytest.raises(ValueError, match="denied root inspection"):
                build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder="guides"
                )
        else:
            assert build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder="guides"
            ).data.startswith(b"PK")
    finally:
        vault.close()


@pytest.mark.parametrize(
    "value", ["../outside.md", "./../outside.md", "a/../../outside.md"]
)
def test_r5_parent_path_does_not_scan_inventory(value):
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    def unavailable():
        raise AssertionError("unneeded inventory")

    paths = ExportPaths("guides", {"guides/a.md"}, None, inventory=unavailable)
    report = OkfExportReport()
    paths.relocate_frontmatter(f"---\nresource: {value}\n---\n", "guides/a.md", report)
    assert report.dependencies


@pytest.mark.parametrize("strict", [False, True])
def test_r5_source_directory_fallback_refuses_retarget(tmp_path, strict):
    from markdown_vault_mcp.okf_export_paths import ExportCollisionError

    (tmp_path / "shared").mkdir()
    (tmp_path / "guides/sub").mkdir(parents=True)
    (tmp_path / "guides/shared").mkdir()
    (tmp_path / "guides/shared/b.md").write_text("---\ntype: Note\n---\n")
    (tmp_path / "guides/sub/a.md").write_text(
        "---\ntype: Note\nsources: [{resource: shared}]\n---\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ExportCollisionError):
            build_okf_bundle(
                vault,
                ProjectConfig(source_dir=tmp_path),
                folder="guides",
                strict=strict,
            )
    finally:
        vault.close()


def test_r5_empty_directory_fallback_externalized(tmp_path):
    (tmp_path / "empty").mkdir()
    output, report = export_note(
        tmp_path,
        b"---\nresource: empty/\n---\n",
        external_base="https://example.com/vault",
    )
    assert b"https://example.com/vault/empty/" in output
    assert report.dependencies and report.v0_2_root_fallback


@pytest.mark.usefixtures("isolated_git")
def test_r5_git_directory_fallback_externalized(tmp_path):
    from tests.fixtures.git import _run_git

    (tmp_path / "empty").mkdir()
    (tmp_path / "empty/b.md").write_text("note")
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("---\nresource: empty/\n---\n")
    _run_git(tmp_path, "init", "-q")
    _run_git(tmp_path, "config", "user.name", "Fixture")
    _run_git(tmp_path, "config", "user.email", "fixture@example.invalid")
    _run_git(tmp_path, "add", "empty/b.md", "guides/a.md")
    _run_git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="guides",
            external_base="https://example.com/vault",
        )
    finally:
        vault.close()
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert b"https://example.com/vault/empty/" in archive.read("a.md")
    assert result.report.dependencies and result.report.v0_2_root_fallback


def test_r5_license_alias_used_as_path_keeps_path_role():
    from markdown_vault_mcp.okf_export_paths import ExportPaths
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    paths = ExportPaths("", {"index.md"}, None)
    report = OkfExportReport()
    paths.relocate_frontmatter(
        "---\nlicense: &value MIT\nresource: *value\n---\n", "index.md", report
    )
    assert report.dependencies
