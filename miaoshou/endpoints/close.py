"""关单 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class CloseEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def close_order(
        self, *, order_no: str, reason: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """订单关闭 · doc-802848. 走绝对路径（老 URL）。"""
        params: dict[str, Any] = {"orderNo": order_no, "reason": reason}
        params.update(extra)
        return self._c._call(
            path="https://openapi.wanshifu.com/user-open/order/closeOrder",
            business_params=params,
            absolute=True,
        )

    def audit_close_order(
        self,
        *,
        order_no: str,
        audit_status: int,
        reason: str | None = None,
        **extra: Any,
    ) -> MiaoshouApiResponse:
        """第三方关单审核结果同步 · doc-1938167.

        audit_status: 1=审核通过, 2=审核拒绝
        """
        params: dict[str, Any] = {"orderNo": order_no, "auditStatus": audit_status}
        if reason:
            params["reason"] = reason
        params.update(extra)
        return self._c._call(
            path="/user-open/order/audit/closeOrder", business_params=params
        )

    def work_order_apply(
        self, *, order_no: str, reason: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """第三方工单申请 · doc-3485549."""
        params: dict[str, Any] = {"orderNo": order_no, "reason": reason}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/workOrderApply", business_params=params
        )
