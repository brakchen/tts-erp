# `commerce.products_spu.status` — SPU 商品状态

> TikTok 上架/下架/删除的 SPU 状态枚举。
> 这是**项目**有"权威来源"**的枚举**之一（`tts_erp_v2/db/constants.py`）。

## 来源
- DB column: `commerce.products_spu.status` (Text, nullable)
- 类型: **text**
- 上游: TikTok OpenAPI `/product/202309/products/search` 响应 `status` 字段
- 权威定义: `tts_erp_v2/db/constants.py:23-41`

## 取值（✅ 固化）

| 值 | 含义 | 白名单 | 验证日期 |
| --- | --- | :---: | --- |
| `ACTIVATE` | 已上架（在售） | active | 2026-08-30 prod 实测 |
| `DEACTIVATE` | 已下架（卖家手动下架） | delisted | — |
| `DELETED` | 已删除 | delisted | — |
| `SUSPENDED` | 已暂停（平台处罚 / 审核中） | delisted | — |
| `ARCHIVED` | 已归档 | delisted | — |

## 固化的常量（`tts_erp_v2/db/constants.py`）

```python
ACTIVE_PRODUCT_STATUS: Final[str] = "ACTIVATE"
ACTIVE_PRODUCT_STATUSES = frozenset({ACTIVE_PRODUCT_STATUS})
DELISTED_PRODUCT_STATUSES = frozenset(
    {"DEACTIVATE", "DELETED", "SUSPENDED", "ARCHIVED"}
)
```

## 已知 gap

- ✅ 完全固化，**无 gap**
- ⚠️ 老的 docs/tests 可能用 `'active'` 小写（错）；现在统一 `'ACTIVATE'` 大写

## 引用
- 代码: `tts_erp_v2/db/constants.py:23-41`、`tts_erp_v2/db/models/commerce.py:97`
- 文档: `tts_erp_v2/db/constants.py` 顶部 docstring（详细解释）
