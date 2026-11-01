"""Upgrade shop onboarding schema using the project's configured database.

Windows: double-click upgrade-database.cmd in the repository root.
Use --check-only to inspect configuration without connecting to the database.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_REVISION = "0072_shop_app_credentials"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Configuration file, relative to the repository or absolute")
    parser.add_argument("--check-only", action="store_true", help="Show configuration and target revision; do not connect or migrate")
    args = parser.parse_args()
    env_file = args.env_file
    if env_file is None:
        env_file = PROJECT_ROOT / (".env.local" if (PROJECT_ROOT / ".env.local").exists() else ".env")
    elif not env_file.is_absolute():
        env_file = PROJECT_ROOT / env_file
    if not env_file.is_file():
        print(f"配置文件不存在：{env_file}", file=sys.stderr)
        return 1

    load_dotenv(env_file, override=True, interpolate=False)
    db_url = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not db_url:
        print("配置缺少 TTS_ERP_DB_URL，未执行迁移。", file=sys.stderr)
        return 1
    try:
        target = make_url(db_url)
        if target.get_backend_name() != "postgresql" or not target.host or not target.database:
            raise ValueError("invalid PostgreSQL configuration")
    except Exception:
        print("TTS_ERP_DB_URL 不是有效的 PostgreSQL 连接配置，未执行迁移。", file=sys.stderr)
        return 1

    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    ScriptDirectory.from_config(config).get_revision(TARGET_REVISION)
    print(f"配置文件：{env_file}")
    print(f"目标数据库：{target.host}:{target.port or 5432}/{target.database}")
    print(f"迁移目标：{TARGET_REVISION}", flush=True)
    if args.check_only:
        print("配置检查完成；没有连接数据库，也没有执行迁移。")
        return 0

    # Running this explicit upgrade launcher authorizes the existing migration
    # guard for this child process only; nothing is written back to env files.
    os.environ["ALLOW_PROD_DESTRUCTIVE"] = "1"
    try:
        command.upgrade(config, TARGET_REVISION)
    except Exception as exc:
        # SQLAlchemy exceptions can embed connection passwords or App parameters.
        # Keep those out of the double-click console.
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        code = f"，SQLSTATE={sqlstate}" if sqlstate else ""
        print(f"迁移失败：{type(exc).__name__}{code}。请根据错误类型排查后重新执行。", file=sys.stderr)
        print("请检查数据库连接、已有迁移版本和 TTS_ERP_FERNET_KEY 配置。", file=sys.stderr)
        return 1
    print("数据库迁移完成；可以启动或重启新版服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
