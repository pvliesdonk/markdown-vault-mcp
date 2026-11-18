"""Path-valued frontmatter relocates without reserializing the document."""

import io
import zipfile

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings


def export(tmp_path, text, **kwargs):
    (tmp_path / "guides").mkdir(exist_ok=True)
    (tmp_path / "guides/a.md").write_text(text)
    (tmp_path / "guides/b.md").write_text("# B\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides", **kwargs
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            return archive.read("a.md").decode(), result.report
    finally:
        vault.close()


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
    assert [(x.path, x.target) for x in report.ambiguous_path] == [
        ("a.md", "references/b.md")
    ]
    assert [(x.path, x.target) for x in report.dependencies] == [
        ("a.md", "/outside.md")
    ]


def test_collision_or_external_base(tmp_path):
    text = "---\nresource: /b.md\n---\n"
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, text)
    output, report = export(tmp_path, text, external_base="https://example.org/vault")
    assert "resource: https://example.org/vault/b.md" in output
    assert report.dependencies[0].target == "/b.md"


def test_unrelated_alias_does_not_block_relocation(tmp_path):
    text = "---\nunknown: &keep value\ncopy: *keep\nresource: /guides/b.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text.replace("resource: /guides/b.md", "resource: /b.md")
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_shared_path_alias_preserves_unknown_value(tmp_path):
    text = "---\nresource: &path /guides/b.md\nunknown: *path\n---\n"
    output, report = export(tmp_path, text)
    import yaml

    metadata = yaml.safe_load(output.split("---")[1])
    assert metadata["resource"] == "/b.md"
    assert metadata["unknown"] == "/guides/b.md"
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


@pytest.mark.parametrize("style", ["|-", ">-", "|"])
def test_block_scalar_and_following_key(tmp_path, style):
    import yaml

    text = f"---\nresource: {style}\n  /guides/b.md\ntitle: T\n---\nbody\n"
    output, _ = export(tmp_path, text)
    data = yaml.safe_load(output.split("---")[1])
    assert data["resource"].rstrip("\n") == "/b.md"
    assert data["title"] == "T"


def test_json_and_relative_reentry(tmp_path):
    text = (
        '{\n"resource": "../guides/b.md",\n"computation": "/guides/b.md#s"\n}\nbody\n'
    )
    output, _ = export(tmp_path, text)
    assert '"resource": "./b.md"' in output
    assert '"computation": "/b.md#s"' in output


def test_merge_and_alias_preserve_defaults(tmp_path):
    import yaml

    text = "---\ndefaults: &d {resource: /guides/b.md, other: yes}\n<<: *d\nexecutor: *d\n---\n"
    output, _ = export(tmp_path, text)
    data = yaml.safe_load(output.split("---")[1])
    assert data["resource"] == "/b.md"
    assert data["executor"]["resource"] == "/b.md"
    assert data["defaults"]["resource"] == "/guides/b.md"
    assert data["executor"]["other"] is True


def test_alias_collision_and_external_suffix(tmp_path):
    import yaml

    text = "---\nresource: &p /b.md#section\nunknown: *p\n---\n"
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, text)
    output, report = export(tmp_path, text, external_base="https://example.org/v")
    data = yaml.safe_load(output.split("---")[1])
    assert data["resource"] == "https://example.org/v/b.md#section"
    assert data["unknown"] == "/b.md#section"
    assert report.dependencies[0].target == "/b.md#section"


def test_emoji_and_long_path(tmp_path):
    import yaml

    name = "😀 " + "word " * 30 + "b.md"
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / name).write_text("B")
    text = f'---\nresource: "/guides/{name}"\n---\n'
    output, _ = export(tmp_path, text)
    assert yaml.safe_load(output.split("---")[1])["resource"] == "/" + name


def test_deep_metadata_copied_and_reported(tmp_path):
    text = "---\nunknown: " + "[" * 1100 + "x" + "]" * 1100 + "\nresource: /b.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_alias_graph_expansion_is_bounded(tmp_path):
    lines = ["v0: &v0 {resource: /guides/b.md}"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 15))
    lines.append("sources: *v14")
    text = "---\n" + "\n".join(lines) + "\n---\n"
    # The graph is traversed once per distinct node, even when a path is nested.
    output, _ = export(tmp_path, text)
    assert output == text


def test_alias_expansion_over_budget_is_copied(tmp_path):
    lines = ["v0: &v0 [x]"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 13))
    lines += ["defaults: &d {resource: /guides/b.md, extra: *v12}", "executor: *d"]
    text = "---\n" + "\n".join(lines) + "\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_relative_path_with_suffix_and_subdirectory(tmp_path):
    import yaml

    (tmp_path / "guides/sub").mkdir(parents=True)
    (tmp_path / "guides/sub/a.md").write_text(
        "---\nresource: ../b.md?view=1#part\n---\n"
    )
    (tmp_path / "guides/b.md").write_text("B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            data = yaml.safe_load(archive.read("sub/a.md").decode().split("---")[1])
        assert data["resource"] == "../b.md?view=1#part"
    finally:
        vault.close()


def test_large_pathless_metadata_exports(tmp_path):
    text = "---\ntags:\n" + "".join(f"  - tag{i}\n" for i in range(4100)) + "---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_recursive_alias_copied_and_reported(tmp_path):
    text = "---\ndefaults: &d {resource: /guides/b.md, extra: *d}\nexecutor: *d\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_recursion_error_copied_and_reported(tmp_path, monkeypatch):
    from markdown_vault_mcp import okf_export_paths

    def fail(*_args):
        raise RecursionError

    monkeypatch.setattr(okf_export_paths, "_isolate", fail)
    text = "---\nresource: &p /guides/b.md\nunknown: *p\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_alias_graph_traversal_visits_nodes_linearly():
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


def test_extended_yaml_delimiters_relocate(tmp_path):
    text = "----\nresource: /guides/b.md\n----\nbody\n"
    output, _ = export(tmp_path, text)
    assert output == text.replace("/guides/b.md", "/b.md")


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


def test_uri_external_and_collision(tmp_path):
    text = "---\nresource: /b%2Emd?x=1#s\n---\n"
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, text)
    output, report = export(tmp_path, text, external_base="https://example.org/v")
    assert "https://example.org/v/b.md?x=1#s" in output
    assert report.dependencies[0].target == "/b%2Emd?x=1#s"


def test_encoded_separator_is_not_decoded(tmp_path):
    text = "---\nresource: /guides/a%2Fb.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.dependencies[0].target == "/guides/a%2Fb.md"


def test_relative_equivalent_spelling_is_unchanged(tmp_path):
    text = "---\nresource: ./x/../b.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert not report.transformed


def test_alias_copy_retains_tags_timestamps_and_order(tmp_path):
    import yaml

    text = "---\ndefaults: &d {resource: /guides/b.md, retrieved: 2026-11-17T10:00:00Z, custom: !foo thing, zzz: 1, aaa: 2}\nexecutor: *d\n---\n"
    output, _ = export(tmp_path, text)
    assert "2026-11-17T10:00:00Z" in output
    assert "!foo" in output
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
    assert executor.value[2][1].tag == "!foo"
    assert "defaults: &d {resource: /guides/b.md" in output


def test_collision_is_checked_before_over_budget_copy(tmp_path):
    lines = ["v0: &v0 [x]"]
    lines.extend(f"v{i}: &v{i} [*v{i - 1}, *v{i - 1}]" for i in range(1, 13))
    lines += ["defaults: &d {resource: /b.md, extra: *v12}", "executor: *d"]
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, "---\n" + "\n".join(lines) + "\n---\n")


def test_merge_cycle_is_copied(tmp_path):
    text = "---\ndefaults: &d {<<: *d}\n<<: *d\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert report.uninspected["guides/a.md"] == "metadata_complexity"


def test_merge_dag_with_no_change_stays_identical(tmp_path):
    lines = ["v0: &v0 {other: value}"]
    lines.extend(f"v{i}: &v{i} {{<<: [*v{i - 1}, *v{i - 1}]}}" for i in range(1, 16))
    lines.append("<<: *v15")
    text = "---\n" + "\n".join(lines) + "\n---\n"
    output, _ = export(tmp_path, text)
    assert output == text


def test_double_quote_escapes_unicode_separators(tmp_path):
    import yaml

    name = "b\u2028😀.md"
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / name).write_text("B")
    text = '---\nresource: "/guides/b\\u2028😀.md"\n---\n'
    output, _ = export(tmp_path, text)
    assert "\\u2028" in output
    assert yaml.safe_load(output.split("---")[1])["resource"] == "/" + name


def test_aliased_source_retains_timestamp_and_order(tmp_path):
    import yaml

    text = "---\nsources:\n  - &s {id: 1, retrieved: 2026-11-17T10:00:00Z, resource: /guides/b.md}\n  - *s\n---\n"
    output, _ = export(tmp_path, text)
    data = yaml.safe_load(output.split("---")[1])
    assert output.count("2026-11-17T10:00:00Z") == 2
    assert all(
        list(source) == ["id", "retrieved", "resource"] for source in data["sources"]
    )
    assert [source["resource"] for source in data["sources"]] == ["/b.md", "/b.md"]


@pytest.mark.parametrize("outside", ["/outside%20file.md", "/outside file.md"])
def test_external_uri_escapes_once(tmp_path, outside):
    output, report = export(
        tmp_path,
        f'---\nresource: "{outside}?q=1#h"\n---\n',
        external_base="https://example.org/v",
    )
    assert "https://example.org/v/outside%20file.md?q=1#h" in output
    assert report.dependencies[0].target == outside + "?q=1#h"


def test_crlf_and_bom_frontmatter(tmp_path):
    text = "\ufeff---\r\nresource: /guides/b.md\r\n---\r\nbody\r\n"
    (tmp_path / "guides").mkdir()
    original = text.encode()
    (tmp_path / "guides/a.md").write_bytes(original)
    (tmp_path / "guides/b.md").write_text("B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == original.replace(b"/guides/b.md", b"/b.md")
        assert (tmp_path / "guides/a.md").read_bytes() == original
    finally:
        vault.close()


def test_malformed_yaml_is_copied(tmp_path):
    text = "---\nresource: /guides/b.md\nbad: [\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert any(finding.path == "guides/a.md" for finding in report.format_findings)


def test_unknown_representation_node_is_bounded():
    from yaml.nodes import Node

    from markdown_vault_mcp.okf_export_paths import _copy_node, _MetadataComplexity

    with pytest.raises(_MetadataComplexity):
        _copy_node(Node("!unknown", "value", None, None), [1], set())
