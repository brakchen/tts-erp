# scripts/migrate_v1_to_v2/ — 已归档

> ⚠️ **ARCHIVED（2026-09-03）** —— v1 → v2 数据迁移已随 2026-08-29 切流执行完毕。
> 一次性脚本，无后续用途。保留仅作历史过程记录。
>
> **归档位置**：`tech-doc/_archive/migrate-v1-to-v2-2026-08-29/scripts/`
> **对应测试**：`tech-doc/_archive/migrate-v1-to-v2-2026-08-29/tests/`（含 `tests/migration/`）
>
> 不要再跑这些脚本——08-31 曾因此把生产凭证回退成 legacy 格式、全线停摆 22h。
> 即便设了 `TTS_ERP_ALLOW_PROD_MIGRATION=1`（闸已随归档目录一起搬走），
> 这些脚本写入的 `public.*` legacy 表也不再被 v2 服务读（见
> `tech-doc/external-api.md` 与 `AGENTS.md §2.1`）。
>
> 完整背景：
>
> - `AGENTS.md §4` — "不要跑 tests/migration/ 域测试或 scripts/migrate_v1_to_v2/ 脚本"
> - `tech-doc/test-domains.md` — migration 域测试的 skip/fixture/env var 三层闸
> - `CHANGELOG.md:286` — 2026-08-27 Review remediation Waves 1-4 收尾
