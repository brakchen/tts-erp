"""link-compute core: turn miaoshou move-collect tasks into evidence +
product_links, and surface AMBIGUOUS_SOURCE issues.

Pure SQLAlchemy — caller's session is responsible for commit. Each call
to ``process_move_collect_task`` handles ONE task dict.

Task dict shape (matching miaoshou ``search_move_collect_list``):
    {
        "external_task_id": str,
        "task_status": "success" | "fail" | ...,
        "platform_item_id": str | None,   # TikTok SPU id
        "source_item_id": str | None,     # miaoshou procurement product external id
        "error_message": str | None,      # populated when fail
        "source_table": str | None,       # e.g. "miaoshou_move_collect_tasks"
    }

Idempotency: a duplicate task with the same external_task_id is a
no-op on product_links (the second call still writes orphan evidence,
which is desired — the operator can audit later).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelProduct,
    LinkEvidence,
    LinkIssue,
    ProcurementAccount,
    ProcurementProduct,
    ProductLink,
)
from tts_erp_v2.linkage import issues as issue_detectors


def process_move_collect_task(
    session: Session,
    *,
    procurement_account: ProcurementAccount,
    channel_account: Any,  # ChannelAccount; avoided to prevent cycle
    task: dict[str, Any],
    observed_at: datetime,
) -> tuple[int, int]:
    """Process one move-collect task. Returns (n_links_written,
    n_evidence_written). Idempotent on duplicate external_task_id."""
    external_task_id = task.get("external_task_id")
    task_status = (task.get("task_status") or "").lower()
    platform_item_id = task.get("platform_item_id")
    source_item_id = task.get("source_item_id")

    evidence_payload = {
        "task_status": task_status,
        "error_message": task.get("error_message"),
        "raw": task,
    }

    # ── 1. fail tasks keep evidence only ────────────────────────────
    if task_status != "success":
        ev = LinkEvidence(
            product_link_id=None,
            variant_link_id=None,
            evidence_type="MOVE_COLLECT_TASK",
            source_table=task.get("source_table") or "miaoshou_move_collect_tasks",
            source_external_id=external_task_id,
            evidence_payload=evidence_payload,
            observed_at=observed_at,
        )
        session.add(ev)
        session.flush()
        return 0, 1

    # ── 2. resolve SPU and procurement_product by external id ───────
    cp = session.execute(
        select(ChannelProduct).where(
            ChannelProduct.channel_account_id == channel_account.id,
            ChannelProduct.external_product_id == platform_item_id,
        )
    ).scalar_one_or_none()
    pp = session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.procurement_account_id == procurement_account.id,
            ProcurementProduct.external_product_id == source_item_id,
        )
    ).scalar_one_or_none()

    if cp is None or pp is None:
        # Unknown SPU / unknown procurement product — keep orphan evidence
        ev = LinkEvidence(
            product_link_id=None,
            variant_link_id=None,
            evidence_type="MOVE_COLLECT_TASK",
            source_table=task.get("source_table") or "miaoshou_move_collect_tasks",
            source_external_id=external_task_id,
            evidence_payload={
                **evidence_payload,
                "unresolved_channel_product_id": platform_item_id,
                "unresolved_procurement_product_id": source_item_id,
            },
            observed_at=observed_at,
        )
        session.add(ev)
        session.flush()
        # Surface a LinkIssue so the operator can resolve it. We
        # default to PRODUCT_LINK_MISSING when the channel_product
        # exists but procurement doesn't, or vice versa.
        if cp is None and pp is None:
            issue_payload = issue_detectors.make_issue(
                issue_type="PRODUCT_LINK_MISSING",
                details={
                    "external_task_id": external_task_id,
                    "platform_item_id": platform_item_id,
                    "source_item_id": source_item_id,
                },
                observed_at=observed_at,
            )
        elif cp is None:
            issue_payload = issue_detectors.make_issue(
                issue_type="PRODUCT_LINK_MISSING",
                procurement_product_id=pp.id if pp else None,
                details={
                    "external_task_id": external_task_id,
                    "platform_item_id": platform_item_id,
                    "reason": "channel_product not yet synced",
                },
                observed_at=observed_at,
            )
        else:
            issue_payload = issue_detectors.make_issue(
                issue_type="PRODUCT_LINK_MISSING",
                channel_product_id=cp.id,
                details={
                    "external_task_id": external_task_id,
                    "source_item_id": source_item_id,
                    "reason": "procurement_product not yet synced",
                },
                observed_at=observed_at,
            )
        session.add(LinkIssue(**issue_payload))
        session.flush()
        return 0, 1

    # ── 3. idempotency: same (cp, pp) link already valid? ──────────
    existing_valid = session.execute(
        select(ProductLink).where(
            ProductLink.channel_product_id == cp.id,
            ProductLink.procurement_product_id == pp.id,
            ProductLink.valid_to.is_(None),
        )
    ).scalar_one_or_none()
    if existing_valid is not None:
        # Idempotent: just append an evidence row referencing the link.
        ev = LinkEvidence(
            product_link_id=existing_valid.id,
            variant_link_id=None,
            evidence_type="MOVE_COLLECT_TASK",
            source_table=task.get("source_table") or "miaoshou_move_collect_tasks",
            source_external_id=external_task_id,
            evidence_payload=evidence_payload,
            observed_at=observed_at,
        )
        session.add(ev)
        session.flush()
        return 0, 1

    # ── 4. AMBIGUOUS_SOURCE handling: supersede older valid link ────
    # If cp already has a valid product_link pointing at a different
    # procurement_product, supersede it (set valid_to) and surface an
    # AMBIGUOUS_SOURCE issue. The newer write wins as the current
    # effective link.
    older_valid_links = session.execute(
        select(ProductLink).where(
            ProductLink.channel_product_id == cp.id,
            ProductLink.valid_to.is_(None),
        )
    ).scalars().all()
    superseded_procurement_ids = [
        lnk.procurement_product_id for lnk in older_valid_links
    ]

    for lnk in older_valid_links:
        lnk.valid_to = observed_at
    if older_valid_links:
        session.flush()

    link = ProductLink(
        procurement_product_id=pp.id,
        channel_product_id=cp.id,
        external_relation_id=external_task_id,
        relation_type="MIAOSHOU_PUBLISHED_TO_TIKTOK",
        status="ACTIVE",
        is_primary=False,
        valid_from=observed_at,
        valid_to=None,
        source_updated_at=observed_at,
        raw_record_id=None,
    )
    session.add(link)
    session.flush()

    ev = LinkEvidence(
        product_link_id=link.id,
        variant_link_id=None,
        evidence_type="MOVE_COLLECT_TASK",
        source_table=task.get("source_table") or "miaoshou_move_collect_tasks",
        source_external_id=external_task_id,
        evidence_payload=evidence_payload,
        observed_at=observed_at,
    )
    session.add(ev)
    session.flush()

    # If there were older valid links, this is now an ambiguous state.
    if superseded_procurement_ids:
        issue_payload = issue_detectors.detect_ambiguous_source(
            channel_product_id=cp.id,
            candidate_count=len(superseded_procurement_ids) + 1,
            candidate_procurement_ids=[*superseded_procurement_ids, pp.id],
            observed_at=observed_at,
        )
        if issue_payload is not None:
            session.add(LinkIssue(**issue_payload))
            session.flush()

    return 1, 1
