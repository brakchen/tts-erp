# Monitor 接入 Zabbix 7.4

先发布 ERP 健康接口退役版本，确认公网和 HP Desk 的 `GET /endpoints` 均不再列出 `POST /v2/plugin/monitor/health`，并确认 HP Desk 同步 worker 已从同一发布版本重启。店铺名册仍由 ERP 店铺登记维护。人工发布、旧接口验收和生产网关配置见[公网 HTTPS 接入与切换](zabbix-https-cutover.md)。

## 导入模板和登记店铺

1. 在 Zabbix 导入 [TTS Monitor 模板](zabbix-monitor.yaml)，保留「删除缺失」选项关闭。导入模板后，只有链接模板的主机才有监控项。
2. 从 ERP 的 `GET /v2/commerce/channel-accounts?view=management&platform=tiktok&source=monitor` 分页读取登记店铺。每个 Seller ID 创建或匹配主机技术名称 `tts-monitor-<sellerId>`，显示名使用 `店名 · Seller ID`，空名称用 Seller ID。Zabbix 要求显示名唯一，保留 Seller ID 后缀可以区分同名店铺。主机放入专用组 `TTS Monitor shops` 并链接模板。名册为空时等待真实店铺登记。
3. 模板宏 `{$TTS.MONITOR.TRAPPER.HOSTS}` 须同时包含 Zabbix Web 看到的 HTTP 客户端地址（PHP 的 `REMOTE_ADDR`），以及 Web 向 Zabbix Server 发起连接时的源地址。HP Desk 当前分别为 FRP 入口看到的 `172.18.0.1` 和 Web 容器的 `172.18.0.3`。启用最终 HTTPS 路径或容器重建后，复核这两个地址，再用受限 Token 验证写入。
4. 通过生产 HTTPS 网关提供独立的 Zabbix 上报路径，转发到 HP Desk 的 Zabbix Web API。浏览器配置使用仅允许 `history.push` 且只授权 Monitor 主机范围的 Token。优先按单店授权，使其他店的 Seller ID 无法被同一凭据写入。Token 在插件本地连接设置中保存，且排除在配置导出和日志之外。

## 上报协议

一次 `history.push` 可以包含同一主机的多个项。每项使用 `host`、`key`、`value`。Zabbix JSON-RPC 响应必须逐项校验。请求不发送 `clock` 或 `ns`，由 Zabbix 记录接收时间。

| 项键 | 值和发出时机 |
| --- | --- |
| `monitor.heartbeat` | 每 30 秒发 `1`（运行）或 `0`（主动停止），状态变化时也立即尝试上报。 |
| `monitor.shop.name` | 文本店名。使用 Seller ID 匹配主机，店名变化不改变归属。 |
| `monitor.stage.activity[<stage>]` | `0` 表示无需观察。首次进入观察或有真实进展时发 `1`。阶段为 `scan`、`evaluate` 或 `close`。重复开始、失败和定时心跳不发送此项。 |
| `monitor.stage.progress[<stage>]` | 同一阶段有真实进展或完成时发 `1`，供已触发事件判断恢复。首次开始不发送。 |

阶段状态和计时由同一项更新。真实进展时发送活动值和进展脉冲。完成时同批结束观察并发送进展脉冲。预期阶段变化、失败重试、停止和恢复由插件报告原有业务事件产生，心跳独立于扫描和关停队列。上报失败不阻塞业务，历史心跳不重放。

## 启用与验收告警

在 Zabbix 建独立的触发器告警动作，同时筛选 `TTS Monitor shops` 主机组和事件标签 `service=tts-monitor`。问题动作仅执行一步，恢复动作发送一次，均使用现有 Feishu 媒介和指定收件组。故障消息展示主机显示名及阶段。

- 心跳初次收件后才具备失联基线。失联窗口为 135 秒，给 Zabbix 的 30 秒评估周期和飞书投递留时间。端到端 180 秒目标以现场收件验证为准。
- 业务阶段连续 180 秒无真实进展时判异常。主动停止期间仅检查失联。已打开的业务故障只有在插件运行且同一阶段收到近期真实进展时才发送恢复通知。
- 用测试店铺验证四类场景：关闭浏览器后失联，在线时扫描卡住，主动停止后仅保留失联检查，收到真实进展后发送恢复。验证正常换阶段、重复开始及普通心跳都不会把故障误报为恢复。还应验证错误店铺凭据和非上报 API 方法被拒绝。
- 验证公网 HTTPS、同一故障的通知去重和恢复消息后，再将动作应用到已登记的真实店铺。扫描周期超过 180 秒的配置须先检查，以免正常等待被判为无进展。

## 隔离验证记录

2026-11-05 在 HP Desk 上使用独立的 Zabbix 7.4.15 数据库和 Docker 网络完成验证。受限 Token 可写所属店铺并持久化中文店名，越权店铺和其他 API 方法被拒绝。扫描保持在线但停滞时，195 秒后送达一条告警。主动停止后停止心跳，161 秒后送达一条失联告警。重新发送停止心跳只恢复失联事件，重新运行也不提前恢复扫描事件。扫描收到真实进展后才恢复。两个故障各送达一次告警和一次恢复，接收端为隔离网络内的测试 Webhook。

参考 [Zabbix 模板导入](https://www.zabbix.com/documentation/7.4/en/manual/xml_export_import/templates)、[上报 API](https://www.zabbix.com/documentation/7.4/en/manual/api/reference/history/push) 和 [恢复动作](https://www.zabbix.com/documentation/7.4/en/manual/config/notifications/action/recovery_operations)。
