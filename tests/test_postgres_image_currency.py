"""Offline checks for the PostgreSQL image currency job."""

import io
import json
from unittest.mock import patch

import pytest
from scripts.check_postgres_images import hub_tags, lagging

IMAGES = [
    "pgvector/pgvector:0.7.0-pg15",
    "pgvector/pgvector:0.7.4-pg17",
    *[f"pgvector/pgvector:0.8.7-pg{major}" for major in range(15, 19)],
    "tensorchord/vchord-suite:pg17-20260501",
]
PG_TAGS = [f"0.8.7-pg{major}" for major in range(15, 19)]
VC_TAGS = ["pg17-20260501"]


def test_new_pgvector_patch_names_only_the_lagging_image() -> None:
    assert lagging(IMAGES, [*PG_TAGS, "0.8.8-pg17"], VC_TAGS) == [
        "pgvector/pgvector:0.8.7-pg17 is behind 0.8.8-pg17"
    ]


def test_new_pgvector_minor_flags_each_tested_major() -> None:
    tags = [f"0.9.0-pg{major}" for major in range(15, 19)]
    assert lagging(IMAGES, tags, VC_TAGS) == [
        f"pgvector/pgvector:0.8.7-pg{major} is behind 0.9.0-pg{major}"
        for major in range(15, 19)
    ]


def test_floors_equal_tags_and_variants_are_ignored() -> None:
    tags = [*PG_TAGS, "0.7.9-pg15", "0.7.5-pg17", "0.8.8-pg17-bookworm"]
    variants = [*VC_TAGS, "pg17-20260601-arm64", "pg17-v0.4.3", "pg18-20260601"]
    assert lagging(IMAGES, tags, variants) == []


def test_new_major_is_a_support_decision() -> None:
    assert lagging(IMAGES, [*PG_TAGS, "0.8.7-pg19"], VC_TAGS) == [
        "PostgreSQL 19 has pgvector images (0.8.7-pg19); adding it is a support decision"
    ]


def test_new_vchord_date_is_reported() -> None:
    assert lagging(IMAGES, PG_TAGS, [*VC_TAGS, "pg17-20260601"]) == [
        "tensorchord/vchord-suite:pg17-20260501 is behind pg17-20260601"
    ]


@pytest.mark.parametrize(
    ("pg_tags", "vc_tags", "missing"),
    [
        ([], VC_TAGS, "15"),
        (PG_TAGS, [], "17"),
        (["0.8.8-pg15-bookworm"], VC_TAGS, "15"),
        (PG_TAGS, ["pg17-v0.4.3"], "17"),
        (
            [tag for tag in PG_TAGS if not tag.endswith("pg16")],
            VC_TAGS,
            "16",
        ),
    ],
)
def test_missing_comparable_tags_fail(
    pg_tags: list[str], vc_tags: list[str], missing: str
) -> None:
    with pytest.raises(KeyError, match=missing):
        lagging(IMAGES, pg_tags, vc_tags)


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


@pytest.mark.parametrize("body", [{}, {"results": [{}], "next": None}])
def test_malformed_hub_response_fails(body: dict) -> None:
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ),
        pytest.raises(KeyError),
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
