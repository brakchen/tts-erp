# sync_cron.py / run_sync_cron.sh — 已归档

> ⚠️ **ARCHIVED（2026-09-03）** —— v1 cron 同步脚本,已被 `tts-erp-sync.service`
> (APScheduler) 取代。脚本内 `sync_cron.py:447-449` 自注 superseded。
>
> **归档位置**:`tech-doc/_archive/sync-cron-legacy-2026-08/`
>
> - crontab 已无 `*/10 * * * *` 调用（`crontab -l` 返回空,2026-09-03 核）
> - `pgrep sync_cron.py` 无在跑实例
> - `/tmp/tts-erp-sync.lock` stale 锁文件已清理
>
> v2 同步由 `tts-erp-sync.service` (systemd user unit) 调度,
> 任务清单见 `tts_erp_v2/sync_worker/scheduler.py::JOBS`。
> 详见 `AGENTS.md §6`(`sync_cron.py` 标 legacy,观察期后删)
> 与 `AGENTS.md §7.1`(sync-worker 进程托管)。
