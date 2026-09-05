"""Pure-function issue detectors.

Each detector returns a dict with the LinkIssue payload keys, or None
when no issue applies. The caller (link-compute job or API handler) is
responsible for inserting the row.

Design choice: detectors are pure (no DB) so they can be unit-tested
without fixtures. They take ids that already exist (FK references are
caller's responsibility), so we keep IO and detection separate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def make_issue(
    *,
    issue_type: str,
    spu_pk: int | None = None,
    procurement_product_id: int | None = None,
    candidate_count: int | None = None,
    details: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a generic LinkIssue payload dict."""
    return {
        "issue_type": issue_type,
        "spu_pk": spu_pk,
        "procurement_product_id": procurement_product_id,
        "candidate_count": candidate_count,
        "status": "OPEN",
        "details": details or {},
        "created_at": observed_at,
        "resolved_at": None,
    }


def detect_product_link_missing(
    *,
    spu_id: str,
    spu_pk: int,
    procurement_product_external_id: str,
    procurement_product_id: int,
    observed_at: datetime,
) -> dict[str, Any]:
    """Issue: no link exists between a TikTok SPU and its expected
    miaoshou counterpart. Surfaced when the link-compute job cannot
    derive a product_link for a known (SPU, procurement_product) pair."""
    return make_issue(
        issue_type="PRODUCT_LINK_MISSING",
        spu_pk=spu_pk,
        procurement_product_id=procurement_product_id,
        candidate_count=0,
        details={
            "channel_external_id": spu_id,
            "procurement_external_id": procurement_product_external_id,
        },
        observed_at=observed_at,
    )


def detect_multiple_primary_links(
    *,
    spu_pk: int,
    primary_link_ids: list[int],
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Issue: more than one link marked is_primary=True for the same
    channel_product. Returns None if 0 or 1 primaries."""
    if len(primary_link_ids) <= 1:
        return None
    return make_issue(
        issue_type="MULTIPLE_PRIMARY_LINKS",
        spu_pk=spu_pk,
        candidate_count=len(primary_link_ids),
        details={"primary_link_ids": list(primary_link_ids)},
        observed_at=observed_at,
    )


def detect_ambiguous_source(
    *,
    spu_pk: int,
    candidate_count: int,
    candidate_procurement_ids: list[int] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Issue: a channel_product has >1 valid (non-superseded) miaoshou
    product_link from different procurement products. Returns None when
    only one valid source exists."""
    if candidate_count <= 1:
        return None
    return make_issue(
        issue_type="AMBIGUOUS_SOURCE",
        spu_pk=spu_pk,
        candidate_count=candidate_count,
        details={
            "candidate_procurement_ids": list(candidate_procurement_ids or []),
        },
        observed_at=observed_at,
    )
