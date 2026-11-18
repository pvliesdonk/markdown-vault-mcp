import pytest

from markdown_vault_mcp._okf_log_merge import merge_log_texts


@pytest.mark.parametrize("heading", ["2026-13-40", "2026-02-30"])
def test_non_calendar_date_refuses_union(heading: str) -> None:
    remote = f"## {heading}\n\n- remote\n"
    local = f"## {heading}\n\n- local\n"
    result = merge_log_texts(remote, local)
    assert result.text is None, result


def test_non_chronological_sections_refuse_union() -> None:
    remote = "## 2026-11-17\n\n- remote\n\n## 2026-11-18\n\n- next\n"
    local = "## 2026-11-17\n\n- local\n\n## 2026-11-18\n\n- next\n"
    result = merge_log_texts(remote, local)
    assert result.text is None, result


def test_union_preserves_list_entry_link_targets() -> None:
    from markdown_it import MarkdownIt

    remote = "## 2026-11-18\n\n- [Remote][ref]\n\n  [ref]: https://remote.example\n"
    local = "## 2026-11-18\n\n- [Local][ref]\n\n  [ref]: https://local.example\n"
    parser = MarkdownIt("commonmark")
    assert 'href="https://local.example"' in parser.render(local)
    result = merge_log_texts(remote, local)
    if result.text is not None:
        assert 'href="https://local.example">Local</a>' in parser.render(result.text), (
            result
        )
