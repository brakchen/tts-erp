"""妙手开放平台 17 个回调节点派发器（不依赖 FastAPI）。

设计目标：
- 与 tts-erp 的 ``ThreadingHTTPServer`` + 自定义 ``Handler`` 类整合
- 不引入 FastAPI 依赖
- ``dispatch_callback(order_status, raw_dict) -> tuple[int, dict]``
  返回 (http_status, body)；  body  含 ``code/message``，
  按 doc-1297700 约定 ``code==200`` 表示回传成功（妙手会停止重试）。

参考：
- doc-802872 服务节点通知 — 所有回调的统一格式
- doc-1297700 版本更新 — retCode != 200 会触发自动重试
"""

from __future__ import annotations

from typing import Any

from .payloads import (  # type: ignore[reportMissingImports]
    AftersaleOrderProcessPayload,
    AftersaleOrderWaitPayload,
    ArbitrationPayload,
    EmptyRunPayload,
    EnterpriseFeeAdjustPayload,
    ExtraRepairPayload,
    FeeReasonUpdatePayload,
    MasterCallPayload,
    OldPartsReturnPayload,
    RushOrderPayload,
    SecondVisitPayload,
    ServiceNodePayload,
    SubOrderStatusNoticePayload,
    TimerNodePayload,
    TotalAuditClosePayload,
    TotalPackageCloseApplyPayload,
    UrgeReplyPayload,
    UserRemindArrivalPayload,
)

# 每个 orderStatus 状态值 → (Pydantic model, 路径别名)
# 路径别名用于 /miaoshou/callback/<name> 单节点路由
NODE_REGISTRY: dict[str, tuple[type, str]] = {
    # 服务节点（最小化对接必接）
    "service_node": (ServiceNodePayload, "service-node"),
    # 一口价订单服务节点
    "rush_order": (RushOrderPayload, "rush-order"),
    # 定时单服务节点
    "order_timer_node": (TimerNodePayload, "timer-node"),
    # 总包调整费用节点
    "enterprise_operate_sub_order": (
        EnterpriseFeeAdjustPayload,
        "enterprise-fee-adjust",
    ),
    # 仲裁节点
    "arbitration_data_info": (ArbitrationPayload, "arbitration"),
    # 费用调整原因列表变更
    "adjust_fee_reason_modified": (FeeReasonUpdatePayload, "fee-reason-update"),
    # 保外维修单
    "insurance_modify_fee_toc": (ExtraRepairPayload, "extra-repair"),
    # 催单回复
    "reminder_result": (UrgeReplyPayload, "urge-reply"),
    # 总包发起关单申请
    "close_order_foraudit": (TotalPackageCloseApplyPayload, "total-package-close"),
    # 旧件寄回
    "repair_parts_send_back": (OldPartsReturnPayload, "old-parts-return"),
    # 二次上门
    "serve_second_service": (SecondVisitPayload, "second-visit"),
    # 总包审核关单申请回传
    "enterprise_audit_third_apply_close_order": (
        TotalAuditClosePayload,
        "total-audit-close",
    ),
    # 售后单待处理
    "after_sale_order_wait": (AftersaleOrderWaitPayload, "aftersale-wait"),
    # 售后单处理中
    "after_sale_order_process": (AftersaleOrderProcessPayload, "aftersale-process"),
    # 师傅与客户通话
    "axb_num_call_record_detail_open": (MasterCallPayload, "master-call"),
    # 师傅发起子订单状态回传
    "sub_order_status_notice": (SubOrderStatusNoticePayload, "sub-order-status"),
    # 空跑
    "empty_run_order": (EmptyRunPayload, "empty-run"),
    # 用户提醒到货
    "user_logisticsArrive_open": (UserRemindArrivalPayload, "user-remind-arrival"),
}


def path_for_order_status(order_status: str) -> str | None:
    """返回 ``order_status`` 对应的单节点回调路径。"""
    entry = NODE_REGISTRY.get(order_status)
    if entry is None:
        return None
    return f"/miaoshou/callback/{entry[1]}"


def dispatch_callback(order_status: str, raw: dict[str, Any]) -> tuple[int, dict]:
    """根据 ``order_status`` 字段分发到对应的 model 校验。

    Args:
        order_status: 回调中的 ``orderStatus`` 字段。
        raw: 解码后的 JSON dict（必须含 ``orderStatus`` / ``thirdOrderId`` / ``data``）。

    Returns:
        (http_status, body)。返回 ``200`` + ``{"code":200,"message":"success"}``
        表示成功，妙手不再重试。

    Raises:
        ValueError: ``orderStatus`` 未知，或 ``raw`` 不符合对应 model schema。
    """
    entry = NODE_REGISTRY.get(order_status)
    if entry is None:
        return 400, {
            "code": 400,
            "message": f"unknown orderStatus: {order_status!r}",
            "supported": sorted(NODE_REGISTRY.keys()),
        }
    model_cls = entry[0]
    try:
        model_cls.model_validate(raw)
    except (ValueError, TypeError, AttributeError) as e:
        return 400, {
            "code": 400,
            "message": f"payload validation failed: {e}",
            "orderStatus": order_status,
        }
    return 200, {"code": 200, "message": "success", "orderStatus": order_status}


def all_node_paths() -> list[str]:
    """所有单节点回调路径（用于 ``/endpoints`` 文档）。"""
    return [f"/miaoshou/callback/{alias}" for _, alias in NODE_REGISTRY.values()]
