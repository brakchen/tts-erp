# `finance.settlement_components.component_code` — 结算费用成分 EAV 编码（58 字段）

> TikTok 结算单每条 transaction 拆解出的**费用成分码**（EAV 行项目）。
> `component_code` = 上游原始字段名 `removesuffix("_amount").upper()` 派生得来。

## 来源
- DB column: `finance.settlement_components.component_code` (Text, NOT NULL)
- 派生规则: `tts_erp_v2/jobs/tiktok/finance.py:124-178` 的 `_COMPONENT_COLUMNS` 元组
- 真实数据源: TikTok OpenAPI `/finance/202309/statements/{statement_id}/statement_transactions` 响应的 58 列
- 文档锚点: `tts_erp_v2/db/models/finance.py` 类注释（settlement_components）、`tts_erp_v2/jobs/tiktok/finance.py:47-49`

## 取值（✅ 固化，58 个 EAV 编码）

> **派生公式**：`gross_sales_amount` → `component_code="GROSS_SALES"`
> 所有 `_amount` 后缀去掉并大写。

| component_code | 派生自字段 | 含义（推断 / 命名直译） |
| --- | --- | --- |
| `ACTUAL_RETURN_SHIPPING_FEE` | `actual_return_shipping_fee_amount` | 实际退件运费 |
| `ACTUAL_SHIPPING_FEE` | `actual_shipping_fee_amount` | 实际运费 |
| `ADJUSTMENT` | `adjustment_amount` | 调整金额 |
| `AFFILIATE_ADS_COMMISSION` | `affiliate_ads_commission_amount` | 联盟广告佣金 |
| `AFFILIATE_COMMISSION` | `affiliate_commission_amount` | 联盟佣金 |
| `AFFILIATE_COMMISSION_BEFORE_PIT` | `affiliate_commission_before_pit` | 税前联盟佣金 |
| `AFFILIATE_PARTNER_COMMISSION` | `affiliate_partner_commission_amount` | 联盟合伙人佣金 |
| `AFTER_SELLER_DISCOUNTS_SUBTOTAL` | `after_seller_discounts_subtotal_amount` | 卖家折扣后小计 |
| `CUSTOMER_ORDER_REFUND` | `customer_order_refund_amount` | 客户订单退款 |
| `CUSTOMER_PAID_SHIPPING_FEE` | `customer_paid_shipping_fee_amount` | 客户已付运费 |
| `CUSTOMER_PAID_SHIPPING_FEE_REFUND` | `customer_paid_shipping_fee_refund_amount` | 客户已付运费退款 |
| `CUSTOMER_PAYMENT` | `customer_payment_amount` | 客户实付 |
| `CUSTOMER_REFUND` | `customer_refund_amount` | 客户退款 |
| `CUSTOMER_SHIPPING_FEE` | `customer_shipping_fee_amount` | 客户应付运费 |
| `CUSTOMER_SHIPPING_FEE_OFFSET` | `customer_shipping_fee_offset_amount` | 客户运费抵扣 |
| `FBM_SHIPPING_COST` | `fbm_shipping_cost_amount` | FBM 物流成本 |
| `FBT_FULFILLMENT_FEE` | `fbt_fulfillment_fee_amount` | FBT 履约费 |
| `FBT_FULFILLMENT_FEE_REIMBURSEMENT` | `fbt_fulfillment_fee_reimbursement_amount` | FBT 履约费补偿 |
| `FBT_SHIPPING_COST` | `fbt_shipping_cost_amount` | FBT 物流成本 |
| `FEE` | `fee_amount` | 平台费（汇总） |
| `GROSS_SALES` | `gross_sales_amount` | GMV 口径——商品销售总额（含税前） |
| `GROSS_SALES_REFUND` | `gross_sales_refund_amount` | GMV 退款 |
| `ISR_INCOME_TAX` | `isr_income_tax_amount` | ISR 所得税（拉美） |
| `IVA_VAT` | `iva_vat_amount` | 增值税（拉美 IVAR / 欧洲 VAT） |
| `NET_SALES` | `net_sales_amount` | 净销售额 |
| `PIT` | `pit_amount` | 个税（PIT，拉美/越南） |
| `PLATFORM_COMMISSION` | `platform_commission_amount` | 平台佣金 |
| `PLATFORM_DISCOUNT` | `platform_discount_amount` | 平台折扣 |
| `PLATFORM_DISCOUNT_REFUND` | `platform_discount_refund_amount` | 平台折扣退款 |
| `PLATFORM_REFUND_SUBSIDY` | `platform_refund_subsidy_amount` | 平台退款补贴 |
| `PLATFORM_SHIPPING_FEE_DISCOUNT` | `platform_shipping_fee_discount_amount` | 平台运费折扣 |
| `PROMO_SHIPPING_INCENTIVE` | `promo_shipping_incentive_amount` | 促销运费激励 |
| `REFERRAL_FEE` | `referral_fee_amount` | 推荐费 |
| `REFUND_ADMINISTRATION_FEE` | `refund_administration_fee_amount` | 退款管理费 |
| `REFUND_SHIPPING_COST_DISCOUNT` | `refund_shipping_cost_discount_amount` | 退款物流成本折扣 |
| `RETAIL_DELIVERY_FEE` | `retail_delivery_fee_amount` | 零售配送费 |
| `RETAIL_DELIVERY_FEE_PAYMENT` | `retail_delivery_fee_payment_amount` | 零售配送费实付 |
| `RETAIL_DELIVERY_FEE_REFUND` | `retail_delivery_fee_refund_amount` | 零售配送费退款 |
| `RETURN_SHIPPING_FEE` | `return_shipping_fee_amount` | 退件运费 |
| `REVENUE` | `revenue_amount` | 收入 |
| `SALES_TAX` | `sales_tax_amount` | 销售税 |
| `SALES_TAX_PAYMENT` | `sales_tax_payment_amount` | 销售税实付 |
| `SALES_TAX_REFUND` | `sales_tax_refund_amount` | 销售税退款 |
| `SELLER_DISCOUNT` | `seller_discount_amount` | 卖家折扣 |
| `SELLER_DISCOUNT_REFUND` | `seller_discount_refund_amount` | 卖家折扣退款 |
| `SETTLEMENT` | `settlement_amount` | **结算金额**（净额，关键字段） |
| `SHIPPING_COST` | `shipping_cost_amount` | 物流成本 |
| `SHIPPING_COST_DISCOUNT` | `shipping_cost_discount_amount` | 物流成本折扣 |
| `SHIPPING_FEE` | `shipping_fee_amount` | 运费 |
| `SHIPPING_FEE_SUBSIDY` | `shipping_fee_subsidy_amount` | 运费补贴 |
| `SHIPPING_INSURANCE_FEE` | `shipping_insurance_fee_amount` | 运费保险 |
| `SIGNATURE_CONFIRMATION_FEE` | `signature_confirmation_fee_amount` | 签收确认费 |
| `TRANSACTION_FEE` | `transaction_fee_amount` | 交易费 |

## 关键字段

- `GROSS_SALES` —— GMV
- `NET_SALES` —— 销售净收
- `PLATFORM_COMMISSION` —— 平台抽佣
- `SETTLEMENT` —— 结算净额（打款金额）
- `FEE` —— 平台费汇总（含 sub-commission）

## 已知 gap

- ❌ 53 字段名**英文直译**，没有中文/业务对照字典（部分字段如 `ISR_INCOME_TAX` 实际只在拉美市场出现）
- ❌ **没有按国家/地区维度的子集**——同一 component_code 在不同区域含义可能不同
- ❌ 没有"卖家净到手 = GROSS_SALES - 各类 fee - 税" 的标准公式文档化

## 引用
- 代码: `tts_erp_v2/jobs/tiktok/finance.py:124-178`（`_COMPONENT_COLUMNS` 权威列表）、`tts_erp_v2/db/models/finance.py` SettlementComponent 类注释
- 文档: `tech-doc/refactor-tech-plan-v2.md §3.2 / §3.5`
- 测试: `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/tests/test_migrate_finance.py:101-128`（验证大写无 _AMOUNT 后缀）
