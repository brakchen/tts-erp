# `after_sales.cases.case_type` — 售后工单类型（项目内部定义）

> 项目**自有**售后工单分类（**3 大类**），对应 v2 标准化表 `after_sales.cases`。
> 与 `plugin.after_sales.cancel_type`（TikTok 原始 2 类）**不是同一套枚举**——但语义高度相关。

## 来源
- DB column: `after_sales.cases.case_type` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 上游: 由 `cancellations/search` 或 `returns/search` 端点归一化而来（v2 标准化层映射）
- 文档锚点: `tts_erp_v2/db/models/after_sales.py:32`（注释）、`tech-doc/order-domain-business-rules.md §3`

## 取值（✅ 固化）

| 等级 |  值 | 含义 | 来源原始字段 | v9 业务归类 |
| :---: | --- | --- | --- | --- |
| ✅ |  `CANCELLATION` | 仅取消（含拒收、超时未付款、自动取消） | `cancellations/search` 的 `cancel_type=BUYER_CANCEL \| CANCEL` | 国内取消（无 38301）/ 海外取消（有 38301） |
| ✅ |  `REFUND_ONLY` | 仅退款（货已发出但退款） | `returns/search` 的 `return_type=REFUND_ONLY` | 完结退货（无论物流） |
| ✅ |  `RETURN_AND_REFUND` | 退货退款（货退回 + 退款） | `returns/search` 的 `return_type=RETURN_AND_REFUND` | 完结退货（无论物流） |

## 业务映射

> v9 ROI 口径（`tech-doc/analytics/spu_roi.py`）：

| case_type | 全损计数 | 取消率分子 | 完结退货 |
| :---: | :---: | :---: | :---: |
| `CANCELLATION` | 仅当 ∧38301 | ✓ | — |
| `REFUND_ONLY` | ✓ | — | ✓ |
| `RETURN_AND_REFUND` | ✓ | — | ✓ |

## 引用
- 代码: `tts_erp_v2/db/models/after_sales.py:32`（注释 `CANCELLATION | REFUND_ONLY | RETURN_AND_REFUND`）
- 文档: `tech-doc/order-domain-business-rules.md §3`、`tech-doc/analytics/spu_roi.py:184-220`
