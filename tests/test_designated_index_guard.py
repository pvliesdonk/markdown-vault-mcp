"""Designated generated indexes reject ordinary writes."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.designated_index import designated_vault


@pytest.mark.parametrize(
    "operation",
    [
        "write",
        "edit",
        "append",
        "delete",
        "rename_from",
        "rename_to",
        "move_folder_from",
        "attachment",
    ],
)
def test_mutation_routes_refuse_designation(tmp_path: Path, operation: str) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "other.md").write_text("other")
    vault = designated_vault(tmp_path)
    try:
        actions = {
            "write": lambda: vault.writer.write("guides/index.md", "changed"),
            "edit": lambda: vault.writer.edit("guides/index.md", "original", "changed"),
            "append": lambda: vault.writer.append("guides/index.md", "changed"),
            "delete": lambda: vault.writer.delete("guides/index.md"),
            "rename_from": lambda: vault.writer.rename("guides/index.md", "new.md"),
            "rename_to": lambda: vault.writer.rename("other.md", "guides/index.md"),
            "move_folder_from": lambda: vault.writer.move_folder("guides", "new"),
            "attachment": lambda: vault.writer.write_attachment(
                "guides/index.md", b"changed"
            ),
        }
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            actions[operation]()
        assert (tmp_path / "guides" / "index.md").read_text() == "original"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "enabled,mode,folder",
    [(False, "on", "guides"), (True, "auto", "guides"), (True, "on", "other")],
)
def test_inactive_or_undesignated_index_is_editable(
    tmp_path: Path, enabled: bool, mode: str, folder: str
) -> None:
    (tmp_path / "guides").mkdir()
    caps = ContentConfig(
        okf_mode=mode, okf_index=enabled, okf_folders=(folder,)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode=mode, okf_capabilities=caps),
    )
    try:
        vault.writer.write("guides/index.md", "authored")
        assert (tmp_path / "guides" / "index.md").read_text().endswith("authored")
    finally:
        vault.close()


@pytest.mark.parametrize("protected", [False, True])
def test_generator_obeys_operator_protection(tmp_path: Path, protected: bool) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("# Before\n")
    (tmp_path / "guides" / "note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["guides/index.md"] if protected else [],
        ),
    )
    try:
        vault.index.build_index()
        if protected:
            with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
                vault.writer.okf_generate_index(folder="guides")
            assert (tmp_path / "guides" / "index.md").read_text() == "# Before\n"
        else:
            vault.writer.okf_generate_index(folder="guides")
            assert "/guides/note.md" in (tmp_path / "guides" / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize("folder", ["A", "a"])
def test_negated_glob_protects_generated_index(tmp_path: Path, folder: str) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "A").mkdir()
    if folder == "a" and not (tmp_path / folder).exists():
        pytest.skip("filesystem distinguishes case variants")
    vault = designated_vault(tmp_path, ("[!a]*",))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="A")
        index = tmp_path / "A/index.md"
        before = index.read_text()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write(f"{folder}/index.md", "CLIENT CONTENT")
        vault.writer.write("A/note.md", "# Note")
        wait_for_writer_drain(vault)
        assert "CLIENT CONTENT" not in index.read_text()
        assert before != index.read_text()
    finally:
        vault.close()


def test_negated_glob_protects_missing_case_alias(tmp_path: Path) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "probe").touch()
    if not (tmp_path / "PROBE").exists():
        pytest.skip("filesystem distinguishes case variants")
    vault = designated_vault(tmp_path, ("[!a]*",))
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("a/index.md", "CLIENT CONTENT")
        assert not (tmp_path / "A/index.md").exists()
        vault.writer.write("A/note.md", "# Note")
        wait_for_writer_drain(vault)
        assert "CLIENT CONTENT" not in (tmp_path / "A/index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize("followup", ["a", "A"])
def test_negated_glob_excluding_case_variants_preserves_authored_index(
    tmp_path: Path, followup: str
) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "probe").touch()
    if not (tmp_path / "PROBE").exists():
        pytest.skip("filesystem distinguishes case variants")
    (tmp_path / "a").mkdir()
    vault = designated_vault(tmp_path, ("[!aA]*",))
    try:
        vault.index.build_index()
        vault.writer.write("a/index.md", "AUTHORED")
        vault.writer.write(f"{followup}/note.md", "# Note")
        wait_for_writer_drain(vault)
        assert (tmp_path / "a/index.md").read_text() == "AUTHORED"
    finally:
        vault.close()


def test_directory_and_leaf_symlink_aliases(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("original")
    (tmp_path / "link").symlink_to(tmp_path / "guides", target_is_directory=True)
    (tmp_path / "alias.md").symlink_to(tmp_path / "guides" / "index.md")
    vault = designated_vault(tmp_path)
    try:
        for path in ("link/index.md", "alias.md"):
            with pytest.raises(InvalidRequestError, match="okf_generate_index"):
                vault.writer.write(path, "changed")
    finally:
        vault.close()


def test_designated_leaf_alias_to_undesignated_index_is_editable(
    tmp_path: Path,
) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "other").mkdir()
    note = tmp_path / "other/index.md"
    note.write_text("authored")
    (tmp_path / "guides/index.md").symlink_to(note)
    vault = designated_vault(tmp_path)
    try:
        vault.writer.write("guides/index.md", "changed")
        assert note.read_text().endswith("changed")
    finally:
        vault.close()


def test_legacy_alias_refuses_designated_index(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(okf_mode="on", okf_write=True).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write("index.md", "changed")
    finally:
        vault.close()


def test_rename_skips_designated_backlink(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "index.md").write_text("[Old](/old.md)\n")
    (tmp_path / "old.md").write_text("# Old\n")
    vault = designated_vault(tmp_path)
    try:
        vault.index.build_index()
        result = vault.writer.rename("old.md", "new.md", update_links=True)
        assert result.hint and "okf_generate_index" in result.hint
        assert (tmp_path / "guides" / "index.md").read_text() == "[Old](/old.md)\n"
    finally:
        vault.close()


def test_explicit_policy_takes_precedence(tmp_path: Path) -> None:
    (tmp_path / "guides").mkdir()
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("guides",)
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["guides/index.md"],
        ),
    )
    try:
        with pytest.raises(InvalidRequestError, match="PROTECTED_PATHS"):
            vault.writer.write("guides/index.md", "changed")
    finally:
        vault.close()


async def test_upload_refuses_designated_index(tmp_path: Path) -> None:
    from fastmcp.exceptions import ToolError

    from markdown_vault_mcp._transfer_sink import VaultTransferSink
    from markdown_vault_mcp.config import ProjectConfig

    (tmp_path / "guides").mkdir()
    vault = designated_vault(tmp_path)
    try:
        sink = VaultTransferSink(
            ProjectConfig(source_dir=tmp_path, read_only=False),
            vault_provider=lambda: vault,
        )
        with pytest.raises(ToolError, match="okf_generate_index"):
            await sink.validate("guides/index.md", "upload")
    finally:
        vault.close()


def test_directory_named_index_is_not_reserved(tmp_path: Path) -> None:
    (tmp_path / "guides" / "index.md").mkdir(parents=True)
    (tmp_path / "guides" / "index.md" / "note.md").write_text("note")
    vault = designated_vault(tmp_path)
    try:
        vault.writer.move_folder("guides", "archive")
        assert (tmp_path / "archive" / "index.md" / "note.md").read_text() == "note"
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("path", "folders", "expected"),
    [
        ("GUIDES/index.md", ("guides",), "case"),
        ("guides/INDEX.md", ("guides",), "case"),
        ("cafe\u0301/index.md", ("café",), "unicode"),
        ("e\u0301/index.md", ("é",), "unicode"),
        ("INDEX.md", (".",), "case"),
        ("123/INDEX.md", ("123",), "case"),
        ("guides/NEW/INDEX.md", ("guides/**",), "case"),
        ("cafe\u0301/NEW/INDEX.md", ("café/**",), "both"),
        ("link/index.md", ("link",), "never"),
    ],
)
def test_designated_index_follows_filesystem_identity(
    tmp_path: Path, path: str, folders: tuple[str, ...], expected: str
) -> None:
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    root = tmp_path / "vault"
    root.mkdir()
    (root / "guides").mkdir()
    (root / "guides/index.md").write_text("original")
    (root / "café").mkdir()
    (root / "café/index.md").write_text("original")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    case_aliases = (root / "GUIDES").exists()
    unicode_aliases = (root / "cafe\u0301").exists()
    want = {
        "case": case_aliases,
        "unicode": unicode_aliases,
        "both": case_aliases and unicode_aliases,
        "never": False,
    }[expected]
    assert designated_index_path(root, path, folders) is want
    assert not list(root.glob(".mvmcp-probe-*"))


def test_filesystem_probe_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.okf_capabilities import _probe

    before = set(tmp_path.iterdir())
    case, unicode = _probe(tmp_path)
    probe_entry = tmp_path / "Aé"
    probe_entry.touch()
    assert case == (tmp_path / "aÉ").exists()
    assert unicode == (tmp_path / "Ae\u0301").exists()
    probe_entry.unlink()
    assert set(tmp_path.iterdir()) == before

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(
        "markdown_vault_mcp.okf_capabilities.tempfile.TemporaryDirectory", denied
    )
    assert _probe(tmp_path) == (True, True)


@pytest.mark.parametrize(
    ("fold_case", "folder", "policy"),
    [
        (True, "a", ("[!a]*", True)),
        (False, "a", ("[!a]*", False)),
        (True, "A", ("[!a]*", True)),
        (False, "A", ("[!a]*", True)),
        (True, "a", ("[!aA]*", False)),
        (False, "a", ("[!aA]*", False)),
    ],
)
def test_negated_glob_policy_on_measured_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fold_case: bool,
    folder: str,
    policy: tuple[str, bool],
) -> None:
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    pattern, expected = policy
    monkeypatch.setattr(
        "markdown_vault_mcp.okf_capabilities._probe", lambda _anchor: (fold_case, False)
    )
    assert designated_index_path(tmp_path, f"{folder}/index.md", (pattern,)) is expected


def test_casefold_expansion_in_positive_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    monkeypatch.setattr(
        "markdown_vault_mcp.okf_capabilities._probe", lambda _anchor: (True, False)
    )
    assert designated_index_path(tmp_path, "ẞ/index.md", ("[ß]",))


@pytest.mark.parametrize(
    ("name", "pattern", "case", "unicode", "want"),
    [
        ("a", "[!a]", True, False, True),
        ("a", "[!aA]", True, False, False),
        ("ẞ", "[ß]", True, False, True),
        ("ẞ", "[À-ÿ]", True, False, True),
        ("é", "??", False, True, True),
        ("é", "[!x]?", False, True, True),
        ("e\u0301", "[é]", False, True, True),
        ("K", "\u212a", False, True, True),
        ("\u212a", "K", False, True, True),
        ("A\u030a", "[Å]", False, True, True),
        ("가", "??", False, True, True),
        ("a\u0323\u0301", "a\u0301\u0323", False, True, True),
        ("a\u0301\u0300", "a\u0300\u0301", False, True, False),
        ("a\u0323\u0301", "a?\u0323", False, True, False),
        ("]", "[]-]", False, False, True),
        ("[", "[", False, False, True),
        ("abc", "a*c", False, False, True),
    ],
)
def test_equivalent_segment_matches_original_glob(
    name: str, pattern: str, case: bool, unicode: bool, want: bool
) -> None:
    from markdown_vault_mcp.okf_capabilities import _equivalent

    assert _equivalent(name, pattern, case, unicode) is want


@pytest.mark.parametrize(
    ("name", "pattern", "axes"),
    [
        ("ẞ", "[ß]", (True, False)),
        ("ß", "[ẞ]", (True, False)),
        ("ẞ", "[À-ÿ]", (True, False)),
        ("é", "??", (False, True)),
        ("é", "[!x]?", (False, True)),
        ("e\u0301", "[é]", (False, True)),
        ("K", "\u212a", (False, True)),
        ("\u212a", "K", (False, True)),
        ("Å", "A\u030a", (False, True)),
        ("Å", "A\u030a", (False, True)),
        ("가", "??", (False, True)),
        ("A", "[a-z]", (True, False)),
    ],
)
def test_policy_with_simulated_folding_axes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    pattern: str,
    axes: tuple[bool, bool],
) -> None:
    from markdown_vault_mcp.okf_capabilities import designated_index_path

    monkeypatch.setattr("markdown_vault_mcp.okf_capabilities._probe", lambda _p: axes)
    assert designated_index_path(tmp_path, f"{name}/index.md", (pattern,))
    monkeypatch.setattr(
        "markdown_vault_mcp.okf_capabilities._probe", lambda _p: (False, False)
    )
    assert not designated_index_path(tmp_path, f"{name}/index.md", (pattern,))


def test_witness_tables_match_each_codepoint() -> None:
    from unicodedata import normalize

    from markdown_vault_mcp.okf_capabilities import _equivalent, _witnesses

    for chars in _witnesses(True).values():
        for char in chars:
            nfd = normalize("NFD", char)
            assert _equivalent(nfd, f"[{char}]", False, True)
            assert _equivalent(char, nfd, False, True)
    for fold, chars in _witnesses(False).items():
        if len(fold) == 1:
            for char in chars:
                assert _equivalent(char, f"[{fold}]", True, False)


@pytest.mark.parametrize(
    ("designated", "alias", "pattern"),
    [
        ("ß", "ẞ", "[ß]"),
        ("ß", "ẞ", "[À-ÿ]"),
        ("e\u0301", "é", "??"),
        ("e\u0301", "é", "[!x]?"),
        ("a\u0301\u0323", "a\u0323\u0301", "a\u0301\u0323"),
    ],
)
def test_native_glob_alias_cannot_lose_content(
    tmp_path: Path, designated: str, alias: str, pattern: str
) -> None:
    (tmp_path / designated).mkdir()
    if not (tmp_path / alias).exists():
        pytest.skip("filesystem distinguishes spellings")
    vault = designated_vault(tmp_path, (pattern,))
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="okf_generate_index"):
            vault.writer.write(f"{alias}/index.md", "CLIENT CONTENT")
    finally:
        vault.close()


def test_literal_combining_marks_order_and_bounded_segment_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itertools import permutations

    import markdown_vault_mcp.okf_capabilities as capabilities

    marks = "\u0315\u0301\u0323"
    for spelling in permutations(marks):
        assert capabilities._equivalent(
            "a" + marks, "a" + "".join(spelling), False, True
        )
    calls = 0
    original = capabilities._equivalent

    def count(name: str, pattern: str, case: bool, unicode: bool) -> bool:
        nonlocal calls
        calls += 1
        return original(name, pattern, case, unicode)

    monkeypatch.setattr(capabilities, "_equivalent", count)
    monkeypatch.setattr(capabilities, "_probe", lambda _p: (True, True))
    name = "é" * 20
    assert capabilities.designated_index_path(tmp_path, f"{name}/INDEX.md", ("*",))
    assert calls <= 3
    calls = 0
    assert capabilities.designated_index_path(
        tmp_path, f"{name}/INDEX.md", ("e\u0301" * 20,)
    )
    assert calls <= 3
