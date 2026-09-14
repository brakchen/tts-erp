"""plugin.orders 数据访问层。

所有 upsert 用 ON CONFLICT DO UPDATE 实现幂等。
has_data_bulk 批量查业务表存在性。
write_raw_log 写同步流水。
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.plugin import (
    ChromeAfterSale,
    ChromeAfterSaleItem,
    ChromeOrder,
    ChromeOrderLine,
    ChromeSettlement,
    ChromeSettlementDetail,
    ChromeShipment,
    ChromeTrackingEvent,
    RawLog,
)

log = logging.getLogger("tts_erp_v2.plugin.orders.repository")

# ── raw_log ─────────────────────────────────────────────────────────


def write_raw_log(
    sess: Session,
    *,
    domain: str,
    shop_id: str,
    endpoint: str,
    captured_at: datetime,
    request_params: dict | None,
    request_body: dict | None,
    response_body: dict | None,
    parse_error: str | None,
    rows_written: int,
) -> int:
    """写同步流水，返回 raw_log.id。"""
    log = RawLog(
        domain=domain,
        shop_id=shop_id,
        endpoint=endpoint,
        captured_at=captured_at,
        request_params=request_params,
        request_body=request_body,
        response_body=response_body,
        parse_error=parse_error,
        rows_written=rows_written,
    )
    sess.add(log)
    sess.flush()
    return log.id  # type: ignore[return-value]


# ── has_data_bulk ───────────────────────────────────────────────────


def has_data_bulk(
    sess: Session,
    *,
    domain: str,
    shop_id: str,
    ids: list[str],
    versions: dict[str, int | list[int]] | None = None,
) -> dict[str, bool]:
    """批量查业务表存在性。

    返回 {id: True/False}，True 表示已存在。
    - orders: 查 ChromeOrder.order_id
    - logistics: 查 ChromeShipment.order_id (distinct)
    - statements: 默认查 statement_id；提供 versions 时按 statement_id + version 查
    """
    if not ids:
        return {}

    ids_set = set(ids)

    if domain == "orders":
        rows = (
            sess.execute(
                select(ChromeOrder.order_id).where(
                    ChromeOrder.shop_id == shop_id,
                    ChromeOrder.order_id.in_(ids),
                )
            )
            .scalars()
            .all()
        )
    elif domain == "logistics":
        rows = (
            sess.execute(
                select(ChromeShipment.order_id.distinct()).where(
                    ChromeShipment.shop_id == shop_id,
                    ChromeShipment.order_id.in_(ids),
                )
            )
            .scalars()
            .all()
        )
    elif domain == "statements":
        if versions is None:
            rows = (
                sess.execute(
                    select(ChromeSettlement.statement_id).where(
                        ChromeSettlement.shop_id == shop_id,
                        ChromeSettlement.statement_id.in_(ids),
                    )
                )
                .scalars()
                .all()
            )
        else:
            requested: list[tuple[str, int]] = []
            for statement_id in ids:
                raw_versions = versions.get(statement_id)
                if raw_versions is None:
                    continue
                version_list = [raw_versions] if isinstance(raw_versions, int) else raw_versions
                requested.extend((statement_id, version) for version in set(version_list))
            unversioned = [
                statement_id for statement_id in ids if statement_id not in versions
            ]
            predicates = [
                and_(
                    ChromeSettlement.statement_id == statement_id,
                    ChromeSettlement.statement_version == version,
                )
                for statement_id, version in requested
            ]
            if unversioned:
                predicates.append(ChromeSettlement.statement_id.in_(unversioned))
            rows = sess.execute(
                select(
                    ChromeSettlement.statement_id, ChromeSettlement.statement_version
                ).where(
                    ChromeSettlement.shop_id == shop_id,
                    or_(*predicates) if predicates else False,
                )
            ).all()
            found_pairs = set(rows)
            return {
                id_: (
                    all(
                        (id_, version) in found_pairs
                        for version in (
                            [versions[id_]]
                            if isinstance(versions[id_], int)
                            else set(versions[id_])
                        )
                    )
                    if id_ in versions
                    else any(row_id == id_ for row_id, _ in found_pairs)
                )
                for id_ in ids_set
            }
    else:
        return dict.fromkeys(ids, False)

    found = set(rows)
    return {id_: (id_ in found) for id_ in ids_set}


# ── synced_ids ──────────────────────────────────────────────────────


def list_synced_ids(
    sess: Session,
    *,
    domain: str,
    shop_id: str,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[str], int]:
    """查询已同步 id 列表。返回 (ids, total)。"""
    if domain == "orders":
        base = select(ChromeOrder.order_id).where(ChromeOrder.shop_id == shop_id)
        count_q = (
            select(text("count(*)"))
            .select_from(ChromeOrder)
            .where(ChromeOrder.shop_id == shop_id)
        )
    elif domain == "logistics":
        base = select(ChromeShipment.order_id.distinct()).where(
            ChromeShipment.shop_id == shop_id
        )
        # distinct count
        count_q = select(text("count(*)")).select_from(
            select(ChromeShipment.order_id.distinct())
            .where(ChromeShipment.shop_id == shop_id)
            .subquery()
        )
    elif domain == "statements":
        base = select(ChromeSettlement.statement_id).where(
            ChromeSettlement.shop_id == shop_id
        )
        count_q = (
            select(text("count(*)"))
            .select_from(ChromeSettlement)
            .where(ChromeSettlement.shop_id == shop_id)
        )
    else:
        return [], 0

    total = sess.execute(count_q).scalar() or 0
    rows = sess.execute(base.offset(offset).limit(limit)).scalars().all()
    return list(rows), total


# ── 时间戳转换 helpers ──────────────────────────────────────────────


def _ts_to_datetime(value: Any) -> datetime | None:
    """TikTok 时间戳 → datetime(UTC)。0 / None / 空串 → None。

    支持的形态（按 prod raw_log 实测）：
    - int/float 秒级（< 1e12）或毫秒级（>= 1e12）
    - 数字字符串（Seller Center order/list 实测形态：create_time 秒级、
      update_time 毫秒级）——2026-09-14 前 str 分支只认 ISO，数字字符串
      被静默吞成 None，导致 plugin.orders.order_time 全 NULL
    - ISO 字符串
    无法解析的非空值：log.warning 后返回 None（字段级失败不中断整批 dump，
    批量信号由 parse_error / NULL 率对账承担）。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value == 0:
            return None
        # 秒级时间戳（< 10^12）；毫秒级（>= 10^12）需除以 1000
        if value > 1e12:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        value = value.strip()
        if value in ("", "0"):
            return None
        # 数字字符串（秒/毫秒），与 int 分支同一启发式
        try:
            num = float(value)
        except ValueError:
            num = None
        if num is not None:
            if num == 0:
                return None
            if num > 1e12:
                num = num / 1000
            return datetime.fromtimestamp(num, tz=UTC)
        # ISO 字符串
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            log.warning("unparseable timestamp string: %r", value)
            return None
    return None


def _to_decimal(value: Any) -> Decimal | None:
    """金额字符串/数字 → Decimal。None/缺失 → None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        if value == "":
            return None
        try:
            return Decimal(value)
        except Exception:  # noqa: BLE001 — Decimal 可抛多种异常
            return None
    return None


def _parse_bill_period(bill_period: str | None) -> tuple[date | None, date | None]:
    """从 bill_period 解析 period_start / period_end。

    格式: "2026-09-01~2026-09-07"
    """
    if not bill_period:
        return None, None
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s*[~\-]\s*(\d{4}-\d{2}-\d{2})", bill_period)
    if not m:
        return None, None
    try:
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    except (ValueError, TypeError):
        return None, None


_PAYMENT_STATUS_MAP = {
    1: "PENDING",
    2: "PAID",
    3: "FAILED",
}


def _payment_status_to_text(value: Any) -> str | None:
    """payment_status int → TEXT 映射。"""
    if value is None:
        return None
    if isinstance(value, int):
        return _PAYMENT_STATUS_MAP.get(value, str(value))
    return str(value)


# ── orders upsert ───────────────────────────────────────────────────


def upsert_order(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    order_id: str,
    main_order_status: int | None = None,
    sku_display_status: int | None = None,
    currency: str | None = None,
    payment_amount: Decimal | None = None,
    total_amount: Decimal | None = None,
    fulfillment_type: int | None = None,
    pay_method: str | None = None,
    sale_region: str | None = None,
    order_time: datetime | None = None,
    update_time: datetime | None = None,
    latest_rts_time: datetime | None = None,
    latest_tts_time: datetime | None = None,
    buyer_nickname: str | None = None,
) -> str:
    """UPSERT 订单头。返回 'inserted' 或 'updated'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeOrder)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            order_id=order_id,
            main_order_status=main_order_status,
            sku_display_status=sku_display_status,
            currency=currency,
            payment_amount=payment_amount,
            total_amount=total_amount,
            fulfillment_type=fulfillment_type,
            pay_method=pay_method,
            sale_region=sale_region,
            order_time=order_time,
            update_time=update_time,
            latest_rts_time=latest_rts_time,
            latest_tts_time=latest_tts_time,
            buyer_nickname=buyer_nickname,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[ChromeOrder.shop_id, ChromeOrder.order_id],
            set_={
                "log_id": log_id,
                "main_order_status": main_order_status,
                "sku_display_status": sku_display_status,
                "currency": currency,
                "payment_amount": payment_amount,
                "total_amount": total_amount,
                "fulfillment_type": fulfillment_type,
                "pay_method": pay_method,
                "sale_region": sale_region,
                "order_time": order_time,
                "update_time": update_time,
                "latest_rts_time": latest_rts_time,
                "latest_tts_time": latest_tts_time,
                "buyer_nickname": buyer_nickname,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── order_lines upsert ──────────────────────────────────────────────


def upsert_order_line(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    order_id: str,
    sku_id: str,
    product_id: str | None = None,
    product_name: str | None = None,
    variant_name: str | None = None,
    image_url: str | None = None,
    quantity: Decimal | None = None,
    unit_price: Decimal | None = None,
    total_price: Decimal | None = None,
    currency: str | None = None,
    main_order_status: int | None = None,
    sku_display_status: int | None = None,
) -> str:
    """UPSERT 订单行。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeOrderLine)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            order_id=order_id,
            sku_id=sku_id,
            product_id=product_id,
            product_name=product_name,
            variant_name=variant_name,
            image_url=image_url,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            currency=currency,
            main_order_status=main_order_status,
            sku_display_status=sku_display_status,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeOrderLine.shop_id,
                ChromeOrderLine.order_id,
                ChromeOrderLine.sku_id,
            ],
            set_={
                "log_id": log_id,
                "product_id": product_id,
                "product_name": product_name,
                "variant_name": variant_name,
                "image_url": image_url,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_price": total_price,
                "currency": currency,
                "main_order_status": main_order_status,
                "sku_display_status": sku_display_status,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── shipments upsert ────────────────────────────────────────────────


def upsert_shipment(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    order_id: str,
    package_id: str,
    tracking_number: str | None = None,
    carrier_name: str | None = None,
    status: str | None = None,
    shipped_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> str:
    """UPSERT 物流包裹。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeShipment)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            order_id=order_id,
            package_id=package_id,
            tracking_number=tracking_number,
            carrier_name=carrier_name,
            status=status,
            shipped_at=shipped_at,
            delivered_at=delivered_at,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[ChromeShipment.shop_id, ChromeShipment.package_id],
            set_={
                "log_id": log_id,
                "order_id": order_id,
                "tracking_number": tracking_number,
                "carrier_name": carrier_name,
                "status": status,
                "shipped_at": shipped_at,
                "delivered_at": delivered_at,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── tracking_events upsert ──────────────────────────────────────────


def upsert_tracking_event(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    package_id: str,
    event_key: str,
    event_at: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """UPSERT 物流轨迹事件。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeTrackingEvent)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            package_id=package_id,
            event_key=event_key,
            event_at=event_at,
            description=description,
            location=location,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeTrackingEvent.shop_id,
                ChromeTrackingEvent.package_id,
                ChromeTrackingEvent.event_key,
            ],
            set_={
                "log_id": log_id,
                "event_at": event_at,
                "description": description,
                "location": location,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── settlements upsert ──────────────────────────────────────────────


def upsert_settlement(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    statement_id: str,
    statement_version: int = 0,
    bill_period: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    settlement_time: datetime | None = None,
    settlement_id: str | None = None,
    payment_id: str | None = None,
    payment_status: str | None = None,
    statement_type: int | None = None,
    payment_pending_reason: int | None = None,
    settle_amount: Decimal | None = None,
    earning_amount: Decimal | None = None,
    fee_amount: Decimal | None = None,
    adjust_amount: Decimal | None = None,
    payable_amount: Decimal | None = None,
    shipping_amount: Decimal | None = None,
    total_reserve_amount: Decimal | None = None,
    currency: str | None = None,
) -> str:
    """UPSERT 结算单头。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeSettlement)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            statement_id=statement_id,
            statement_version=statement_version,
            bill_period=bill_period,
            period_start=period_start,
            period_end=period_end,
            settlement_time=settlement_time,
            settlement_id=settlement_id,
            payment_id=payment_id,
            payment_status=payment_status,
            statement_type=statement_type,
            payment_pending_reason=payment_pending_reason,
            settle_amount=settle_amount,
            earning_amount=earning_amount,
            fee_amount=fee_amount,
            adjust_amount=adjust_amount,
            payable_amount=payable_amount,
            shipping_amount=shipping_amount,
            total_reserve_amount=total_reserve_amount,
            currency=currency,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeSettlement.shop_id,
                ChromeSettlement.statement_id,
                ChromeSettlement.statement_version,
            ],
            set_={
                "log_id": log_id,
                "bill_period": bill_period,
                "period_start": period_start,
                "period_end": period_end,
                "settlement_time": settlement_time,
                "settlement_id": settlement_id,
                "payment_id": payment_id,
                "payment_status": payment_status,
                "statement_type": statement_type,
                "payment_pending_reason": payment_pending_reason,
                "settle_amount": settle_amount,
                "earning_amount": earning_amount,
                "fee_amount": fee_amount,
                "adjust_amount": adjust_amount,
                "payable_amount": payable_amount,
                "shipping_amount": shipping_amount,
                "total_reserve_amount": total_reserve_amount,
                "currency": currency,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── settlement_details upsert ───────────────────────────────────────


def upsert_settlement_detail(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    statement_id: str,
    statement_version: int = 0,
    sku_detail_id: str,
    trade_order_id: str | None = None,
    sku_id: str | None = None,
    product_name: str | None = None,
    sku_name: str | None = None,
    quantity: Decimal | None = None,
    settlement_status: str | None = None,
    placed_time: datetime | None = None,
    settlement_amount: Decimal | None = None,
    earning_amount: Decimal | None = None,
    fees_amount: Decimal | None = None,
    currency: str | None = None,
    fee_components: list[dict] | None = None,
    seller_web_cut_flow: bool | None = None,
    seller_app_cut_flow: bool | None = None,
) -> str:
    """UPSERT SKU 级结算明细。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeSettlementDetail)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            statement_id=statement_id,
            statement_version=statement_version,
            sku_detail_id=sku_detail_id,
            trade_order_id=trade_order_id,
            sku_id=sku_id,
            product_name=product_name,
            sku_name=sku_name,
            quantity=quantity,
            settlement_status=settlement_status,
            placed_time=placed_time,
            settlement_amount=settlement_amount,
            earning_amount=earning_amount,
            fees_amount=fees_amount,
            currency=currency,
            fee_components=fee_components,
            seller_web_cut_flow=seller_web_cut_flow,
            seller_app_cut_flow=seller_app_cut_flow,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeSettlementDetail.shop_id,
                ChromeSettlementDetail.sku_detail_id,
            ],
            set_={
                "log_id": log_id,
                "statement_id": statement_id,
                "statement_version": statement_version,
                "trade_order_id": trade_order_id,
                "sku_id": sku_id,
                "product_name": product_name,
                "sku_name": sku_name,
                "quantity": quantity,
                "settlement_status": settlement_status,
                "placed_time": placed_time,
                "settlement_amount": settlement_amount,
                "earning_amount": earning_amount,
                "fees_amount": fees_amount,
                "currency": currency,
                "fee_components": fee_components,
                "seller_web_cut_flow": seller_web_cut_flow,
                "seller_app_cut_flow": seller_app_cut_flow,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


# ── after_sales (售后/退款) ──────────────────────────────────
def upsert_after_sale(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    cancel_id: str,
    cancel_type: str,
    cancel_status: str,
    main_order_id: str | None,
    reason: str | None,
    request_time: datetime | None,
    complete_time: datetime | None,
    raw_payload: dict | None,
) -> str:
    """upsert plugin.after_sales。幂等键 (shop_id, cancel_id)。

    返回 "inserted" 或 "updated" 供 caller 计入 rows_written。
    """
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeAfterSale)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            cancel_id=cancel_id,
            cancel_type=cancel_type,
            cancel_status=cancel_status,
            main_order_id=main_order_id,
            reason=reason,
            request_time=request_time,
            complete_time=complete_time,
            raw_payload=raw_payload,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeAfterSale.shop_id,
                ChromeAfterSale.cancel_id,
            ],
            set_={
                "log_id": log_id,
                "cancel_type": cancel_type,
                "cancel_status": cancel_status,
                "main_order_id": main_order_id,
                "reason": reason,
                "request_time": request_time,
                "complete_time": complete_time,
                "raw_payload": raw_payload,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"


def upsert_after_sale_item(
    sess: Session,
    *,
    log_id: int,
    shop_id: str,
    cancel_id: str,
    line_item_id: str,
    order_line_item_id: str | None,
    sku_id: str | None,
    product_id: str | None,
    quantity: Decimal | None,
    refund_amount: Decimal | None,
    currency: str | None,
    raw_payload: dict | None,
) -> str:
    """upsert plugin.after_sale_items。幂等键 (shop_id, line_item_id)。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeAfterSaleItem)
        .values(
            log_id=log_id,
            shop_id=shop_id,
            cancel_id=cancel_id,
            line_item_id=line_item_id,
            order_line_item_id=order_line_item_id,
            sku_id=sku_id,
            product_id=product_id,
            quantity=quantity,
            refund_amount=refund_amount,
            currency=currency,
            raw_payload=raw_payload,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeAfterSaleItem.shop_id,
                ChromeAfterSaleItem.line_item_id,
            ],
            set_={
                "log_id": log_id,
                "cancel_id": cancel_id,
                "order_line_item_id": order_line_item_id,
                "sku_id": sku_id,
                "product_id": product_id,
                "quantity": quantity,
                "refund_amount": refund_amount,
                "currency": currency,
                "raw_payload": raw_payload,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"
