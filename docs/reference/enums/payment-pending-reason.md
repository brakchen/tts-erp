# `plugin.settlements.payment_pending_reason` — 付款挂起原因

> 当 `payment_status=挂起` 时的原因码（int）。

## 来源
- DB column: `plugin.settlements.payment_pending_reason` (Integer, nullable)
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/v1/pay/statement/order/list` 响应

## 取值

🔴 **完全未知** —— 项目中**未观察到任何样本**。

## 已知 gap

- ❌ 没有任何样本
- ❌ 没有任何枚举定义
- ❌ 不知道 TikTok 端真实有哪些值（如银行问题 / 卖家待补资料 / 平台审核中 / ...）

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:425`
- 文档: 无
