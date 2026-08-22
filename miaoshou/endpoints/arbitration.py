"""仲裁 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class ArbitrationEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def apply(
        self, *, order_no: str, reason_code: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """仲裁申请 · doc-947445."""
        params: dict[str, Any] = {"orderNo": order_no, "reasonCode": reason_code}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/refund/arbitrationApply", business_params=params
        )

    def get_reason(self, **extra: Any) -> MiaoshouApiResponse:
        """仲裁类别查询 · doc-947593."""
        params: dict[str, Any] = {}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/refund/getArbitrationReason", business_params=params
        )

    def cancel(self, *, arbitration_id: str, **extra: Any) -> MiaoshouApiResponse:
        """仲裁撤销 · doc-947766."""
        params: dict[str, Any] = {"arbitrationId": arbitration_id}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/refund/arbitrationCancel", business_params=params
        )

    def submit_evidence(
        self, *, arbitration_id: str, evidence_list: list[dict[str, Any]], **extra: Any
    ) -> MiaoshouApiResponse:
        """仲裁提交证据 · doc-947793."""
        params: dict[str, Any] = {
            "arbitrationId": arbitration_id,
            "evidenceList": evidence_list,
        }
        params.update(extra)
        return self._c._call(
            path="/user-open/order/refund/submitEvidence", business_params=params
        )
