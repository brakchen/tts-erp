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


def test_sample_gates_cover_both_windows_not_just_current() -> None:
    """样本门槛必须同时看两周：只有 current 达标的对比不可信。

    修复前 ``evaluate()`` 的 sample gates 只读 ``current``，于是 previous 窗口
    掉到门槛以下（1 单 / 53 元 vs 门槛 3 单 / 100 元）时，样本仍被判为
    ``sufficient``，并以单薄基线算出百分比跌幅。
    """
    layer = SEED_FALLBACK_CONFIG.fast[7]
    warning = replace(layer.warning, min_ad_orders=1)
    # (previous, current) —— 每一行都是 previous 单侧不达门槛、current 达门槛。
    one_sided = (
        (metric("1", "20", spend="99.99", order_count=3, ad_orders=1),
         metric("0", "20", spend="100", order_count=3, ad_orders=1)),
        (metric("1", "20", spend="100", order_count=2, ad_orders=1),
         metric("0", "20", spend="100", order_count=3, ad_orders=1)),
        (metric("1", "20", spend="100", order_count=3, ad_orders=0),
         metric("0", "20", spend="100", order_count=3, ad_orders=1)),
    )
    for previous, current in one_sided:
        decision = evaluate(previous, current, warning, layer.critical)
        assert decision.sample_status is SampleStatus.SAMPLE_INSUFFICIENT, (
            previous.spend_cny,
            previous.order_count,
            previous.ad_orders,
        )
        assert decision.state is AlertState.SAMPLE_INSUFFICIENT
        assert decision.severity is AlertSeverity.NONE


def test_two_window_sample_gates_keep_fully_covered_windows_sufficient() -> None:
    """两周都达门槛时判定不变：min 口径不得把本来可信的对比改坏。"""
    layer = SEED_FALLBACK_CONFIG.fast[7]
    decision = evaluate(
        metric("1.949", "200", spend="344", order_count=4, ad_orders=1),
        metric("1.167", "120", spend="344", order_count=4, ad_orders=1),
        layer.warning,
        layer.critical,
    )
    assert decision.sample_status is SampleStatus.SUFFICIENT
    assert decision.state is AlertState.ROI_DETERIORATION
    assert decision.severity is AlertSeverity.WARNING


def test_warning_and_critical_gates_share_the_two_window_measure() -> None:
    """两处 gates 同口径：min(previous, current)，两边谁弱听谁的。

    构造一周达 warning 门槛、另一周不达 critical 门槛的行：修复前两处都只看
    current，``min_spend`` / ``min_orders`` 不对称时判成 critical；修复后
    降为 warning。把两周对调后结论必须对称，证明取的是 min 而非 max。
    """
    layer = SEED_FALLBACK_CONFIG.fast[7]
    # 300 元 / 5 单：同时达 warning 门槛(100/3)与 critical 门槛(300/5)。
    strong_previous = metric("1", "100", spend="300", order_count=5, ad_orders=1)
    strong_current = metric(".4", "40", spend="300", order_count=5, ad_orders=1)
    # 200 元 / 5 单：达 warning 门槛，但不达 critical 的 300 元。
    weak_previous = metric("1", "100", spend="200", order_count=5, ad_orders=1)
    weak_current = metric(".4", "40", spend="200", order_count=5, ad_orders=1)
    assert strong_current.spend_cny >= layer.critical.min_spend_cny
    assert weak_previous.spend_cny < layer.critical.min_spend_cny
    # 弱周放在 previous 或放在 current，结论都必须降到 warning —— 取的是 min。
    for previous, current in (
        (weak_previous, strong_current),
        (strong_previous, weak_current),
    ):
        decision = evaluate(previous, current, layer.warning, layer.critical)
        assert decision.sample_status is SampleStatus.SUFFICIENT
        assert decision.state is AlertState.ROI_DETERIORATION
        assert decision.severity is AlertSeverity.WARNING, (
            previous.spend_cny,
            current.spend_cny,
        )

    # 订单数同理：previous 单数不达 critical.min_orders=5 时也不得判 critical。
    few_orders = metric("1", "100", spend="300", order_count=4, ad_orders=1)
    decision = evaluate(few_orders, strong_current, layer.warning, layer.critical)
    assert decision.sample_status is SampleStatus.SUFFICIENT
    assert decision.state is AlertState.ROI_DETERIORATION
    assert decision.severity is AlertSeverity.WARNING


def test_thin_previous_window_is_no_longer_an_alert() -> None:
    """生产回归样本 spu=54258 / 7d / confirmation / warning。

    上周 1 单、广告 53 元（门槛 3 单 / 100 元），本周 4 单、344 元；ROI
    1.949 → 1.167 被报成 “跌 40.1%”。修复前样本门控只看 current，于是这行
    以单薄基线判成 warning。净利润不是该行的报告字段，这里取 20 → 8 以复现
    生产上 “确实是 warning、但因 min_orders=4 < critical 5 没过 critical” 的结果。
    """
    layer = SEED_FALLBACK_CONFIG.confirmation[7]
    previous = metric("1.949", "20", spend="53", order_count=1, ad_orders=0)
    current = metric("1.167", "8", spend="344", order_count=4, ad_orders=1)
    decision = evaluate(previous, current, layer.warning, layer.critical)
    assert decision.roi_decline == pytest.approx(Decimal("0.401"), abs=Decimal("0.001"))
    assert decision.sample_status is SampleStatus.SAMPLE_INSUFFICIENT
    assert decision.state is AlertState.SAMPLE_INSUFFICIENT
    assert decision.severity is AlertSeverity.NONE


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
    # 两窗都需达到 critical 的 min_spend=300 才能判 critical：门槛取两窗 min，
    # 只给 current 300 元而 previous 仍 100 元的行修复前才被判成 critical。
    critical = evaluate(
        metric("1", "100", spend="300"),
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
        Path(__file__).parents[2] / "alembic/versions/0068_spu_deterioration_alert.py"
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


def test_recovery_shaped_windows_always_classify_as_loss_to_profit() -> None:
    """B-04 回归：扭亏为盈只有一个状态，枚举里没有 ROI/净利润两个“回升”值。

    修复前 ``_state()`` 尾部有两组与前置判定字面完全相同的分支（恒被
    ``LOSS_TO_PROFIT`` 抢先 return），于是 ``roi_recovery`` / ``recovery``
    永远不可达，却仍在枚举、API allow-list、页面下拉和文档里各占一个值。
    这个测试遍历 ROI 与净利润两种转正路径以及它们与 sample 门控的组合，
    把 “任何转正形状的输入都只产出 loss_to_profit” 钉成可执行契约。
    """
    assert [state.value for state in AlertState] == [
        "profit_to_loss",
        "loss_expanding",
        "loss_to_profit",
        "roi_deterioration",
        "net_profit_deterioration",
        "stable",
        "sample_insufficient",
        "unavailable",
    ]
    layer = SEED_FALLBACK_CONFIG.fast[1]
    # (previous roi, previous profit, current roi, current profit, orders, expected state)
    recovery_shapes = [
        # ROI 与净利润同时转正
        ("-0.2", "-20", ".1", "5", 5, AlertState.LOSS_TO_PROFIT),
        # 只有 ROI 转正
        ("-0.2", "50", ".1", "5", 5, AlertState.LOSS_TO_PROFIT),
        # 只有净利润转正
        ("1", "-20", "2", "5", 5, AlertState.LOSS_TO_PROFIT),
        # ROI 转正但净利润仍为负
        ("-0.2", "-20", ".1", "-5", 5, AlertState.LOSS_TO_PROFIT),
        # 同样转正，但订单门控不满足
        ("-0.2", "-20", ".1", "5", 1, AlertState.SAMPLE_INSUFFICIENT),
    ]
    for prev_roi, prev_profit, cur_roi, cur_profit, orders, expected in recovery_shapes:
        decision = evaluate(
            metric(prev_roi, prev_profit, order_count=orders),
            metric(cur_roi, cur_profit, order_count=orders),
            layer.warning,
            layer.critical,
        )
        # 转正形状永远不会落到一个 “回升 / 恢复” 状态：要么是 loss_to_profit，
        # 要么因样本门控先行被判为 sample_insufficient。
        assert decision.state is expected, (
            (prev_roi, prev_profit, cur_roi, cur_profit, orders),
            decision.state,
        )

    # paired_recovery 的 numerator 口径：转正 confirmation 依旧计入，
    # 且唯一能计入的 state 就是 loss_to_profit（删除两个死成员后集合只剩它）。
    alert = evaluate(
        metric("1", "100"), metric(".7", "70"), layer.warning, layer.critical
    )
    recovered = evaluate(
        metric("-0.2", "-20"), metric(".1", "5"), layer.warning, layer.critical
    )
    assert recovered.state is AlertState.LOSS_TO_PROFIT
    assert paired_recovery([("a", alert)], {"a": recovered}) == {
        "numerator": 1,
        "denominator": 1,
        "rate": Decimal(1),
    }


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
    """表头按钮的 data-state-values（不含 all；all 是 JS 侧循环起点，补上保持旧契约）。"""
    body = _TEMPLATE.read_text(encoding="utf-8")
    match = re.search(
        r'<button[^>]*id="filter-state"[^>]*data-state-values="([^"]+)"', body
    )
    assert match, "filter-state 表头按钮必须带 data-state-values"
    return ["all", *(v.strip() for v in match.group(1).split(",") if v.strip())]


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
