# PostgreSQL 定时备份方案

> 创建日期: 2026-09-12
> 状态: 已部署运行中

## 概述

TTS-ERP 使用每小时 `pg_dump` 逻辑备份 + 自动轮转方案，确保数据库可恢复。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  systemd timer (tts-erp-pgbackup.timer)                    │
│  OnCalendar: *-*-* *:00:00 (每小时整点)                    │
│  RandomizedDelaySec: 30                                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  systemd service (tts-erp-pgbackup.service)                │
│  Type: oneshot                                              │
│  ExecStart: scripts/backup/pg_dump_hourly.sh               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  pg_dump_hourly.sh                                          │
│  1. docker exec postgres pg_dump → gzip                    │
│  2. 保留最近 3 份，自动删除旧文件                            │
│  3. 写入日志 → /home/schan/backups/tts_erp_pgdump/         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  备份存储: /home/schan/backups/tts_erp_pgdump/             │
│  文件格式: tts_erp_YYYYMMDD_HHMMSS.sql.gz                 │
│  保留策略: 最近 3 份（约 3 小时覆盖）                        │
│  日志: pgbackup.log                                         │
└─────────────────────────────────────────────────────────────┘
```

## 文件清单

| 文件 | 用途 |
|------|------|
| `scripts/backup/pg_dump_hourly.sh` | 备份脚本（dump + 轮转 + 日志） |
| `scripts/systemd/tts-erp-pgbackup.service` | systemd service 单元 |
| `scripts/systemd/tts-erp-pgbackup.timer` | systemd timer 单元 |
| `/home/schan/backups/tts_erp_pgdump/` | 备份文件存储目录 |
| `/home/schan/backups/tts_erp_pgdump/pgbackup.log` | 备份操作日志 |

## 配置参数

脚本顶部可配置：

```bash
BACKUP_DIR="/home/schan/backups/tts_erp_pgdump"  # 存储目录
KEEP_COUNT=3                                       # 保留份数
DB_NAME="tts_erp"                                  # 数据库名
CONTAINER="postgres"                               # Docker 容器名
```

环境变量覆盖：

```bash
BACKUP_DIR=/path/to/dir KEEP_COUNT=5 bash scripts/backup/pg_dump_hourly.sh
```

## 运维操作

### 手动触发备份

```bash
bash scripts/backup/pg_dump_hourly.sh
```

### 查看定时器状态

```bash
systemctl --user status tts-erp-pgbackup.timer
systemctl --user list-timers | grep pgbackup
```

### 查看备份日志

```bash
tail -20 /home/schan/backups/tts_erp_pgdump/pgbackup.log
```

### 查看已有备份

```bash
ls -lh /home/schan/backups/tts_erp_pgdump/*.sql.gz
```

### 恢复数据库

```bash
# 从最近备份恢复
gunzip -c /home/schan/backups/tts_erp_pgdump/tts_erp_*.sql.gz | docker exec -i postgres psql -U postgres tts_erp

# 从指定备份恢复
gunzip -c /home/schan/backups/tts_erp_pgdump/tts_erp_20260912_060721.sql.gz | docker exec -i postgres psql -U postgres tts_erp
```

### 暂停/恢复定时备份

```bash
# 暂停
systemctl --user stop tts-erp-pgbackup.timer

# 恢复
systemctl --user start tts-erp-pgbackup.timer
```

### 修改保留份数

编辑 `scripts/backup/pg_dump_hourly.sh` 中的 `KEEP_COUNT` 变量。

## 性能指标

| 指标 | 值 |
|------|-----|
| tts_erp 库大小 | ~1GB |
| 备份耗时 | ~20 秒 |
| 压缩后大小 | ~62MB |
| 压缩率 | ~94% |
| 对业务影响 | 无（MVCC 快照，不阻塞 DML） |

## 注意事项

1. **pg_dump 不锁表**：使用 MVCC 快照读取，不阻塞 INSERT/UPDATE/DELETE
2. **磁盘占用**：每份 ~62MB，3 份 ≈ 186MB，可忽略
3. **时区**：备份文件名使用 UTC 时间戳
4. **日志累积**：`pgbackup.log` 会持续增长，定期清理或 logrotate
5. **容器依赖**：备份脚本依赖 `postgres` 容器运行中

## 扩展建议

如需更长保留期或异地备份：

- 增大 `KEEP_COUNT`（如 24 份 = 1 天覆盖）
- 添加 rsync 到远程服务器
- 开启 WAL 归档实现时间点恢复（PITR）
