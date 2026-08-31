"""Tests for ``tts_erp_v2.jobs.miaoshou.move_collect``.

Critical regression test (★ acceptance criterion):
``paginate_with_retry`` is invoked with the upstream client. When the
upstream returns alternating ``accountApiQpsRateLimit`` empty pages
followed by real pages, the job must walk all pages (not terminate at
the first rate-limit empty). This is the bug-fix carrier from the
silent-truncation incident (237 → 20 records).

The test simulates the production scenario from
``miaoshou/README.md``:

* 12 pages × 20 items = 237+ records.
* Pages 2, 5, 9 alternate between ``accountApiQpsRateLimit`` empty
  and a real (full) page on the retry.
* The job must still write 12 × 20 = 240 evidence rows.

We keep the test pure: the fake client is injected via the
``client=`` parameter; no network, no real SDK, no real DB writes
outside the test-owned transaction (rolled back at teardown).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from sqlalchemy import select

from tts_erp_v2.db.models.integration import (
    RawRecord,
    SyncIssue,
    SyncJob,
)
from tts_erp_v2.db.models.linkage import LinkEvidence
from tts_erp_v2.jobs.miaoshou.move_collect import sync_move_collect


pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


# ---- helpers --------------------------------------------------------


def _make_task(task_id: str, *, status: str = "success") -> dict:
    """Build a single task row in the production shape."""
    return {
        "moveCollectTaskDetailId": task_id,
        "collectBoxDetailId": f"cbd_{task_id}",
        "shopId": "17060852",
        "platformItemId": f"1737{task_id[-8:]}",
        "source": "1688",
        "sourceItemId": f"src_{task_id}",
        "sourceItemUrl": f"http://detail.1688.com/offer/src_{task_id}.html",
        "title": f"TEST widget {task_id}",
        "status": status,
        "gmtCreate": "2026-08-29 12:00:00",
        "gmtModified": "2026-08-29 12:30:00",
        "isRenewItem": False,
    }


def _build_paginated_side_effect(
    *,
    items_per_page: int = 20,
    total_pages: int = 12,
    rate_limit_pages: tuple[int, ...] = (2, 5, 9),
):
    """Build a fake-client side_effect that simulates rate-limited pages.

    For each ``rate_limit_pages`` entry, the *first* invocation of that
    page returns the rate-limit shape; the second returns the real
    page. Other pages return real data on the first call.
    """
    call_log: list[int] = []

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("pageNo", 1))
        call_log.append(page)
        if page in rate_limit_pages and call_log.count(page) == 1:
            # FIRST call for this page returns the rate-limit shape;
            # the SECOND (retry) returns real data.
            return {
                "result": "fail",
                "data": None,
                "code": "accountApiQpsRateLimit",
                "reason": "rate limit",
            }
        return {
            "result": "success",
            "data": {
                "moveCollectDetailList": [
                    _make_task(f"t_{page}_{i}") for i in range(items_per_page)
                ],
                "total": items_per_page * total_pages,
                "totalPage": total_pages,
            },
        }

    return side_effect, call_log


# ---- ★ acceptance test: rate-limit retry walks all 12 pages ---------


def test_move_collect_walks_all_pages_through_rate_limit_retrys(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """★ Acceptance criterion: ``accountApiQpsRateLimit`` alternating
    responses must NOT cause silent truncation. The job must walk
    through all 12 pages and persist every item as evidence.
    """
    side_effect, call_log = _build_paginated_side_effect(
        items_per_page=20,
        total_pages=12,
        rate_limit_pages=(2, 5, 9),
    )
    fake_client.install(side_effect)

    result = sync_move_collect(
        db_session,
        client=fake_client,
        max_retries=3,
    )

    db_session.commit()

    # All 12 pages walked.
    assert result["pages_walked"] == 12
    # 12 pages × 20 items = 240 records.
    assert result["tasks_seen"] == 240
    assert result["evidence_inserted"] == 240
    # 3 pages were rate-limited once each → 3 retries observed.
    assert result["rate_limit_retries"] >= 3
    assert result["issues"] == 0

    # Rate-limited pages were each called twice (first fail + retry).
    for page in (2, 5, 9):
        assert call_log.count(page) == 2, (
            f"page {page} should be called twice (rate-limit + retry)"
        )

    # SyncJob row exists with status='succeeded' and the right counters.
    job = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "miaoshou.move_collect")
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.rows_total == 240
    assert job.rows_inserted == 240
    assert job.rows_failed == 0
    assert job.extra["rate_limit_retries"] >= 3
    assert job.extra["pages_walked"] == 12


def test_move_collect_writes_raw_records_per_task(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Each task gets one ``integration.raw_records`` row."""
    side_effect, _ = _build_paginated_side_effect(
        items_per_page=5, total_pages=2, rate_limit_pages=()
    )
    fake_client.install(side_effect)

    sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()

    raw_rows = (
        db_session.execute(
            select(RawRecord).where(
                RawRecord.endpoint == "miaoshou.move_collect.search_move_collect_list"
            )
        )
        .scalars()
        .all()
    )
    # 2 pages × 5 items = 10 raw records.
    assert len(raw_rows) == 10
    # Each carries the task detail id as external_id.
    sample = raw_rows[0]
    assert sample.external_id is not None
    assert sample.payload_hash is not None


def test_move_collect_writes_link_evidence(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Per-task ``linkage.link_evidence`` rows get inserted with the
    right shape."""
    side_effect, _ = _build_paginated_side_effect(
        items_per_page=3, total_pages=1, rate_limit_pages=()
    )
    fake_client.install(side_effect)

    sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()

    evidence = (
        db_session.execute(
            select(LinkEvidence).where(
                LinkEvidence.evidence_type == "MOVE_COLLECT_TASK",
                LinkEvidence.source_external_id.like("t_%"),
            )
        )
        .scalars()
        .all()
    )
    assert len(evidence) == 3
    sample = evidence[0]
    assert sample.source_table == "miaoshou.move_collect"
    assert sample.source_external_id is not None
    # Evidence payload preserves platformItemId (SPU, not SKU).
    assert sample.evidence_payload["platform_item_id"] is not None
    assert sample.evidence_payload["platform"] == "tiktok"
    # The raw-record link isn't enforced by FK but the evidence should
    # be self-sufficient for Lane D's link-compute job to use.
    assert isinstance(sample.observed_at, datetime)


def test_move_collect_idempotent(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Re-running with the same fake client must NOT create duplicate
    ``link_evidence`` rows. The job's idempotency guarantee is the
    (source_table, source_external_id) dedup.
    """
    side_effect, _ = _build_paginated_side_effect(
        items_per_page=3, total_pages=1, rate_limit_pages=()
    )
    fake_client.install(side_effect)

    sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()
    first_count = len(
        db_session.execute(
            select(LinkEvidence).where(
                LinkEvidence.evidence_type == "MOVE_COLLECT_TASK",
                LinkEvidence.source_external_id.like("t_%"),
            )
        ).scalars().all()
    )
    assert first_count == 3

    # Second run: same tasks → should not duplicate.
    sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()
    second_count = len(
        db_session.execute(
            select(LinkEvidence).where(
                LinkEvidence.evidence_type == "MOVE_COLLECT_TASK",
                LinkEvidence.source_external_id.like("t_%"),
            )
        ).scalars().all()
    )
    assert second_count == 3, (
        f"idempotency broken: re-run added {second_count - first_count} extra evidence rows"
    )


def test_move_collect_handles_empty_response(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Empty move-collect list must terminate cleanly."""
    def side_effect(*, path, body, **_kwargs):
        return {"result": "success", "data": {"moveCollectDetailList": [], "total": 0, "totalPage": 1}}

    fake_client.install(side_effect)

    result = sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()

    assert result["tasks_seen"] == 0
    assert result["evidence_inserted"] == 0
    assert result["issues"] == 0
    # SyncJob row still marked succeeded with 0 counters.
    job = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "miaoshou.move_collect")
    ).scalar_one()
    assert job.status == "succeeded"
    assert job.rows_total == 0


def test_move_collect_skips_non_dict_items(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Items that aren't dicts land in ``sync_issues`` and the job continues."""
    def side_effect(*, path, body, **_kwargs):
        return {
            "result": "success",
            "data": {
                "moveCollectDetailList": [
                    _make_task("t_1_0"),
                    "this-should-be-a-dict",
                    _make_task("t_1_2"),
                ],
                "total": 3,
                "totalPage": 1,
            },
        }

    fake_client.install(side_effect)
    result = sync_move_collect(db_session, client=fake_client, max_retries=2)
    db_session.commit()

    assert result["tasks_seen"] == 3
    assert result["evidence_inserted"] == 2
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "miaoshou.move_collect")
    ).scalar_one()
    assert issue.issue_type == "MOVE_COLLECT_PARSE_FAILED"


def test_move_collect_status_filter(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """The ``status`` filter is passed to the upstream body."""
    side_effect, _ = _build_paginated_side_effect(
        items_per_page=2, total_pages=1, rate_limit_pages=()
    )
    fake_client.install(side_effect)

    sync_move_collect(
        db_session, client=fake_client, status="success", max_retries=2
    )
    db_session.commit()

    # The status filter must appear in the body sent to the upstream.
    bodies = [c["body"] for c in fake_client.calls]
    assert any(b.get("filter", {}).get("status") == "success" for b in bodies), (
        "status filter not forwarded to upstream body"
    )
