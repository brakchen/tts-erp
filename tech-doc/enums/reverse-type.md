# `order/list reverse_module.reverse_type` — 售后/取消子类型（卖家中心 int）

> TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `reverse_module[0].reverse_type`。
> **与 `plugin.after_sales.cancel_type` 是不同维度的枚举**——这个是卖家中心按"用户最终动作"的细分。

## 来源
- DB column: **不存** —— 反序列化后丢弃（`tts_erp_v2/plugin/orders/parser.py` 没写 `reverse_type` 列）
- 持久化: `plugin.intercepted_requests.response_body[].reverse_module[0].reverse_type`（response_body JSONB）
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `main_orders[].reverse_module[0].reverse_type`
- 文档锚点: `tech-doc/plugin-sourced-shop-analytics.md §8`、`tech-doc/intercept-plugin-canonical.md §3`

## 取值

| 等级 | int 码 | 含义（推断） | 实测样本 | 来源 |
| :---: | ---: | --- | ---: | --- |
| 🟡 | `1` | 系统取消 / 超时未付款 | 2 单（VN Bridge nook） | "客户超时支付" |
| 🟡 | `3` | 退货退款 | 0（`/api/fulfillment/order/list` 路径下未出现） | `tech-doc/plugin-sourced-shop-analytics.md §8` 推断 |
| 🟡 | `4` | 买家取消 | 14 单（VN Bridge nook） | "不想要了" / "发现更优惠的价格" / "需要更改收货地址/付款方式" / "预计送达时间过晚" |
| 🔴 | `2` | （未观测） | 0 | 未知——可能"仅退款"或"申诉"等场景 |
| 🔴 | `0` | （未观测） | 0 | 未知——可能"未审核/草稿" |
| 🔴 | `5+` | （未观测） | 0 | 未知——上游可能有更多码 |

## ⚠️ 未固化值速查

- 🟡 **3 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``1`` — 系统取消 / 超时未付款
  - 🟡 ``3`` — 退货退款
  - 🟡 ``4`` — 买家取消

- 🔴 **3 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``2`` — （未观测）
  - 🔴 ``0`` — （未观测）
  - 🔴 ``5+`` — （未观测）


## 与 `cancel_type` 关系

| reverse_type | cancel_type 推断 | case_type 推断 |
| ---: | --- | --- |
| `1` | `CANCEL` | `CANCELLATION`（系统取消） |
| `3` | （return 接口，不在 cancellation/search） | `RETURN_AND_REFUND` |
| `4` | `BUYER_CANCEL` | `CANCELLATION`（买家取消） |

> ⚠️ **`/api/fulfillment/order/list` 的 `reverse_type` 跟 `/return_refund/.../cancellations/search` 的 `cancel_type` 是同一业务事实的两套编码**——前者是卖家中心 int 码，后者是 API text。**没有自动转换关系**。

## 已知 gap

- 🔴 **`reverse_type` 完整枚举未文档化** —— `tech-doc/plugin-sourced-shop-analytics.md §8` 明确："`reverse_type` 枚举（3=退货? 4=取消?）**待核实**"——且 1 类没有示例对照
- 🔴 type=2 完全未观测到（什么场景？）
- ❌ 卖家中心退货流程不通过 `order/list` 暴露（`/return_refund/.../returns/search` 才有完整退货数据）
- ❌ **plugin 表里没存 reverse_type**——目前是"抓到了但不落库"，P0 TODO（`tech-doc/plugin-sourced-shop-analytics.md §4.4`）

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:280-310`（读但没写库）
- 文档: `tech-doc/plugin-sourced-shop-analytics.md §4.4 / §8`、`tech-doc/intercept-plugin-canonical.md §3`、`tech-doc/tiktok-seller-center-api-catalog.md §2.5`
