# `linkage.link_issues.issue_type` — 链接异常类型

> 检测到的商品链接/账号链接异常类型。
> 项目自有枚举——通过 `/v2/linkage/issues` API 暴露给前端。

## 来源
- DB column: `linkage.link_issues.issue_type` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/linkage.py:236-239`（注释）

## 取值（✅ 固化）

| 等级 |  值 | 含义 | 触发场景 |
| :---: | --- | --- | --- |
| ✅ |  `PRODUCT_LINK_MISSING` | 缺少商品链接 | miaoshou 有品但 TikTok 端没绑 / 反之 |
| ✅ |  `MULTIPLE_PRIMARY_LINKS` | 多个主链接冲突 | 同 SPU 多个 product_link 同时 `is_primary=true` |
| ✅ |  `SOURCE_LINK_CONFLICT` | 货源链接冲突 | 多个 miaoshou 标为同一个 TikTok SPU 的货源 |
| ✅ |  `ACCOUNT_LINK_MISSING` | 缺少账号链接 | miaoshou license 没绑 TikTok shop |
| ✅ |  `VARIANT_LINK_MISSING` | 缺少变体链接 | SKU 级别未关联 |
| ✅ |  `AMBIGUOUS_SOURCE` | 货源模糊 | 多个货源都能匹配到同一 SPU，无法决定 |

## 引用
- 代码: `tts_erp_v2/db/models/linkage.py:236-239`
- API 暴露: `/v2/linkage/issues`
