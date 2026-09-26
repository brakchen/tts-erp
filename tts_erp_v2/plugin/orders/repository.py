"""plugin.orders 数据访问层。

所有 upsert 用 ON CONFLICT DO UPDATE 实现幂等。
has_data_bulk 批量查业务表存在性。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.plugin import (
    ChromeAfterSale,
    ChromeAfterSaleItem,
    ChromeOrder,
    ChromeOrderDetail,
    ChromeOrderLine,
    ChromeOrderTimeline,
    ChromeSettlement,
    ChromeSettlementDetail,
    ChromeShipment,
    ChromeTrackingEvent,
)

log = logging.getLogger("tts_erp_v2.plugin.orders.repository")


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


# ── reconcile ───────────────────────────────────────────────────────


ORDER_RECONCILE_SORT_INFO = "6"
ORDER_RECONCILE_SORT_FIELD = "order_time"
ORDER_RECONCILE_SORT_DIRECTION = "desc"
ORDER_RECONCILE_TIE_BREAKER = "order_id"

# TikTok Seller Center main_order_status=104 is the observed CANCELLED state.
# Keep the raw value in orders; this constant only controls logistics
# candidate selection so cancelled orders are not queried for packages.
TIKTOK_CANCELLED_MAIN_ORDER_STATUS = 104

# 这是可迭代的业务规则，而不是 TikTok 状态码的完整字典。只有已经明确表示
# 包裹不会继续流转的状态才进入这里；未知状态保持 active，宁可多查一次。
_LOGISTICS_TERMINAL_TERMS = (
    "delivered",
    "signed",
    "received",
    "complete",
    "completed",
    "returned",
    "return to sender",
    "cancelled",
    "canceled",
    "refunded",
    "lost",
    "destroyed",
    "已签收",
    "签收",
    "已送达",
    "已完成",
    "已退回",
    "已取消",
    "退款",
    "丢失",
    "损坏",
    "破损",
)

_LOGISTICS_TERMINAL_CODES = frozenset({50101, 80101, 110101})


def reconcile_orders(
    sess: Session,
    *,
    shop_id: str,
    sort_info: str,
    anchor_positions: list[int],
    hot_window_size: int,
) -> dict[str, Any]:
    """返回订单服务端计数和确定性锚点。

    TikTok `sortInfo=6` 的服务端顺序由 order_time + order_id 对齐。存在历史
    记录缺少 order_time 时，不能把 offset 当成安全增量窗口，插件会自动回退
    到完整分页校验。
    """
    scope = ChromeOrder.shop_id == shop_id
    total = int(
        sess.execute(
            select(func.count(ChromeOrder.id)).where(scope)
        ).scalar_one()
    )
    missing_order_time = int(
        sess.execute(
            select(func.count(ChromeOrder.id)).where(
                scope,
                ChromeOrder.order_time.is_(None),
            )
        ).scalar_one()
    )
    order_by = (
        ChromeOrder.order_time.desc().nulls_last(),
        ChromeOrder.order_id.asc(),
    )

    anchors: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    for raw_position in anchor_positions:
        position = int(raw_position)
        if position < 0 or position in seen_positions or position >= total:
            continue
        seen_positions.add(position)
        order_id = sess.execute(
            select(ChromeOrder.order_id)
            .where(scope)
            .order_by(*order_by)
            .offset(position)
            .limit(1)
        ).scalar_one_or_none()
        if order_id is not None:
            anchors.append({"position": position, "orderId": order_id})

    offset_safe = sort_info == ORDER_RECONCILE_SORT_INFO and missing_order_time == 0
    return {
        "serverTotal": total,
        "anchors": anchors,
        "canIncremental": offset_safe,
        "offsetSafe": offset_safe,
        "ordering": {
            "field": ORDER_RECONCILE_SORT_FIELD,
            "direction": ORDER_RECONCILE_SORT_DIRECTION,
            "tieBreaker": ORDER_RECONCILE_TIE_BREAKER,
        },
        "hotWindowSize": max(0, int(hot_window_size)),
    }


def _terminal_reason(
    *,
    action_code: Any = None,
    status: Any = None,
    description: Any = None,
) -> str | None:
    try:
        code = int(action_code) if action_code is not None else None
    except (TypeError, ValueError):
        code = None
    if code in _LOGISTICS_TERMINAL_CODES:
        return f"action_code:{code}"

    # Compatibility for rows written before action_code was persisted. Match
    # the complete status only; substring matching misclassifies phrases such
    # as "not delivered", "未签收", and location text.
    for value in (status, description):
        if value is None:
            continue
        normalized = str(value).strip().casefold()
        if normalized and any(normalized == term.casefold() for term in _LOGISTICS_TERMINAL_TERMS):
            return str(value).strip()[:160]
    return None


def reconcile_logistics(
    sess: Session,
    *,
    shop_id: str,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    """按订单游标返回物流采集候选，并标记已知终态订单。

    订单表和包裹表取并集：订单刚写入但还没有包裹记录时仍然会被返回，
    避免后端已有包裹记录才会继续采集的闭环。游标是稳定排序后的 offset，
    仅用于一次 reconcile 分页，不作为业务数据的持久化状态。
    """
    order_ids = set(
        sess.execute(
            select(ChromeOrder.order_id)
            .where(ChromeOrder.shop_id == shop_id)
        )
        .scalars()
        .all()
    )
    shipment_order_ids = set(
        sess.execute(
            select(ChromeShipment.order_id)
            .where(ChromeShipment.shop_id == shop_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    cancelled_order_ids = set(
        sess.execute(
            select(ChromeOrder.order_id)
            .where(
                ChromeOrder.shop_id == shop_id,
                ChromeOrder.main_order_status == TIKTOK_CANCELLED_MAIN_ORDER_STATUS,
            )
        )
        .scalars()
        .all()
    )
    candidate_ids = sorted((order_ids | shipment_order_ids) - cancelled_order_ids)
    total = len(candidate_ids)

    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        offset = 0
    offset = min(offset, total)
    page_ids = candidate_ids[offset : offset + limit]

    shipments = (
        sess.execute(
            select(ChromeShipment)
            .where(
                ChromeShipment.shop_id == shop_id,
                ChromeShipment.order_id.in_(page_ids),
            )
            .order_by(ChromeShipment.order_id.asc(), ChromeShipment.package_id.asc())
        )
        .scalars()
        .all()
        if page_ids
        else []
    )
    shipments_by_order: dict[str, list[ChromeShipment]] = {}
    package_ids = []
    for shipment in shipments:
        shipments_by_order.setdefault(shipment.order_id, []).append(shipment)
        package_ids.append(shipment.package_id)

    events_by_package: dict[str, list[ChromeTrackingEvent]] = {}
    if package_ids:
        events = (
            sess.execute(
                select(ChromeTrackingEvent)
                .where(
                    ChromeTrackingEvent.shop_id == shop_id,
                    ChromeTrackingEvent.package_id.in_(package_ids),
                )
                .order_by(
                    ChromeTrackingEvent.package_id.asc(),
                    ChromeTrackingEvent.event_at.desc().nulls_last(),
                    ChromeTrackingEvent.id.desc(),
                )
            )
            .scalars()
            .all()
        )
        for event in events:
            events_by_package.setdefault(event.package_id, []).append(event)

    terminal_by_order: dict[str, tuple[bool, list[str], str | None]] = {}
    for order_id in page_ids:
        order_shipments = shipments_by_order.get(order_id, [])
        if not order_shipments:
            terminal_by_order[order_id] = (False, [], None)
            continue
        reasons: list[str] = []
        all_terminal = True
        for shipment in order_shipments:
            reason = _terminal_reason(status=shipment.status)
            if reason is None:
                for event in events_by_package.get(shipment.package_id, []):
                    reason = _terminal_reason(
                        action_code=event.action_code,
                        description=event.description,
                    )
                    if reason is not None:
                        break
            if reason is None:
                all_terminal = False
            else:
                reasons.append(reason)
        terminal_by_order[order_id] = (
            all_terminal,
            [s.package_id for s in order_shipments],
            reasons[0] if reasons else None,
        )

    items: list[dict[str, Any]] = []
    for order_id in page_ids:
        is_terminal, package_ids_for_order, reason = terminal_by_order[order_id]
        items.append(
            {
                "orderId": order_id,
                "packageIds": package_ids_for_order,
                "isTerminal": is_terminal,
                "terminalReason": reason,
                "nextCheckAt": None,
            }
        )

    next_offset = offset + len(page_ids)
    return {
        "complete": next_offset >= total,
        "items": items,
        "nextCursor": str(next_offset) if next_offset < total else None,
    }


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
        # 秒级（< 1e12）/ 毫秒级（1e12）/ 微秒级（1e15）：循环除 1000 归一到秒
        while value > 1e12:
            value = value / 1000
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            log.warning("out-of-range timestamp value: %r", value)
            return None
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
            while num > 1e12:
                num = num / 1000
            try:
                return datetime.fromtimestamp(num, tz=UTC)
            except (OverflowError, OSError, ValueError):
                log.warning("out-of-range timestamp string: %r", value)
                return None
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


def _to_decimal(value: Any, *, field: str = "") -> Decimal | None:
    """金额字符串/数字 → Decimal。None/缺失/空串 → None。

    解析失败（非空但非法）：log.warning 带字段名 + 原始值后返回 None——
    字段级失败不中断整批 dump，但必须在日志里可见（2026-09-14 review
    结论：静默 None 不可接受）。
    """
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
            log.warning("unparseable decimal %s: %r", field or "<unknown>", value)
            return None
    log.warning("unparseable decimal %s: %r (type %s)", field or "<unknown>", value, type(value).__name__)
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
    shop_id: str,
    package_id: str,
    event_key: str,
    action_code: int | None = None,
    event_at: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """UPSERT 物流轨迹事件。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeTrackingEvent)
        .values(
            shop_id=shop_id,
            package_id=package_id,
            event_key=event_key,
            action_code=action_code,
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
                "event_at": event_at,
                "action_code": action_code,
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


# ── record_dump_health ──────────────────────────────────────────────

_SQL_INSERT_DUMP_HEALTH = """
INSERT INTO plugin.plugin_logs (
    seller_id,
    advertiser_id,
    plugin_version,
    plugin_name,
    level,
    message,
    context,
    occurred_at
) VALUES (
    :seller_id,
    :advertiser_id,
    :plugin_version,
    :plugin_name,
    :level,
    :message,
    CAST(:context AS JSONB),
    :occurred_at
)
"""


def record_dump_health(
    sess: Session,
    *,
    shop_id: str,
    domain: str,
    endpoint: str,
    rows_written: int,
    parse_error_class: str | None,
    captured_at: datetime,
) -> None:
    """Write per-domain dump health metric to plugin.plugin_logs.

    Purpose: diagnose why certain domains (e.g., statements) have 0 rows.
    Called as part of the dump transaction (before sess.commit).
    """
    level = "info" if rows_written > 0 and not parse_error_class else "warn"
    context = {
        "domain": domain,
        "shop_id": shop_id,
        "endpoint": endpoint,
        "rows_written": rows_written,
        "parse_error_class": parse_error_class,
        "captured_at": captured_at.isoformat(),
        "server_received_at": datetime.now(UTC).isoformat(),
    }
    message = (
        f"dump_processed domain={domain} rows={rows_written}"
        + (f" parse_error={parse_error_class}" if parse_error_class else "")
    )
    sess.execute(
        text(_SQL_INSERT_DUMP_HEALTH),
        {
            "seller_id": shop_id,
            "advertiser_id": "",
            "plugin_version": "order-sync-v1",
            "plugin_name": "order-sync",
            "level": level,
            "message": message,
            "context": json.dumps(context, ensure_ascii=False),
            "occurred_at": captured_at,
        },
    )


# ── order_details upsert ──────────────────────────────────────────────


def upsert_order_detail(
    sess: Session,
    *,
    shop_id: str,
    order_id: str,
    fields: dict[str, Any],
) -> str:
    """UPSERT 订单全量详情（order/get）。返回 'inserted'。"""
    now = datetime.now(UTC)
    values = {"shop_id": shop_id, "order_id": order_id, "created_at": now, "updated_at": now}
    values.update(fields)
    stmt = (
        pg_insert(ChromeOrderDetail)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[ChromeOrderDetail.shop_id, ChromeOrderDetail.order_id],
            set_={k: v for k, v in fields.items()},
        )
    )
    stmt = stmt.values(updated_at=now)
    sess.execute(stmt)
    return "inserted"


# ── order_timeline upsert ─────────────────────────────────────────────


def upsert_order_timeline(
    sess: Session,
    *,
    shop_id: str,
    order_id: str,
    event_index: int,
    description: str | None = None,
    event_at: datetime | None = None,
    detail: str | None = None,
    raw_payload: dict | None = None,
) -> str:
    """UPSERT 订单时间线事件（order/history）。返回 'inserted'。"""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(ChromeOrderTimeline)
        .values(
            shop_id=shop_id,
            order_id=order_id,
            event_index=event_index,
            description=description,
            event_at=event_at,
            detail=detail,
            raw_payload=raw_payload,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[
                ChromeOrderTimeline.shop_id,
                ChromeOrderTimeline.order_id,
                ChromeOrderTimeline.event_index,
            ],
            set_={
                "description": description,
                "event_at": event_at,
                "detail": detail,
                "raw_payload": raw_payload,
                "updated_at": now,
            },
        )
    )
    sess.execute(stmt)
    return "inserted"
