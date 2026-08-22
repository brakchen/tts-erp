"""投诉 endpoint（九件套）· apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class ComplaintEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def bank_list(self, **extra: Any) -> MiaoshouApiResponse:
        """获取支持赔付的银行卡列表 · doc-1735125."""
        params: dict[str, Any] = {}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/complaint/bankList", business_params=params
        )

    def apply(self, **payload: Any) -> MiaoshouApiResponse:
        """发起投诉 · doc-1735128. payload 字段见 doc 表格。"""
        return self._c._call(
            path="/user-open/order/complaint/apply", business_params=payload
        )

    def cancel(self, *, complaint_id: str, **extra: Any) -> MiaoshouApiResponse:
        """撤销投诉 · doc-1735133."""
        params: dict[str, Any] = {"complaintId": complaint_id}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/complaint/cancel", business_params=params
        )

    def get_type(self, **extra: Any) -> MiaoshouApiResponse:
        """获取投诉类别 · doc-1735138."""
        params: dict[str, Any] = {}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/complaint/type", business_params=params
        )

    def submit_evidence(self, **payload: Any) -> MiaoshouApiResponse:
        """提交举证 · doc-1735141. payload 字段见 doc 表格。"""
        return self._c._call(
            path="/user-open/order/complaint/evidenceSubmit", business_params=payload
        )

    def evidence_supplement(self, **payload: Any) -> MiaoshouApiResponse:
        """补充举证（客服要求补充证据时调用）· doc-1735143."""
        return self._c._call(
            path="/user-open/order/complaint/evidenceSupplement",
            business_params=payload,
        )

    def pay_channel(self, **extra: Any) -> MiaoshouApiResponse:
        """赔付收款渠道 · doc-1735147."""
        params: dict[str, Any] = {}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/complaint/payChannel", business_params=params
        )

    def detail(self, *, complaint_id: str, **extra: Any) -> MiaoshouApiResponse:
        """获取投诉详情 · doc-1735153."""
        params: dict[str, Any] = {"complaintId": complaint_id}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/complaint/detail", business_params=params
        )
