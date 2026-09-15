# `order/list reverse_module.reverse_status` — 售后/取消状态（卖家中心 int）

> TikTok 卖家中心 `reverse_module[0].reverse_status` —— 售后/取消单的状态码。
> 配套 [`reverse-type.md`](reverse-type.md)。

## 来源
- DB column: **不存**（与 reverse_type 同）
- 持久化: `plugin.intercepted_requests.response_body[].reverse_module[0].reverse_status`
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `main_orders[].reverse_module[0].reverse_status`

## 取值（🟡 仅观察到 2 种）

| int 码 | 含义（推断） | 实测 |
| ---: | --- | ---: |
| `4` | 处理中 / 售后进行中 | 是（`reverse_type=4` 路径下 1 例） |
| `100` | 已完成 | 是（`reverse_type=1` 路径下 1 例，cancelled_time 已设） |

## 已知 gap

- 🔴 **完整枚举未文档化** —— 卖家中心可能还有 `0`（待审核）、`1`（已拒绝）等中间态
- ❌ **不存库**——所有 reverse_status 都活在 response_body JSONB 里，无法 SQL 查询

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:280-310`
- 文档: `tech-doc/plugin-sourced-shop-analytics.md §4.4 / §8`
