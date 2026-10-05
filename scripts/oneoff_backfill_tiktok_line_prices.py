"""Guarded, resumable backfill of TikTok line price observations.

The default mode is a read-only preview. Only raw captures referenced by the
current normalized order *and* line rows are considered. That lineage is the
shop-ownership proof because integration.raw_records intentionally has no shop
column; an ambiguous pointer is rejected rather than guessed.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _db_url() -> str:
    value = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("TTS_ERP_DB_URL_TEST")
    if not value:
        raise SystemExit("TTS_ERP_DB_URL or TTS_ERP_DB_URL_TEST is required")
    return value.replace("postgresql://", "postgresql+psycopg://", 1)


def _order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    order = data.get("order") if isinstance(data, dict) else None
    if isinstance(order, dict):
        return order
    orders = data.get("orders") if isinstance(data, dict) else None
    if isinstance(orders, list) and orders and isinstance(orders[0], dict):
        return orders[0]
    return data if isinstance(data, dict) else {}


def _endpoint_kind(endpoint: str) -> str:
    return "ORDER_DETAIL" if endpoint == "/order/202309/orders" else "ORDER_SEARCH"


def _run(args: argparse.Namespace) -> dict[str, int]:
    from sqlalchemy import and_, create_engine, or_, select, text
    from sqlalchemy.orm import Session

    from tts_erp_v2.db.models.commerce import SalesOrder, SalesOrderLine
    from tts_erp_v2.db.models.integration import RawRecord
    from tts_erp_v2.jobs.tiktok.order_prices import (
        normalize_price_line,
        payload_hash,
        persist_price_observation,
    )

    engine = create_engine(_db_url())
    counts = {
        "scanned": 0,
        "inserted": 0,
        "duplicate_noop": 0,
        "unmatched": 0,
        "invalid": 0,
        "unknown_gift": 0,
        "stale_skip": 0,
    }
    with Session(engine) as session:
        last_id = args.start_raw_id
        while True:
            query = (
                select(RawRecord)
                .join(SalesOrder, SalesOrder.raw_record_id == RawRecord.id)
                .join(
                    SalesOrderLine,
                    and_(
                        SalesOrderLine.raw_record_id == RawRecord.id,
                        SalesOrderLine.order_pk == SalesOrder.id,
                    ),
                )
                .where(SalesOrder.shop_pk == args.shop_pk)
                .where(RawRecord.id > last_id)
                .where(
                    RawRecord.endpoint.in_(
                        ["/order/202309/orders/search", "/order/202309/orders"]
                    )
                )
                .distinct()
            )
            if args.end_raw_id is not None:
                query = query.where(RawRecord.id <= args.end_raw_id)
            query = query.order_by(RawRecord.id).limit(args.batch_size)
            if args.dry_run:
                session.execute(text("SET TRANSACTION READ ONLY"))
            rows = session.execute(query).scalars().all()
            if not rows:
                break
            for raw_record in rows:
                last_id = raw_record.id
                counts["scanned"] += 1
                references = session.execute(
                    select(SalesOrder.id, SalesOrder.shop_pk, SalesOrder.order_id)
                    .join(SalesOrderLine, SalesOrderLine.order_pk == SalesOrder.id)
                    .where(
                        or_(
                            SalesOrder.raw_record_id == raw_record.id,
                            SalesOrderLine.raw_record_id == raw_record.id,
                        )
                    )
                    .distinct()
                ).all()
                if (
                    len(references) != 1
                    or references[0].shop_pk != args.shop_pk
                ):
                    counts["unmatched"] += 1
                    continue
                order_pk, _shop_pk, referenced_order_id = references[0]
                order = _order_payload(raw_record.payload)
                payload_order_id = str(order.get("order_id") or order.get("id") or "")
                if (
                    not payload_order_id
                    or raw_record.external_id != payload_order_id
                    or referenced_order_id != payload_order_id
                ):
                    counts["unmatched"] += 1
                    continue
                order_row = session.execute(
                    select(SalesOrder).where(SalesOrder.id == order_pk)
                ).scalar_one()
                raw_version = order.get("update_time")
                raw_version_at = None
                try:
                    if raw_version is not None and int(raw_version) > 0:
                        raw_version_at = datetime.fromtimestamp(int(raw_version), tz=UTC)
                except (TypeError, ValueError, OverflowError):
                    raw_version_at = None
                if (
                    raw_version_at is not None
                    and order_row.order_modify_time is not None
                    and raw_version_at < order_row.order_modify_time
                ):
                    counts["stale_skip"] += 1
                    continue
                # These values are from this raw capture only. Never borrow
                # status/currency from the mutable normalized order row.
                parent_status = order.get("status") or order.get("order_status")
                payment = order.get("payment") or {}
                parent_currency = order.get("currency") or payment.get("currency")
                for raw_line in order.get("line_items") or []:
                    line_id = str(raw_line.get("line_id") or raw_line.get("id") or "")
                    line_row = session.execute(
                        select(SalesOrderLine).where(
                            SalesOrderLine.order_pk == order_pk,
                            SalesOrderLine.external_line_id == line_id,
                            SalesOrderLine.raw_record_id == raw_record.id,
                        )
                    ).scalar_one_or_none()
                    if line_row is None:
                        counts["unmatched"] += 1
                        continue
                    observation = normalize_price_line(
                        raw_line,
                        shop_pk=args.shop_pk,
                        order_pk=order_pk,
                        external_line_id=line_id,
                        raw_record_id=raw_record.id,
                        source_endpoint=_endpoint_kind(raw_record.endpoint),
                        source_payload_hash=payload_hash(raw_line),
                        source_captured_at=raw_record.captured_at,
                        source_order_version_at=raw_version_at,
                        parent_status=parent_status,
                        parent_currency=parent_currency,
                        spu_pk=line_row.spu_pk,
                    )
                    if observation.gift_status == "UNKNOWN":
                        counts["unknown_gift"] += 1
                    if (
                        observation.quantity_status
                        not in {"OBSERVED", "DEFAULT_ONE_PER_LINE"}
                        or observation.original_price_status != "OBSERVED"
                        or observation.paid_price_status != "OBSERVED"
                    ):
                        counts["invalid"] += 1
                    if args.dry_run:
                        continue
                    before = session.execute(
                        select(text("1"))
                        .select_from(text("commerce.sales_order_line_price_observations"))
                        .where(
                            text(
                                "shop_pk = :shop_pk AND order_pk = :order_pk "
                                "AND external_line_id = :line_id "
                                "AND semantic_observation_hash = :semantic_hash"
                            )
                        )
                        .params(
                            shop_pk=args.shop_pk,
                            order_pk=order_pk,
                            line_id=line_id,
                            semantic_hash=observation.semantic_observation_hash,
                        )
                    ).first()
                    persist_price_observation(
                        session,
                        raw_line=raw_line,
                        shop_pk=args.shop_pk,
                        order_pk=order_pk,
                        raw_record_id=raw_record.id,
                        source_endpoint=_endpoint_kind(raw_record.endpoint),
                        source_captured_at=raw_record.captured_at,
                        source_order_version_at=raw_version_at,
                        parent_status=parent_status,
                        parent_currency=parent_currency,
                        spu_pk=line_row.spu_pk,
                    )
                    counts["duplicate_noop" if before else "inserted"] += 1
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
            if len(rows) < args.batch_size:
                break
    engine.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill TikTok line-price observations (dry-run by default)."
    )
    parser.add_argument("--shop-pk", type=int, required=True)
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only; this is the default mode."
    )
    parser.add_argument("--confirm", action="store_true", help="Required for writes.")
    parser.add_argument("--start-raw-id", type=int, default=0)
    parser.add_argument("--end-raw-id", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    if args.confirm and args.dry_run:
        parser.error("--confirm and --dry-run are mutually exclusive")
    if not args.confirm:
        args.dry_run = True
    from tts_erp_v2.api.deps import require_destructive_script_guard

    require_destructive_script_guard(
        script_name="oneoff_backfill_tiktok_line_prices",
        confirmation=args.confirm,
        dangerous=args.confirm,
    )
    counts = _run(args)
    print("[backfill] " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
