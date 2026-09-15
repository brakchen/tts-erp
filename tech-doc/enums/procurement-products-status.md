# `procurement.procurement_products.status` / `procurement_accounts.status` — 妙手采集状态

> 妙手侧产品/账号的状态码。
> ⚠️ **这是妙手内部的 free-text 枚举**，不是项目自有。

## 来源
- DB column A: `procurement.procurement_products.status` (Text, nullable)
- DB column B: `procurement.procurement_accounts.status` (Text, nullable)
- 类型: **text**（妙手 free-text）
- 上游: 妙手公共采集箱 / 采购接口

## 实测取值（🟡 实测 2026-09-14 prod）

### `procurement_products.status`

| 等级 |  值 | 含义（推断） | 实测 n |
| :---: | --- | --- | ---: |
| 🟡 |  `success` | 采集成功 | 835 |
| 🟡 |  `fail` | 采集失败 | 33 |
| 🟡 |  `skip` | 已跳过 | 32 |
| 🟡 |  `active` | 活跃（罕见，可能语义同 success） | 1 |
| 🟡 |  `NULL` | 未同步 | 3 |

### `procurement_accounts.status`

| 等级 |  值 | 含义（推断） | 实测 n |
| --- | --- | ---: |
| 🟡 |  `normal` | 正常 | 2 |
| 🟡 |  `active` | 活跃 | 1 |
| 🟡 |  `NULL` | 未同步 | 1 |

## ⚠️ 未固化值速查

- 🟡 **8 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``success`` — 采集成功
  - 🟡 ``fail`` — 采集失败
  - 🟡 ``skip`` — 已跳过
  - 🟡 ``active`` — 活跃（罕见，可能语义同 success）
  - 🟡 ``NULL`` — 未同步
  - …（其余 3 个见下方"## 取值"表）


## 已知 gap

- 🔴 **没有"权威"枚举定义** —— 妙手开放平台文档未必提供完整枚举
- 🔴 同一个字段出现 `success` 和 `active`（语义重叠？）—— **是真实业务数据**，但**没文档说明**
- ❌ 项目代码**没读这个 status 字段**做白名单校验——存在就存原值

## 引用
- 代码: `tts_erp_v2/db/models/procurement.py:52,100`
- 文档: `tech-doc/miaoshou-platform.md`（待补全 status 语义）
