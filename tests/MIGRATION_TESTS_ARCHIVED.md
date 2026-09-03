# tests/migration/ — 已归档

> ⚠️ **ARCHIVED（2026-09-03）** —— `tests/migration/` 是 `scripts/migrate_v1_to_v2/`
> 的配套测试。两者一起归档：
>
> - **归档位置**：`tech-doc/_archive/migrate-v1-to-v2-2026-08-29/tests/`
> - **对应脚本**：`tech-doc/_archive/migrate-v1-to_v2-2026-08-29/scripts/`
>
> 这些测试会真实写生产库（`integration.credentials` 等），已随归档目录一起搬走。
> `pyproject.toml addopts` 的 `-m 'not domain_migration'` 现在已无对应模块，
> 可保留作"历史 skip marker"或后续移除。
>
> 详见 `tech-doc/test-domains.md` migration 章节（描述了 module-level skip +
> fixture-level env var + per-script guard 三层闸，已随归档走）。
