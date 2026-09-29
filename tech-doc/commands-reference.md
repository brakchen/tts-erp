# tts-erp 命令参考

> 本文档包含 tts-erp 项目的常用命令和测试规范。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 测试命令

```bash
# 日常全量测试（唯一入口；自动 source .env.test 切到 tts_erp_v3_test；migration 域已归档勿跑）
bash scripts/test.sh fast

# 跳过 .env.test source（仅迁移/手动调试用；默认禁走）
TTS_ERP_TEST_OFF=1 bash scripts/test.sh fast

# 单域测试（如 tests/miaoshou/、tests/jobs_tiktok/）
# worktree 内无 .venv，改用 /home/schan/tts-erp/.venv/bin/pytest（见 §11）
.venv/bin/pytest tests/<domain>/ -q
```

## 2. 数据导入

```bash
# 看 import plan（37 张表 + credentials + api_keys）
bash scripts/import_prod_to_test.sh --dry-run

# 实际把 prod 数据导入 tts_erp_v3_test（multi-pass FK）
bash scripts/import_prod_to_test.sh --yes

# 不拷 credentials（部分 FK 表会空）
bash scripts/import_prod_to_test.sh --exclude-credentials --yes
```

## 3. 服务管理

```bash
# 重启 API = systemctl --user restart tts-erp.service
bash restart.sh

# 改了 jobs/ 或 sync_worker/ 后必须单独跑
systemctl --user restart tts-erp-sync.service
```

## 4. 端到端测试

```bash
# 端到端冒烟（需 :9877 在跑）
python3 test_e2e.py / test_e2e_finance.py

# 7 步生产冒烟（healthz/auth/v2 读端点/page/sync_jobs）
bash prod-switch/postswitch-smoke.sh
```

## 5. 监控和日志

```bash
# 进程状态
systemctl --user status tts-erp{,-sync}.service

# systemd 日志
journalctl --user -u tts-erp -n 50

# 业务日志：logs/stderr.log 抓 traceback
# watchdog 巡检发现看 logs/watchdog.log（约定 agent 定时扫，代告警）
```

## 6. Schema 变更

```bash
# 流程：改 tts_erp_v2/db/models/ → 重新生成 → 应用
python3 scripts/regen_schema.py
# 生成 schema_tts_erp.sql（IF NOT EXISTS 幂等兼容老库）
```

## 7. API key 管理

```bash
# key 管理 CLI
python3 api_keys.py create/list/revoke/rotate
# --prefix 定位；明文只创建时打印一次
```

## 8. 签名调试

```bash
# TikTok 签名调试
TTS_DEBUG_SIGN=1

# 妙手签名调试
MIAOSHOU_DEBUG_SIGN=1
# 在 stderr 打 canonical
```

## 9. 测试规范

- **TDD**：先写测试再实现
- **共享 fixtures**：在 `tests/conftest.py`（事务回滚隔离、`TEST_%` 哨兵数据）
- **测试环境隔离（2026-09-07）**：所有测试默认连 `tts_erp_v3_test`（专用 test db，已 schema 一致）
  - `bash scripts/test.sh` 自动 source `.env.test`（gitignored）切到 test db
  - prod API service / `uvicorn` 本地启动仍读 `.env` 连 prod `tts_erp`，**零变更**
  - 安全护栏：tests/conftest.py 检测到 `TTS_ERP_DB_URL` 指向 prod-shape dbname（`tts_erp` / `tts_erp_prod`）会往 stderr 打 WARNING
  - scripts/test.sh 会在 .env.test 缺失时直接退出
  - 需要 prod-shaped 数据时运行 `bash scripts/import_prod_to_test.sh --yes`
- **收尾标准**：跑不过 0 fail 不收尾
