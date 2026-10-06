from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import Text

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    SEED_FALLBACK_CONFIG,
    config_to_payload,
    validate_alert_config,
)
from tts_erp_v2.analytics.spu_deterioration_alert.facts import WindowMetric
from tts_erp_v2.analytics.spu_deterioration_alert.policy import (
    AlertSeverity,
    AlertState,
    SampleStatus,
    confirmation_windows,
    evaluate,
    fast_windows,
    paired_recovery,
)
from tts_erp_v2.analytics.spu_deterioration_alert.read import _window_items
from tts_erp_v2.db.models import SpuDeteriorationAlert

pytestmark = pytest.mark.layer_unit


def metric(
    roi: str | None,
    profit: str | None,
    spend: str = "100",
    order_count: int = 5,
    ad_orders: int = 1,
) -> WindowMetric:
    return WindowMetric(
        date(2026, 1, 1),
        date(2026, 1, 1),
        Decimal(spend),
        order_count,
        ad_orders,
        None if roi is None else Decimal(roi),
        None if profit is None else Decimal(profit),
    )


def test_exact_non_overlapping_windows_and_confirmation_shift() -> None:
    anchor = date(2026, 10, 10)
    fast = fast_windows(anchor)[3]
    confirmation = confirmation_windows(anchor)[3]
    assert (fast.current_start, fast.current_end) == (date(2026, 10, 8), anchor)
    assert (fast.previous_start, fast.previous_end) == (
        date(2026, 10, 5),
        date(2026, 10, 7),
    )
    assert (confirmation.current_start, confirmation.current_end) == (
        date(2026, 10, 1),
        date(2026, 10, 3),
    )
    assert confirmation.previous_end == date(2026, 9, 30)


def test_prior_non_positive_uses_transition_state_without_percentage() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    decision = evaluate(
        metric("-0.2", "-20"), metric("-0.4", "-30"), layer.warning, layer.critical
    )
    assert decision.roi_decline is None
    assert decision.state is AlertState.LOSS_EXPANDING
    assert decision.sample_status is SampleStatus.SUFFICIENT


def test_missing_or_zero_spend_is_never_stable() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    missing = evaluate(
        metric(None, "0"), metric(None, "0"), layer.warning, layer.critical
    )
    assert missing.sample_status is SampleStatus.SAMPLE_INSUFFICIENT
    unavailable = evaluate(
        metric("1", "10"),
        metric("1", "10", spend="0"),
        layer.warning,
        layer.critical,
    )
    assert unavailable.sample_status is SampleStatus.SAMPLE_INSUFFICIENT


def test_warning_gates_make_rows_insufficient_before_classification() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    warning = replace(layer.warning, min_ad_orders=1)
    cases = (
        metric("0", "100", spend="99.99"),
        metric("0", "100", order_count=2),
        metric("0", "100", ad_orders=0),
    )
    for current in cases:
        decision = evaluate(metric(".1", "100"), current, warning, layer.critical)
        assert decision.sample_status is SampleStatus.SAMPLE_INSUFFICIENT
        assert decision.state is AlertState.SAMPLE_INSUFFICIENT
        assert decision.severity is AlertSeverity.NONE

    boundary = evaluate(
        metric(".1", "100"),
        metric("0", "100", spend="100", order_count=3, ad_orders=1),
        warning,
        layer.critical,
    )
    assert boundary.sample_status is SampleStatus.SUFFICIENT
    assert boundary.state is AlertState.PROFIT_TO_LOSS
    assert boundary.severity is AlertSeverity.WARNING


def test_transition_warning_uses_gates_without_net_profit_decline() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    decision = evaluate(
        metric(".1", "100"), metric("0", "100"), layer.warning, layer.critical
    )
    assert decision.state is AlertState.PROFIT_TO_LOSS
    assert decision.severity is AlertSeverity.WARNING


def test_inclusive_warning_and_critical_boundaries() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    warning = evaluate(
        metric("1", "100"), metric(".8", "75"), layer.warning, layer.critical
    )
    critical = evaluate(
        metric("1", "100"),
        metric(".6", "60", spend="300"),
        layer.warning,
        layer.critical,
    )
    assert warning.severity is AlertSeverity.WARNING
    assert critical.severity is AlertSeverity.CRITICAL


def test_alert_schema_text_columns_match_migration() -> None:
    columns = SpuDeteriorationAlert.__table__.c
    for name in (
        "layer",
        "severity",
        "state",
        "sample_status",
        "effective_config_source",
        "effective_config_updated_by",
        "config_payload_hash",
    ):
        assert isinstance(columns[name].type, Text)
    migration = (
        Path(__file__).parents[2] / "alembic/versions/0053_spu_deterioration_alert.py"
    )
    source = migration.read_text(encoding="utf-8")
    for field in (
        "layer TEXT",
        "severity TEXT",
        "state TEXT",
        "sample_status TEXT",
        "effective_config_source TEXT",
        "effective_config_updated_by TEXT",
        "config_payload_hash TEXT",
    ):
        assert field in source


def test_window_items_batches_full_scope_in_stable_order(monkeypatch) -> None:
    calls: list[int] = []

    def fake_query(_session, **kwargs):
        selected = kwargs["selection"].spu_ids
        calls.append(len(selected))
        rows = [
            SimpleNamespace(spu_pk=int(spu_id.split("_")[1])) for spu_id in selected
        ]
        return SimpleNamespace(items=rows)

    monkeypatch.setattr(
        "tts_erp_v2.analytics.spu_deterioration_alert.read._implementation._query_spu_roi",
        fake_query,
    )
    result = _window_items(
        cast(Session, object()),
        shop_pk=1,
        selected_spu_ids=tuple(f"TEST_{index}" for index in range(501)),
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
        calculated_at=None,
    )
    assert len(result) == 501
    assert list(result) == list(range(501))
    assert calls == [100, 100, 100, 100, 100, 1]


def test_malformed_config_shapes_fail_closed_before_set_operations() -> None:
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
    for malformed in (
        [],
        {**payload, "fast": []},
        {**payload, "fast": {"1": []}},
        {**payload, "fast": {"1": {"warning": []}}},
    ):
        with pytest.raises((TypeError, ValueError)):
            validate_alert_config(malformed)  # type: ignore[arg-type]


def test_paired_recovery_keeps_all_fast_alerts_in_denominator() -> None:
    layer = SEED_FALLBACK_CONFIG.fast[1]
    alert = evaluate(
        metric("1", "100"), metric(".7", "70"), layer.warning, layer.critical
    )
    recovery = evaluate(
        metric("-0.2", "-20"), metric(".1", "5"), layer.warning, layer.critical
    )
    result = paired_recovery([("a", alert), ("b", alert)], {"a": recovery})
    assert result == {"numerator": 1, "denominator": 2, "rate": Decimal("0.5")}


# ── 枚举漂移守卫：state 筛选在领域/模板/文档三处各有一份字面量 ──
# 页面的 state 下拉读 DOM option（模板里只维护一份），但领域枚举
# ``AlertState`` 与 docs/api/external-api.md 必须与之完全一致，否则运维按文档
# 挑一个值会得到 422，而页面也会把合法值当非法处理。
_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "tts_erp_v2" / "templates" / "pages" / "spu-profit-deterioration.html"
_EXTERNAL_API_DOC = _ROOT / "docs" / "api" / "external-api.md"
_DESIGN_DOC = _ROOT / "docs" / "design" / "spu-profit-deterioration-alert.md"
_ALLOWED_STATES = ["all", *(state.value for state in AlertState)]


def _template_state_options() -> list[str]:
    body = _TEMPLATE.read_text(encoding="utf-8")
    block = body[body.index('id="filter-state"'): body.index('id="filter-severity"')]
    return re.findall(r'<option value="([a-z_]+)"', block)


def _doc_bullet(prefix: str, doc: Path = _EXTERNAL_API_DOC) -> str:
    """取一个 ``- `` 开头的 bullet 全文（含缩进续行，否则会漏掉被折行的枚举值）。"""
    lines = doc.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith(prefix)
    )
    collected = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith((" ", "\t")):
            break
        collected.append(line)
    return " ".join(collected)


def test_page_state_dropdown_matches_the_domain_enum() -> None:
    assert _template_state_options() == _ALLOWED_STATES


def test_documented_state_enum_matches_the_domain_enum() -> None:
    # 去掉 ``- `state`：`` 前缀，剩下的是纯枚举列表。
    documented = re.findall(r"`([a-z_]+)`", _doc_bullet("- `state`：").split("：", 1)[1])
    assert documented == [state.value for state in AlertState]
    # ``all`` 是页面侧的选择性哨兵，不是服务端枚举值，不得被写进文档枚举。
    assert "all" not in documented


def test_documented_warning_codes_are_all_reachable_from_the_api() -> None:
    """文档枚举的 warningCode 必须都有代码路径能产出（不可达值不得进文档）。

    external-api.md 与设计文档都枚举了 warningCode，两处都要被同一份可达集合
    约束；只守一份会让另一份悄悄漂移。
    """
    from tts_erp_v2.api.v2.spu_deterioration_alert import _row

    def alert_row(state: str) -> Any:
        return SpuDeteriorationAlert(
            shop_pk=7,
            spu_pk=1001,
            anchor_date=date(2026, 10, 3),
            window_days=3,
            layer="fast",
            severity="warning",
            state=state,
            sample_status="sufficient",
            prior_roi=Decimal(1),
            current_roi=Decimal("0.7"),
            roi_decline=Decimal("0.3"),
            prior_net_profit_cny=Decimal(100),
            current_net_profit_cny=Decimal(70),
            net_profit_decline=Decimal("0.3"),
            previous_spend_cny=Decimal(100),
            current_spend_cny=Decimal(100),
            previous_order_count=5,
            current_order_count=5,
            previous_ad_orders=1,
            current_ad_orders=1,
            effective_config_source="runtime_config",
            effective_config_version=3,
            config_payload_hash="0" * 64,
            basis_calculated_at=datetime(2026, 10, 3, 2, 0, tzinfo=UTC),
        )

    emitted = {_row(alert_row(state.value))["warningCode"] for state in AlertState}
    # 只取枚举段（第一个句号之前），排除后半句的散文。
    documented_by_doc = {
        doc: set(re.findall(r"`([A-Z_]+)`", _doc_bullet("- `warningCode`：", doc).split("。", 1)[0]))
        for doc in (_EXTERNAL_API_DOC, _DESIGN_DOC)
    }
    for doc, documented in documented_by_doc.items():
        assert documented == emitted, (doc, documented, emitted)
    # 逐份文档再点名一次不可达值：external-api.md 与设计文档任一处出现
    # API 无法产出的 code（例如历史草稿值 CONFIG_FALLBACK）都必须失败。
    for doc, documented in documented_by_doc.items():
        assert not documented - emitted, (doc, documented - emitted)
