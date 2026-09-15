# `commerce.shops.platform` — 店铺平台

> 区分 TikTok Shop 店铺 vs 其他平台（预留）。

## 来源
- DB column: `commerce.shops.platform` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/commerce.py:55-57`

## 取值（✅ 固化）

| 值 | 含义 | 备注 |
| --- | --- | --- |
| `tiktok` | TikTok Shop 店铺 | 当前**唯一**生产平台 |

> 未来可能扩展（如 Lazada / Shopee），但**目前没有其他值**——任何 `platform` 不为 `tiktok` 的店铺是**异常数据**。

## 引用
- 代码: `tts_erp_v2/db/models/commerce.py:55-57`
