# 物流终态白名单 — `LOGISTICS_TERMINAL_CODES`

> 项目**自有的**物流终态判定集合。
> 用来判断"是否还需要继续抓物流事件"——见 `tech-doc/order-domain-business-rules.md §5`。

## 来源
- 定义: `tts_erp_v2/plugin/orders/repository.py` 的 `_LOGISTICS_TERMINAL_CODES`（与 `tech-doc/order-domain-business-rules.md §5` 保持一致）
- 类型: `set[int]`
- 文档锚点: `tech-doc/order-domain-business-rules.md §5`

## 取值（✅ 固化在文档中）

| 等级 |  code | 描述 | 含义 |
| :---: | ---: | --- | --- |
| ✅ |  `50101` | "Your package was delivered!" | 已签收 |
| ✅ |  `80101` | "returned to seller by shipping provider" | 退回卖家 |
| ✅ |  `110101` | "Your package delivery was canceled" | 配送取消 |

## 判断逻辑

```python
LOGISTICS_TERMINAL_CODES = {"50101", "80101", "110101"}

def is_logistics_terminal(tracking_events: list[dict]) -> bool:
    """物流是否到达终态（已签收 / 退回卖家 / 配送取消）"""
    if not tracking_events:
        return False
    last_event = max(tracking_events, key=lambda e: e["update_time_millis"])
    return str(last_event.get("action_code")) in LOGISTICS_TERMINAL_CODES
```

## 关键设计决策（`tech-doc/order-domain-business-rules.md §5`）

> **物流信息是否继续拉取，由"物流终态"独立判断，不依赖订单状态。**

- 订单状态 `COMPLETED` 后，物流可能还没到 `50101`（签收事件延迟回传），插件仍继续拉
- 这就解决了"订单已终态但物流事件还没全部回传"的缺口

## 实现状态

- ✅ 后端已在 `tts_erp_v2/plugin/orders/repository.py` 固化 `{50101, 80101, 110101}` 白名单
- ✅ 插件端已持久化 `action_code`；物流终态优先按白名单判断，旧数据或缺码数据只做完整文本匹配，未知状态继续采集。

## 引用
- 代码: `tts_erp_v2/plugin/orders/repository.py::_LOGISTICS_TERMINAL_CODES`
- 文档: `tech-doc/order-domain-business-rules.md §5`
