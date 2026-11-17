"""Offline checks for the PostgreSQL image currency job."""

import io
import json
from unittest.mock import patch

import pytest
from scripts.check_postgres_images import hub_tags, lagging

from tests.test_postgres_range_workflow import _images


def test_new_pgvector_patch_names_only_the_lagging_image() -> None:
    assert lagging(_images(), ["0.8.8-pg17"], []) == [
        "pgvector/pgvector:0.8.7-pg17 is behind 0.8.8-pg17"
    ]


def test_new_pgvector_minor_flags_each_tested_major() -> None:
    tags = [f"0.9.0-pg{major}" for major in range(15, 19)]
    assert lagging(_images(), tags, []) == [
        f"pgvector/pgvector:0.8.7-pg{major} is behind 0.9.0-pg{major}"
        for major in range(15, 19)
    ]


def test_floors_equal_tags_and_variants_are_ignored() -> None:
    tags = ["0.7.9-pg15", "0.7.5-pg17", "0.8.7-pg17", "0.8.8-pg17-bookworm"]
    assert (
        lagging(
            _images(),
            tags,
            ["pg17-20260501", "pg17-20260601-arm64", "pg17-v0.4.3", "pg18-20260601"],
        )
        == []
    )


def test_new_major_is_a_support_decision() -> None:
    assert lagging(_images(), ["0.8.7-pg19"], []) == [
        "PostgreSQL 19 has pgvector images (0.8.7-pg19); adding it is a support decision"
    ]


def test_new_vchord_date_is_reported() -> None:
    assert lagging(_images(), [], ["pg17-20260601"]) == [
        "tensorchord/vchord-suite:pg17-20260501 is behind pg17-20260601"
    ]


def test_hub_follows_next_page() -> None:
    pages = [
        io.BytesIO(
            json.dumps(
                {"results": [{"name": "first"}], "next": "https://example.test/page2"}
            ).encode()
        ),
        io.BytesIO(
            json.dumps({"results": [{"name": "second"}], "next": None}).encode()
        ),
    ]
    with patch("scripts.check_postgres_images.urlopen", side_effect=pages) as request:
        assert hub_tags("pgvector/pgvector") == ["first", "second"]
    assert request.call_args_list[1].args[0] == "https://example.test/page2"


@pytest.mark.parametrize(
    "body", [{}, {"results": [{}], "next": None}, {"results": [], "next": 42}]
)
def test_malformed_hub_response_fails(body: dict) -> None:
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ),
        pytest.raises(ValueError),
    ):
        hub_tags("pgvector/pgvector")


def test_page_cap_fails_instead_of_reporting_current() -> None:
    body = json.dumps({"results": [], "next": "https://example.test/next"}).encode()
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            side_effect=lambda *_args, **_kwargs: io.BytesIO(body),
        ),
        pytest.raises(ValueError, match="page"),
    ):
        hub_tags("pgvector/pgvector")
