"""妙手开放平台 17 个回调节点的 Pydantic 模型（apifox doc-802872 + 各类节点 doc）。

通用 envelope（doc-802872）::

    {
      "orderStatus":  "<节点标识>",
      "thirdOrderId": "<第三方订单号>",
      "data":          { ... 节点业务参数 ... },
      "timestamp":     1700000000000
    }

所有具体节点在 ``data`` 里塞各自的字段。下面每个 ``*Payload`` 类都把 envelope
顶层字段 + data 内层字段一起平铺，方便消费方直接 ``.orderStatus`` / ``.data.x``
拿到。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CallbackEnvelope(BaseModel):
  """通用回调 envelope（所有节点都用这套外壳）。"""

  model_config = ConfigDict(extra="allow")

  orderStatus: str
  thirdOrderId: str
  data: dict[str, Any] = Field(default_factory=dict)
  timestamp: int | None = None


# ---- 1. 服务节点通知 doc-802872 ----
class ServiceNodePayload(CallbackEnvelope):
  """最小化对接必接。data 字段形如 {orderNodeStatus, masterId, masterPhone, ...}。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 2. 一口价订单抢单 doc-4807719 ----
class RushOrderPayload(CallbackEnvelope):
  """rush_order 节点。data 含 masterId/masterName/masterPhone/offerPriceNote/promiseInfoList。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 3. 定时单服务节点 doc-1297662 ----
class TimerNodePayload(CallbackEnvelope):
  """order_timer_node 节点。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 4. 总包调整费用节点 doc-1297670 ----
class EnterpriseFeeAdjustPayload(CallbackEnvelope):
  """enterprise_operate_sub_order 节点（change_order_fee / cancel_suborder_fee / ...）。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 5. 仲裁节点 doc-1297684 ----
class ArbitrationPayload(CallbackEnvelope):
  """arbitration_data_info 节点（open_arbitration_evidence / close_arbitration_evidence /
  get_arbitration_result / customer_supplementary_evidence）。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 6. 费用调整原因变更 doc-1297691 ----
class FeeReasonUpdatePayload(CallbackEnvelope):
  """adjust_fee_reason_modified 节点。data 含 changeType (ADD/MODIFY/CHANGE_STATUS)。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 7. 保外维修单节点 doc-1473369 ----
class ExtraRepairPayload(CallbackEnvelope):
  """insurance_modify_fee_toc 节点。data 含 globalOrderTraceId/tocTotalOrderFee。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 8. 催单回复 doc-1936975 ----
class UrgeReplyPayload(CallbackEnvelope):
  """reminder_result 节点。data 含 content/imgUrls/operateTime。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 9. 总包发起关单申请 doc-1936986 ----
class TotalPackageCloseApplyPayload(CallbackEnvelope):
  """close_order_foraudit 节点。data 含 auditStatus/globalOrderTraceId/reason。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 10. 旧件寄回 doc-1936988 ----
class OldPartsReturnPayload(CallbackEnvelope):
  """repair_parts_send_back 节点。data 含 logisticsCompany/logisticsNo。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 11. 二次上门 doc-2264227 ----
class SecondVisitPayload(CallbackEnvelope):
  """serve_second_service 节点。data 含 costlist/secondDoorStartTime/reason/imageUrls/videoUrl。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 12. 总包审核关单申请回传 doc-3470104 ----
class TotalAuditClosePayload(CallbackEnvelope):
  """enterprise_audit_third_apply_close_order 节点。data 含 refuseReason/status。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 13. 售后单待处理 doc-4614577 ----
class AftersaleOrderWaitPayload(CallbackEnvelope):
  """after_sale_order_wait 节点。data 含 afterSaleOrderNo。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 14. 售后单处理中 doc-4614577 ----
class AftersaleOrderProcessPayload(CallbackEnvelope):
  """after_sale_order_process 节点。data 含 afterSaleOrderNo/masterId/masterName/masterPhone。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 15. 师傅与客户通话 doc-6684300 ----
class MasterCallPayload(CallbackEnvelope):
  """axb_num_call_record_detail_open 节点。data 含 callStartTime/callEndTime/durationTime。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 16. 师傅发起子订单状态回传 doc-7006352 ----
class SubOrderStatusNoticePayload(CallbackEnvelope):
  """sub_order_status_notice 节点。data 含 subOrderList/status/reason。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 17. 空跑节点 doc-7611598 ----
class EmptyRunPayload(CallbackEnvelope):
  """empty_run_order 节点。data 通常为空对象。"""

  data: dict[str, Any] = Field(default_factory=dict)


# ---- 18. 用户提醒到货节点 doc-7955713 ----
class UserRemindArrivalPayload(CallbackEnvelope):
  """user_logisticsArrive_open 节点。data 通常为空对象。"""

  data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
  "AftersaleOrderProcessPayload",
  "AftersaleOrderWaitPayload",
  "ArbitrationPayload",
  "CallbackEnvelope",
  "EmptyRunPayload",
  "EnterpriseFeeAdjustPayload",
  "ExtraRepairPayload",
  "FeeReasonUpdatePayload",
  "MasterCallPayload",
  "OldPartsReturnPayload",
  "RushOrderPayload",
  "SecondVisitPayload",
  "ServiceNodePayload",
  "SubOrderStatusNoticePayload",
  "TimerNodePayload",
  "TotalAuditClosePayload",
  "TotalPackageCloseApplyPayload",
  "UrgeReplyPayload",
  "UserRemindArrivalPayload",
]
