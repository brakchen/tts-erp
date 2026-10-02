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
| `import_prod_to_test.sh` 跑完后磁盘持续膨胀、容器 `/tmp` 堆 `tmp.*.sql` | `mktemp` 在宿主机建文件，`docker exec pg_dump --file` 却写进容器 | 已修（2026-10-02）：dump 走 stdout 落宿主机、psql 走 stdin；存量垃圾 `docker exec postgres sh -c 'rm -f /tmp/tmp.*.sql'` |

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

### 2.7 import 后磁盘膨胀（容器 /tmp 堆 tmp.*.sql）

**原因**：`scripts/import_prod_to_test.sh` 用宿主机 `mktemp --suffix=.sql` 生成临时路径，但
`pg_dump`/`psql` 被包装为 `docker exec postgres ...`，`--file=<host-path>` 实际写进**容器内**
`/tmp`；收尾的 `rm -f` 只删了宿主机空壳文件，容器里的明文 dump 永久残留（单次全量 import
可泄漏数 GB——曾累积 295 个文件共 15G，含 `integration.raw_records` 全量 COPY dump）。
写入者和读取者都在容器里，所以 import 功能正常、无报错，只有清理跨错了命名空间。

**修复**（2026-10-02）：dump 经 stdout 落到宿主机临时文件、psql 经 stdin 读回
（docker 分支的 psql 使用 `docker exec -i`），`mktemp`/`rm` 同侧闭环。约束注释见
`scripts/import_prod_to_test.sh` 的 "File-transport invariant" 段。

**存量垃圾清理**：

```bash
docker exec postgres sh -c 'rm -f /tmp/tmp.*.sql'
```
