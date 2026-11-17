"""Exercise convention precedence across folder chains."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import strategies as st

from markdown_vault_mcp.conventions import ConventionsResolver


@given(
    st.lists(
        st.sampled_from(["none", "primary", "legacy", "both"]), min_size=1, max_size=5
    )
)
def test_convention_chain_uses_one_file_per_folder(states: list[str]) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        folder = root
        expected = []
        for depth, state in enumerate(states):
            if depth:
                folder = folder / f"level{depth}"
                folder.mkdir()
            if state in ("primary", "both"):
                (folder / ".conventions.md").write_text("primary", encoding="utf-8")
            if state in ("legacy", "both"):
                (folder / "_conventions.md").write_text("legacy", encoding="utf-8")
            if state != "none":
                name = (
                    ".conventions.md"
                    if state in ("primary", "both")
                    else "_conventions.md"
                )
                expected.append((folder / name).relative_to(root).as_posix())
        assert [
            entry.path
            for entry in ConventionsResolver(root, ".conventions.md").for_path(
                "/".join(folder.relative_to(root).parts)
            )
        ] == expected
