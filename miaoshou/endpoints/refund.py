"""退款 / 完工验收 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class RefundEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def apply_refund(
        self, *, order_no: str, reason: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """订单退费申请 · doc-802855."""
        params: dict[str, Any] = {"orderNo": order_no, "reason": reason}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/refund/apply", business_params=params
        )

    def confirm_pay_to_master(
        self, *, order_no: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """订单完工验收（师傅服务完成后，商家验收；调用即认为确认验收）· doc-1525956."""
        params: dict[str, Any] = {"orderNo": order_no}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/confirmPayToMaster", business_params=params
        )
