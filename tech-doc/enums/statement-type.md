# `plugin.settlements.statement_type` — 结算单类型

> 结算单的分类（账单 / 发票 / 调整 / 其它）。

## 来源
- DB column: `plugin.settlements.statement_type` (Integer, nullable)
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/v1/pay/statement/order/list` 响应

## 取值

🔴 **完全未知** —— 项目中**未观察到任何样本**，也没有文档/代码定义。

## 已知 gap

- ❌ 没有任何样本
- ❌ 没有任何枚举定义
- ❌ 不知道 TikTok 端真实有哪些值

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:423`
- 文档: 无
