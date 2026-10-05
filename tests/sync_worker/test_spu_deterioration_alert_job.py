from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelAccount, ChannelProduct, SpuDeteriorationAlert
from tts_erp_v2.jobs.spu_deterioration_alert import materialize_alerts, run_scheduled

pytestmark = pytest.mark.domain_sync


def test_run_scheduled_replaces_all_anchors_and_records_later_failure(
    monkeypatch,
) -> None:
    session = MagicMock()
    anchors = {date(2026, 10, 9): [1], date(2026, 10, 10): [2]}
    replacement = MagicMock(side_effect=[1, RuntimeError("batch failed")])
    failure = MagicMock(return_value={"status": "failed"})
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.build_materialized_rows",
        lambda _session: anchors,
    )
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.replace_anchor", replacement
    )
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert._record_failure", failure
    )
    assert run_scheduled(session) == {"status": "failed"}
    assert replacement.call_count == 2
    failure.assert_called_once()


def test_run_scheduled_materializes_and_replaces_idempotently(db_engine) -> None:
    if not inspect(db_engine).has_table("spu_deterioration_alerts", schema="analytics"):
        pytest.skip("alert migration is not applied in the isolated template")
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_alert_job_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        session.add(
            ChannelProduct(
                shop_pk=shop.id, spu_id="TEST_alert_job_spu", status="active"
            )
        )
        session.commit()
        shop_pk = shop.id
    try:
        with Session(db_engine) as session:
            first = run_scheduled(session)
            assert first["status"] == "success"
            first_count = session.scalar(
                select(SpuDeteriorationAlert.id)
                .where(SpuDeteriorationAlert.shop_pk == shop_pk)
                .order_by(SpuDeteriorationAlert.id.desc())
                .limit(1)
            )
            assert first_count is not None
        with Session(db_engine) as session:
            second = run_scheduled(session)
            assert second["status"] == "success"
            rows = session.scalars(
                select(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            ).all()
            assert len(rows) > 0
            assert second["rows"] == first["rows"]
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()


def test_materialize_alerts_uses_anchor_replacement_seam(monkeypatch) -> None:
    session = MagicMock()
    replacement = MagicMock(return_value=2)
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.replace_anchor", replacement
    )
    result = materialize_alerts(session, anchor_date=date(2026, 10, 10), rows=[])
    replacement.assert_called_once_with(
        session, rows=[], anchor_date=date(2026, 10, 10)
    )
    assert result == {"status": "success", "anchorDate": "2026-10-10", "rows": 2}
