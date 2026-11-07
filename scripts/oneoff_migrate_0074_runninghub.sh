#!/usr/bin/env bash
# Human-operated RunningHub migration on HP desk. Run from the tts-erp checkout.
# Usage: bash scripts/oneoff_migrate_0074_runninghub.sh
# The production guard is enabled only after the operator confirms tts_erp.
set -euo pipefail

repo="$HOME/tts-erp"
runtime_env="$HOME/.config/tts-erp/runtime.env"
release=06a28b2c6b1856057e1138162bd7def0d4b2c248

if ! ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fx '192.168.1.2' >/dev/null; then
    echo '请在 HP desk（192.168.1.2）上执行。' >&2
    exit 1
fi
if [[ ! -x "$repo/.venv/bin/python" || ! -r "$runtime_env" ]]; then
    echo '缺少项目虚拟环境或 tts-erp/runtime.env。' >&2
    exit 1
fi

git -C "$repo" fetch origin refs/heads/master:refs/remotes/origin/master
git -C "$repo" merge-base --is-ancestor "$release" refs/remotes/origin/master
scratch="$(mktemp -d /tmp/tts-erp-runninghub-0074.XXXXXXXX)"
checkout="$scratch/checkout"
cleanup() {
    if [[ -d "$checkout" ]]; then
        git -C "$repo" worktree remove "$checkout" ||
            printf '请检查临时工作区：%s\n' "$checkout" >&2
    fi
    rmdir -- "$scratch" 2>/dev/null || true
}
trap cleanup EXIT
git -C "$repo" worktree add --detach "$checkout" "$release"
cd -- "$checkout"

"$repo/.venv/bin/python" - "$runtime_env" <<'PY'
from __future__ import annotations

import os
import sys

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

BEFORE = "0073_media_assets"
TARGET = "0074_runninghub"
TABLES = {
    "workflow_versions", "profiles", "templates", "batches", "jobs",
    "quotas", "attempts", "asset_uploads", "artifacts",
}


class Stop(Exception):
    pass


def inspect(engine: Engine) -> str:
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        name, version_table = conn.exec_driver_sql(
            "SELECT current_database(), "
            "to_regclass('alembic_version') = to_regclass('public.alembic_version')"
        ).one()
        if name != "tts_erp" or not version_table:
            raise Stop("数据库身份或迁移版本表不匹配。")
        revisions = conn.exec_driver_sql(
            "SELECT version_num FROM public.alembic_version"
        ).scalars().all()
        if revisions == [BEFORE]:
            if conn.exec_driver_sql("SELECT to_regnamespace('runninghub') IS NOT NULL").scalar_one():
                raise Stop("版本仍是 0073，但已存在 runninghub schema，请先核对。")
            return BEFORE
        if revisions != [TARGET]:
            raise Stop(f"当前版本为 {revisions}，只允许 {BEFORE} → {TARGET}。")
        tables = set(conn.exec_driver_sql(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='runninghub'"
        ).scalars())
        roles = set(conn.exec_driver_sql(
            "SELECT role_code FROM security.role_permissions WHERE permission_code='page:runninghub'"
        ).scalars())
        if not TABLES <= tables or not {"admin", "operator"} <= roles:
            raise Stop("已登记 0074，但 RunningHub 表或菜单授权校验未通过。")
        return TARGET


def main() -> None:
    raw = dotenv_values(sys.argv[1], interpolate=False).get("TTS_ERP_DB_URL")
    if not raw:
        raise Stop("runtime.env 中没有 TTS_ERP_DB_URL。")
    url = make_url(raw)
    if (
        url.get_backend_name() != "postgresql" or url.host != "127.0.0.1"
        or (url.port or 5432) != 5432 or url.database != "tts_erp" or url.query
    ):
        raise Stop("runtime.env 未指向 HP desk 的 127.0.0.1:5432/tts_erp。")
    url = url.set(drivername="postgresql+psycopg")
    engine = create_engine(url, connect_args={"connect_timeout": 8}, hide_parameters=True)
    try:
        current = inspect(engine)
        print(f"HP desk 业务库 tts_erp：当前 {current}，目标 {TARGET}", flush=True)
        if current == BEFORE:
            with open("/dev/tty", encoding="utf-8") as terminal:
                print("输入 tts_erp 确认执行升级：", end="", flush=True)
                if terminal.readline().strip() != "tts_erp":
                    raise Stop("已取消。")
            if inspect(engine) == BEFORE:
                os.environ["TTS_ERP_DB_URL"] = url.render_as_string(hide_password=False)
                os.environ["ALLOW_PROD_DESTRUCTIVE"] = "1"
                try:
                    command.upgrade(Config("alembic.ini"), TARGET)
                finally:
                    os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
        if inspect(engine) != TARGET:
            raise Stop(f"未达到目标版本 {TARGET}。")
        print(f"完成：{TARGET}，9 张 RunningHub 表和菜单授权校验通过。")
    finally:
        engine.dispose()


try:
    main()
except Stop as error:
    print(error, file=sys.stderr)
    raise SystemExit(1) from None
except Exception as error:
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    print(f"升级退出：{type(error).__name__}，SQLSTATE={sqlstate or '未知'}。", file=sys.stderr)
    raise SystemExit(1) from None
PY
