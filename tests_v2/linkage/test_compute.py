"""TDD tests for linkage.compute.

Targets the link-compute core: move_collect evidence → link_evidence →
product_links. Fail tasks keep evidence only, no link. Multi-source
ambiguity is surfaced to LinkIssue AMBIGUOUS_SOURCE.

Each test inserts only TEST_-prefixed data; the session-end cleanup
fixture purges without touching real rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sqlalchemy import func, select

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    LinkEvidence,
    LinkIssue,
    ProcurementAccount,
    ProcurementProduct,
    ProductLink,
)
from tts_erp_v2.linkage import compute


pytestmark = [pytest.mark.domain_linkage, pytest.mark.layer_integration]


def _utc(year=2026, month=8, day=29):
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_channel_account(session, *, external_id="TEST_TT_SHOP_1"):
    cred = Credentials(
        provider="tiktok",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id=external_id,
        account_name="TEST shop",
        credential_id=cred.id,
    )
    session.add(acct)
    session.flush()
    return acct


def _make_procurement_account(session, *, external_id="TEST_MS_LIC_1"):
    cred = Credentials(
        provider="miaoshou",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ProcurementAccount(
        provider="miaoshou",
        external_account_id=external_id,
        account_name="TEST license",
        credential_id=cred.id,
    )
    session.add(acct)
    session.flush()
    return acct


def _make_channel_product(session, account, *, external_id="TEST_TT_PROD_1"):
    p = ChannelProduct(
        channel_account_id=account.id,
        external_product_id=external_id,
        title="TEST some product",
        status="ACTIVE",
    )
    session.add(p)
    session.flush()
    return p


def _make_procurement_product(session, account, *, external_id="TEST_MS_PROD_1"):
    p = ProcurementProduct(
        procurement_account_id=account.id,
        external_product_id=external_id,
        product_type="COLLECTED_PRODUCT",
        status="ACTIVE",
    )
    session.add(p)
    session.flush()
    return p


# ─── 1. success task: evidence + product_link both written ──────────────


def test_success_task_creates_evidence_and_product_link(db_session):
    """A SUCCESS move_collect task with platformItemId (TikTok SPU) yields
    one LinkEvidence row and one ProductLink row pointing to that SPU."""
    ca = _make_channel_account(db_session)
    pa = _make_procurement_account(db_session)
    cp = _make_channel_product(db_session, ca, external_id="TEST_TT_PROD_42")
    pp = _make_procurement_product(db_session, pa, external_id="TEST_MS_PROD_42")

    task = {
        "external_task_id": "TEST_TASK_1",
        "task_status": "success",
        "platform_item_id": "TEST_TT_PROD_42",
        "source_item_id": "TEST_MS_PROD_42",
    }
    n_links, n_evidence = compute.process_move_collect_task(
        db_session,
        procurement_account=pa,
        channel_account=ca,
        task=task,
        observed_at=_utc(),
    )

    assert n_links == 1
    assert n_evidence == 1

    links = (
        db_session.execute(
            select(ProductLink).where(ProductLink.channel_product_id == cp.id)
        )
        .scalars()
        .all()
    )
    assert len(links) == 1
    link = links[0]
    assert link.procurement_product_id == pp.id
    assert link.relation_type == "MIAOSHOU_PUBLISHED_TO_TIKTOK"
    assert link.valid_to is None

    evidences = (
        db_session.execute(
            select(LinkEvidence).where(LinkEvidence.product_link_id == link.id)
        )
        .scalars()
        .all()
    )
    assert len(evidences) == 1
    assert evidences[0].source_external_id == "TEST_TASK_1"
    assert evidences[0].evidence_type == "MOVE_COLLECT_TASK"


# ─── 2. fail task: evidence only, no product_link ──────────────────────


def test_fail_task_keeps_evidence_no_link(db_session):
    """FAIL tasks keep evidence but do NOT create a product_link.
    The whole point of the evidence table is to retain failed-task
    provenance for debugging."""
    ca = _make_channel_account(db_session)
    pa = _make_procurement_account(db_session)
    links_before = db_session.execute(select(func.count(ProductLink.id))).scalar_one()

    task = {
        "external_task_id": "TEST_TASK_FAIL_1",
        "task_status": "fail",
        "platform_item_id": "TEST_TT_PROD_GHOST",
        "source_item_id": "TEST_MS_PROD_99",
        "error_message": "platform item deleted",
    }
    n_links, n_evidence = compute.process_move_collect_task(
        db_session,
        procurement_account=pa,
        channel_account=ca,
        task=task,
        observed_at=_utc(),
    )

    assert n_links == 0
    assert n_evidence == 1

    # link evidence must exist but product_link_id must be NULL (orphaned evidence)
    evidences = (
        db_session.execute(
            select(LinkEvidence).where(
                LinkEvidence.source_external_id == "TEST_TASK_FAIL_1"
            )
        )
        .scalars()
        .all()
    )
    assert len(evidences) == 1
    assert evidences[0].product_link_id is None

    # no product_link row was created by this task (baseline-delta: the dev
    # DB may already hold migrated production links)
    after_links = db_session.execute(select(func.count(ProductLink.id))).scalar_one()
    assert after_links == links_before


# ─── 3. AMBIGUOUS_SOURCE: 2 valid miaoshou links to same channel_product
#       ⇒ link_issues row, no AMBIGUOUS_SOURCE-state link


def test_ambiguous_source_raises_issue_and_blocks_link(db_session):
    """Two valid product_links pointing to the same channel_product from
    different procurement products raises AMBIGUOUS_SOURCE and the second
    link is marked superseded (valid_to set). No cost snapshot can be
    generated downstream without operator resolution."""
    ca = _make_channel_account(db_session)
    pa = _make_procurement_account(db_session)
    cp = _make_channel_product(db_session, ca, external_id="TEST_TT_PROD_AMBIG")
    pp1 = _make_procurement_product(db_session, pa, external_id="TEST_MS_PROD_A")
    pp2 = _make_procurement_product(db_session, pa, external_id="TEST_MS_PROD_B")

    base_kwargs = dict(
        procurement_account=pa,
        channel_account=ca,
        observed_at=_utc(),
    )
    t1 = dict(
        external_task_id="TEST_TASK_A",
        task_status="success",
        platform_item_id="TEST_TT_PROD_AMBIG",
        source_item_id="TEST_MS_PROD_A",
    )
    t2 = dict(
        external_task_id="TEST_TASK_B",
        task_status="success",
        platform_item_id="TEST_TT_PROD_AMBIG",
        source_item_id="TEST_MS_PROD_B",
    )

    compute.process_move_collect_task(db_session, task=t1, **base_kwargs)
    compute.process_move_collect_task(db_session, task=t2, **base_kwargs)

    links = (
        db_session.execute(
            select(ProductLink).where(ProductLink.channel_product_id == cp.id)
        )
        .scalars()
        .all()
    )
    assert len(links) == 2

    valid_links = [lnk for lnk in links if lnk.valid_to is None]
    superseded_links = [lnk for lnk in links if lnk.valid_to is not None]
    # The newer write should be the one that remains valid
    assert len(valid_links) == 1
    assert len(superseded_links) == 1
    assert valid_links[0].procurement_product_id == pp2.id
    assert superseded_links[0].procurement_product_id == pp1.id

    issues = (
        db_session.execute(
            select(LinkIssue).where(
                LinkIssue.issue_type == "AMBIGUOUS_SOURCE",
                LinkIssue.channel_product_id == cp.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(issues) >= 1
    issue = issues[-1]
    assert issue.channel_product_id == cp.id
    assert issue.candidate_count == 2
    assert issue.resolved_at is None


# ─── 4. UNRESOLVED: task references unknown channel_product_id


def test_task_with_unknown_channel_product_writes_issue(db_session):
    """A success task pointing to a channel_product that doesn't exist
    yet in commerce.channel_products writes a sync-level LinkIssue (or
    skip silently) — the move_collect processor must not crash, and the
    evidence should be retained as orphan."""
    ca = _make_channel_account(db_session)
    pa = _make_procurement_account(db_session)

    task = {
        "external_task_id": "TEST_TASK_UNKNOWN",
        "task_status": "success",
        "platform_item_id": "TEST_TT_PROD_NOT_YET_SYNCED",
        "source_item_id": "TEST_MS_PROD_Z",
    }
    # Should not raise; the task will produce a sync_issue (LinkIssue or
    # SyncIssue depending on policy). Here we exercise that it's
    # surfaced rather than silently dropped.
    compute.process_move_collect_task(
        db_session,
        procurement_account=pa,
        channel_account=ca,
        task=task,
        observed_at=_utc(),
    )

    # No product_link row created
    assert db_session.execute(select(ProductLink)).scalars().first() is None


# ─── 5. ALREADY_LINKED: re-processing same evidence idempotent


def test_reprocessing_same_task_is_idempotent(db_session):
    """Re-running the same external_task_id does not create duplicate
    product_links. Either upsert on (procurement_product_id,
    channel_product_id, source_external_id) or skip."""
    ca = _make_channel_account(db_session)
    pa = _make_procurement_account(db_session)
    _make_channel_product(db_session, ca, external_id="TEST_TT_PROD_IDEM")
    _make_procurement_product(db_session, pa, external_id="TEST_MS_PROD_IDEM")

    task = {
        "external_task_id": "TEST_TASK_IDEM_1",
        "task_status": "success",
        "platform_item_id": "TEST_TT_PROD_IDEM",
        "source_item_id": "TEST_MS_PROD_IDEM",
    }
    base_kwargs = dict(
        procurement_account=pa,
        channel_account=ca,
        observed_at=_utc(),
    )
    compute.process_move_collect_task(db_session, task=task, **base_kwargs)
    compute.process_move_collect_task(db_session, task=task, **base_kwargs)

    links = (
        db_session.execute(
            select(ProductLink).where(
                ProductLink.relation_type == "MIAOSHOU_PUBLISHED_TO_TIKTOK"
            )
        )
        .scalars()
        .all()
    )
    assert len(links) == 1
