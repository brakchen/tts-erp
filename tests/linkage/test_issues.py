"""TDD tests for linkage.issues — detector functions.

These are pure functions returning ORM objects (or list thereof) that
the link-compute job (or any operator dashboard) can persist. They do
NOT touch the database on their own.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tts_erp_v2.linkage import issues

pytestmark = [pytest.mark.domain_linkage, pytest.mark.layer_integration]


def _ts() -> datetime:
    return datetime(2026, 8, 29, tzinfo=UTC)


# ─── PRODUCT_LINK_MISSING ─────────────────────────────────────────────


def test_detect_product_link_missing_returns_issue_payload():
    out = issues.detect_product_link_missing(
        channel_product_external_id="TEST_TT_PROD_X",
        channel_product_id=10,
        procurement_product_external_id="TEST_MS_PROD_X",
        procurement_product_id=20,
        observed_at=_ts(),
    )
    assert out["issue_type"] == "PRODUCT_LINK_MISSING"
    assert out["channel_product_id"] == 10
    assert out["procurement_product_id"] == 20
    assert out["candidate_count"] == 0
    assert out["details"]["channel_external_id"] == "TEST_TT_PROD_X"
    assert out["status"] == "OPEN"


# ─── MULTIPLE_PRIMARY_LINKS ───────────────────────────────────────────


def test_detect_multiple_primary_links_two_primaries():
    out = issues.detect_multiple_primary_links(
        channel_product_id=42,
        primary_link_ids=[1, 2],
        observed_at=_ts(),
    )
    assert out["issue_type"] == "MULTIPLE_PRIMARY_LINKS"
    assert out["candidate_count"] == 2
    assert out["channel_product_id"] == 42
    assert out["details"]["primary_link_ids"] == [1, 2]


def test_detect_multiple_primary_links_zero_returns_none():
    out = issues.detect_multiple_primary_links(
        channel_product_id=42, primary_link_ids=[]
    )
    assert out is None


# ─── AMBIGUOUS_SOURCE ─────────────────────────────────────────────────


def test_detect_ambiguous_source_payload():
    out = issues.detect_ambiguous_source(
        channel_product_id=99,
        candidate_count=2,
        candidate_procurement_ids=[10, 11],
        observed_at=_ts(),
    )
    assert out["issue_type"] == "AMBIGUOUS_SOURCE"
    assert out["channel_product_id"] == 99
    assert out["candidate_count"] == 2
    assert out["details"]["candidate_procurement_ids"] == [10, 11]


def test_detect_ambiguous_source_single_returns_none():
    out = issues.detect_ambiguous_source(channel_product_id=99, candidate_count=1)
    assert out is None


# ─── generic helper ───────────────────────────────────────────────────


def test_make_issue_base_fields():
    out = issues.make_issue(
        issue_type="SOURCE_LINK_CONFLICT",
        channel_product_id=5,
        candidate_count=3,
        details={"foo": "bar"},
        observed_at=_ts(),
    )
    assert out["issue_type"] == "SOURCE_LINK_CONFLICT"
    assert out["status"] == "OPEN"
    assert out["resolved_at"] is None
    assert out["created_at"] == _ts()
    assert out["details"] == {"foo": "bar"}
