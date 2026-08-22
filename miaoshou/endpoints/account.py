"""子账号管理 endpoint · apifox 妙手开放平台."""

from __future__ import annotations

from typing import Any

from ..miaoshou_client import MiaoshouApiResponse, MiaoshouClient


class AccountEndpoint:
    def __init__(self, client: MiaoshouClient) -> None:
        self._c = client

    def update_sub_user_role(
        self, *, sub_user_id: str, role: str, **extra: Any
    ) -> MiaoshouApiResponse:
        """更改子用户角色权限 · doc-4560118."""
        params: dict[str, Any] = {"subUserId": sub_user_id, "role": role}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/updateSubUserRole", business_params=params
        )

    def query_sub_user_info_list(
        self, *, page: int = 1, page_size: int = 20, **extra: Any
    ) -> MiaoshouApiResponse:
        """查询子账号列表 · doc-9001165."""
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        params.update(extra)
        return self._c._call(
            path="/user-open/order/querySubUserInfoList", business_params=params
        )

    def update_buyer_phone(
        self, *, order_no: str, new_phone: str
    ) -> MiaoshouApiResponse:
        """修改客户虚拟手机号 · doc-8605253."""
        return self._c._call(
            path="/user-open/order/updateBuyerPhone",
            business_params={"orderNo": order_no, "newPhone": new_phone},
        )
