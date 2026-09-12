# tts-erp 常见 bug + 修复

> 本文档包含 tts-erp 项目的常见 bug 和修复方案。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 常见 bug 表格

| 症状 | 原因 | 修复 |
| --- | --- | --- |
| `106001 invalid sign` | 签名格式错（最常见） | `TTS_DEBUG_SIGN=1` 看 canonical，对比 §4.2 |
| `105005 Access denied` | app 没勾 scope | Partner Center 改 app scope + 重新授权 |
| `36009004 PageSize is required` | body 字段名/格式错 | 查 TikTok API 文档 Request Body 章节 |
| v2 端点传 `?shop_id=` 没过滤 | v2 只认内部 id，静默忽略 | 先查 `shop_pk`（§5） |
| 物流数据多日不更新 | `tiktok.logistics` job 没在跑 | `systemctl --user status tts-erp-sync.service`；取 tracking 首尾必须按 `update_time_millis` 排序（列表最新在前） |
| `psycopg.OperationalError` | PG 容器 down | `docker exec postgres pg_isready` |

## 2. 详细说明

### 2.1 签名错误（106001 invalid sign）

**原因**：签名格式错（最常见）

**修复**：

1. 设置 `TTS_DEBUG_SIGN=1` 环境变量
2. 查看 stderr 输出的 canonical 字符串
3. 对比 `tech-doc/tiktok-hmac-signing.md` 中的规范
4. 检查 keys 是否按字母序、shop_cipher 是否在 query、body 是否 URL-encode 了

### 2.2 权限错误（105005 Access denied）

**原因**：app 没勾 scope

**修复**：

1. 登录 TikTok Partner Center
2. 找到对应 app
3. 修改 app scope 勾选所需权限
4. 重新授权

### 2.3 字段缺失（36009004 PageSize is required）

**原因**：body 字段名/格式错

**修复**：

1. 查 TikTok API 文档 Request Body 章节
2. 确认字段名拼写和格式
3. 检查必填字段是否都传了

### 2.4 过滤无效（v2 端点传 `?shop_id=` 没过滤）

**原因**：v2 只认内部 id，静默忽略

**修复**：

1. 先查 `shop_pk`（内部主键）
2. 用 `?shop_pk=<内部 id>` 过滤
3. 参考 `tech-doc/external-api.md` 中的过滤规则

### 2.5 物流数据不更新

**原因**：`tiktok.logistics` job 没在跑

**修复**：

1. 检查 sync-worker 状态：`systemctl --user status tts-erp-sync.service`
2. 查看 sync-worker 日志确认 job 是否正常调度
3. 取 tracking 首尾必须按 `update_time_millis` 排序（列表最新在前）

### 2.6 数据库连接错误（psycopg.OperationalError）

**原因**：PG 容器 down

**修复**：

1. 检查 PostgreSQL 容器状态：`docker exec postgres pg_isready`
2. 如果容器 down，重启容器
3. 检查 `.env` 中的 `TTS_ERP_DB_URL` 配置
