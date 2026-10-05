"""Read seam for materialized alerts and the shared effective config."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    ALERT_CONFIG_KEY,
    SEED_FALLBACK_CONFIG,
    config_to_payload,
    validate_alert_config,
)
from tts_erp_v2.analytics.spu_deterioration_alert.materialize import decision_row, stale
from tts_erp_v2.analytics.spu_deterioration_alert.policy import (
    compare_windows,
    evaluate,
)
from tts_erp_v2.analytics.spu_profitability import _implementation
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.analytics.spu_profitability._types import ActivitySelection, SortField
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    RuntimeConfigItem,
    RuntimeConfigRevision,
    SpuDeteriorationAlert,
)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def effective_config_details(
    session: Session,
) -> tuple[dict[str, Any], str, int | None, str, Any | None, str | None]:
    item = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
    if item is None or item.published_version is None:
        payload = config_to_payload(SEED_FALLBACK_CONFIG)
        source = "seed_fallback"
        version = None
        updated_at = None
        updated_by = None
    else:
        revision = session.execute(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                RuntimeConfigRevision.version == item.published_version,
            )
        ).scalar_one_or_none()
        if revision is None:
            raise RuntimeError("published alert configuration is unavailable")
        try:
            validate_alert_config(revision.payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("published alert configuration is invalid") from exc
        payload = dict(revision.payload)
        source = "runtime_config"
        version = revision.version
        updated_at = revision.created_at
        updated_by = revision.created_by
    return (
        payload,
        source,
        version,
        sha256(_canonical_json(payload).encode()).hexdigest(),
        updated_at,
        updated_by,
    )


def effective_config(session: Session) -> tuple[dict[str, Any], str, int | None, str]:
    return effective_config_details(session)[:4]


_FACT_BATCH_SIZE = 500


def _window_items(
    session: Session,
    *,
    shop_pk: int,
    start: date,
    end: date,
    calculated_at: Any,
) -> dict[int, Any]:
    """Read one window in deterministic bounded batches."""
    items: dict[int, Any] = {}
    offset = 0
    while True:
        overview = _implementation._query_spu_roi(
            session,
            q=None,
            shop_pk=shop_pk,
            selection=ActivitySelection(),
            active_only=True,
            include_without_activity=False,
            sort_field=SortField.ROI_REAL.value,
            ascending=True,
            limit=_FACT_BATCH_SIZE,
            offset=offset,
            fee_rate=None,
            calculated_at=calculated_at,
            w_start=start,
            w_end=end,
        )
        batch = sorted(overview.items, key=lambda item: item.spu_pk)
        if not batch:
            break
        for item in batch:
            items[item.spu_pk] = item
        offset += len(batch)
        if len(batch) < _FACT_BATCH_SIZE:
            break
    return items


def build_materialized_rows(
    session: Session,
) -> dict[date, list[SpuDeteriorationAlert]]:
    """Read all active shop×SPU facts in one read-only repeatable-read snapshot."""
    with consistent_read_snapshot(session) as calculated_at:
        config_payload, source, version, config_hash, updated_at, updated_by = (
            effective_config_details(session)
        )
        if config_payload.get("enabled") is False:
            return {}
        config = validate_alert_config(
            config_payload,
            source=source,
            version=version,
            payload_hash=config_hash,
        )
        config = config.__class__(
            config.enabled,
            config.maturity_days,
            config.fast,
            config.confirmation,
            config.source,
            config.version,
            config.payload_hash,
            updated_at,
            updated_by,
        )
        shops = (
            session.execute(
                select(ChannelAccount).where(
                    ChannelAccount.platform == "tiktok",
                    or_(
                        ChannelAccount.status.is_(None),
                        ChannelAccount.status.notin_(("disabled", "deleted")),
                    ),
                )
            )
            .scalars()
            .all()
        )
        output: dict[date, list[SpuDeteriorationAlert]] = {}
        for shop in shops:
            timezone = _implementation._shop_reporting_timezone(
                session, shop_pk=shop.id
            )
            anchor = calculated_at.astimezone(timezone).date() - timedelta(days=2)
            products = (
                session.execute(
                    select(ChannelProduct.id)
                    .where(
                        ChannelProduct.shop_pk == shop.id,
                        or_(
                            ChannelProduct.status.is_(None),
                            ChannelProduct.status.notin_(("deleted", "DELETED")),
                        ),
                    )
                    .order_by(ChannelProduct.id)
                )
                .scalars()
                .all()
            )
            if not products:
                continue
            cache: dict[tuple[date, date], dict[int, Any]] = {}
            for pair in compare_windows(anchor).values():
                for start, end in (
                    (pair.current_start, pair.current_end),
                    (pair.previous_start, pair.previous_end),
                ):
                    if (start, end) not in cache:
                        cache[(start, end)] = _window_items(
                            session,
                            shop_pk=shop.id,
                            start=start,
                            end=end,
                            calculated_at=calculated_at,
                        )
            for pair in compare_windows(anchor, confirmation=True).values():
                for start, end in (
                    (pair.current_start, pair.current_end),
                    (pair.previous_start, pair.previous_end),
                ):
                    if (start, end) not in cache:
                        cache[(start, end)] = _window_items(
                            session,
                            shop_pk=shop.id,
                            start=start,
                            end=end,
                            calculated_at=calculated_at,
                        )
            for days in (1, 3, 7):
                for layer, windows in (
                    ("fast", compare_windows(anchor)),
                    ("confirmation", compare_windows(anchor, confirmation=True)),
                ):
                    pair = windows[days]
                    threshold = (
                        config.fast[days]
                        if layer == "fast"
                        else config.confirmation[days]
                    )
                    previous_items = cache.get(
                        (pair.previous_start, pair.previous_end), {}
                    )
                    current_items = cache.get(
                        (pair.current_start, pair.current_end), {}
                    )
                    for spu_pk in products:
                        previous = _item_metric(
                            previous_items.get(spu_pk),
                            pair.previous_start,
                            pair.previous_end,
                        )
                        current = _item_metric(
                            current_items.get(spu_pk),
                            pair.current_start,
                            pair.current_end,
                        )
                        decision = evaluate(
                            previous, current, threshold.warning, threshold.critical
                        )
                        output.setdefault(anchor, []).append(
                            decision_row(
                                decision=decision,
                                shop_pk=shop.id,
                                spu_pk=spu_pk,
                                anchor_date=anchor,
                                window_days=days,
                                layer=layer,
                                config=config,
                                config_hash=config_hash,
                                calculated_at=calculated_at,
                            )
                        )
        return output


def _item_metric(item: Any, start: date, end: date):
    from tts_erp_v2.analytics.spu_deterioration_alert.facts import WindowMetric

    if item is None:
        return WindowMetric(start, end, Decimal(0), 0, 0, None, None, False)
    return WindowMetric(
        start,
        end,
        item.spend,
        item.order_count,
        item.ad_orders,
        item.roi_real,
        item.net_profit,
        True,
    )


def read_alerts(
    session: Session,
    *,
    shop_pk: int | None = None,
    spu_pks: list[int] | None = None,
    window_days: list[int] | None = None,
    layer: str | None = None,
    severity: str | None = None,
    states: list[str] | None = None,
    sample: str = "all",
    anchor_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[
    list[SpuDeteriorationAlert],
    int,
    list[SpuDeteriorationAlert],
    tuple[dict[str, Any], str, int | None, str, Any | None, str | None],
]:
    """Validate the complete requested snapshot before presentation filters."""
    config = effective_config_details(session)
    payload = config[0]
    if payload["enabled"] is False:
        return [], 0, [], config
    if shop_pk is None:
        raise RuntimeError("shop_pk is required for alert snapshots")

    if anchor_date is None:
        anchor_date = session.execute(
            select(func.max(SpuDeteriorationAlert.anchor_date)).where(
                SpuDeteriorationAlert.shop_pk == shop_pk
            )
        ).scalar_one_or_none()
    if anchor_date is None:
        raise RuntimeError("materialized alert snapshot is unavailable")

    source, version, payload_hash = config[1:4]
    basis_rows = (
        session.execute(
            select(SpuDeteriorationAlert)
            .where(
                SpuDeteriorationAlert.shop_pk == shop_pk,
                SpuDeteriorationAlert.anchor_date == anchor_date,
            )
            .order_by(SpuDeteriorationAlert.spu_pk, SpuDeteriorationAlert.window_days)
        )
        .scalars()
        .all()
    )
    if not basis_rows:
        raise RuntimeError("materialized alert snapshot is unavailable")
    if stale(anchor_date):
        raise RuntimeError("materialized alert snapshot is stale")
    calculated_at_values = {row.basis_calculated_at for row in basis_rows}
    if len(calculated_at_values) != 1:
        raise RuntimeError("materialized alert calculation basis is incoherent")
    if any(
        row.effective_config_source != source
        or row.effective_config_version != version
        or row.config_payload_hash != payload_hash
        for row in basis_rows
    ):
        raise RuntimeError("materialized alert configuration basis is stale")

    statement = select(SpuDeteriorationAlert).where(
        SpuDeteriorationAlert.shop_pk == shop_pk,
        SpuDeteriorationAlert.anchor_date == anchor_date,
    )
    if spu_pks:
        statement = statement.where(SpuDeteriorationAlert.spu_pk.in_(spu_pks))
    if window_days:
        statement = statement.where(SpuDeteriorationAlert.window_days.in_(window_days))
    if layer is not None:
        statement = statement.where(SpuDeteriorationAlert.layer == layer)
    if severity is not None:
        statement = statement.where(SpuDeteriorationAlert.severity == severity)
    if states:
        statement = statement.where(SpuDeteriorationAlert.state.in_(states))
    if sample != "all":
        statement = statement.where(SpuDeteriorationAlert.sample_status == sample)
    filtered_rows = (
        session.execute(
            statement.order_by(
                SpuDeteriorationAlert.spu_pk,
                SpuDeteriorationAlert.window_days,
                SpuDeteriorationAlert.layer,
            )
        )
        .scalars()
        .all()
    )
    return (
        list(filtered_rows[offset : offset + limit]),
        len(basis_rows),
        list(basis_rows),
        config,
    )
