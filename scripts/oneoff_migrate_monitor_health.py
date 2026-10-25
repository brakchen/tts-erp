"""Human-only 0069 -> 0070: check, confirm, full backup, guarded Alembic, verify."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

SOURCE = "0069_video_publish_confirmation"
TARGET = "0070_monitor_health"
ROOT = Path(__file__).resolve().parents[1]
SSL_ENV = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT",
}
# Static named keys only; never enumerate the operator/runner environment.
PG_OVERRIDES = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGUSER",
    "PGDATABASE",
    "PGPASSWORD",
    "PGPASSFILE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGOPTIONS",
    "PGTARGETSESSIONATTRS",
    "PGLOADBALANCEHOSTS",
    "PGCHANNELBINDING",
    "PGGSSENCMODE",
    "PGREQUIRESSL",
    "PGSSLNEGOTIATION",
    "PGCLIENTENCODING",
    *SSL_ENV.values(),
)
EXPECTED = {
    "shop_id": ("VARCHAR(128)", False),
    "shop_name": ("VARCHAR(200)", False),
    "state": ("JSONB", False),
}


class OperatorError(RuntimeError):
    """Only fixed, credential-free messages may cross the CLI boundary."""


def read_db_url(path: Path) -> URL:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise OperatorError("配置须为私有普通文件（如 600），不能是符号链接。")
    # python-dotenv (BSD-3-Clause) is an existing uvicorn[standard] dependency.
    values = dotenv_values(path, interpolate=False)
    if any(os.environ.get(k) or values.get(k) for k in PG_OVERRIDES):
        raise OperatorError("拒绝额外 libpq 设置；请仅使用明确的 DB URL。")
    try:
        url = make_url(values.get("TTS_ERP_DB_URL") or "")
        fields = (url.host, url.username, url.password, url.database)
        if url.drivername not in {"postgresql", "postgresql+psycopg"} or None in fields:
            raise ValueError
        host_ok = re.fullmatch(r"[A-Za-z0-9_.:-]+", url.host or "")
        db_ok = re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", url.database or "")
        if not host_ok or not db_ok or set(url.query) - SSL_ENV.keys():
            raise ValueError
        if any(not isinstance(v, str) for v in url.query.values()):
            raise ValueError
        if any(
            ord(c) < 32 or ord(c) == 127
            for v in (*fields, *url.query.values())
            for c in str(v)
        ):
            raise ValueError
        for key in ("sslrootcert", "sslcert", "sslkey"):
            if key in url.query and not Path(str(url.query[key])).is_absolute():
                raise ValueError
        return url.set(drivername="postgresql+psycopg", port=url.port or 5432)
    except (ArgumentError, ValueError, TypeError):
        raise OperatorError("DB URL 身份不明确或选项不支持，停止。") from None


def probe(url: URL) -> str:
    engine = create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": 10}
    )
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            identity = conn.exec_driver_sql(
                "SELECT current_database(), current_schema()"
            )
            if identity.one() != (url.database, "public"):
                raise OperatorError("实际连接身份或默认 schema 不符，停止。")
            rows = conn.exec_driver_sql(
                "SELECT version_num FROM public.alembic_version"
            )
            revisions = rows.scalars().all()
            if len(revisions) != 1:
                raise OperatorError("迁移版本不是单一版本，停止。")
            revision = revisions[0]
            inspector = inspect(conn)
            exists = inspector.has_table("monitor_health", schema="plugin")
            if revision == SOURCE and exists:
                raise OperatorError("0069 与已存在的 monitor_health 表不一致，停止。")
            if revision == TARGET:
                if not exists:
                    raise OperatorError("0070 缺少 monitor_health 表，停止。")
                columns = inspector.get_columns("monitor_health", schema="plugin")
                actual = {c["name"]: (str(c["type"]), c["nullable"]) for c in columns}
                pk = inspector.get_pk_constraint("monitor_health", schema="plugin")
                if actual != EXPECTED or pk["constrained_columns"] != ["shop_id"]:
                    raise OperatorError("0070 表结构或主键不符；不会自动修复或回退。")
            return str(revision)
    finally:
        engine.dispose()


def _child_env() -> dict[str, str]:
    keys = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    return {k: os.environ[k] for k in keys if k in os.environ}


def backup(url: URL, directory: Path) -> Path:
    tools = {name: shutil.which(name) for name in ("pg_dump", "pg_restore")}
    if not all(tools.values()):
        raise OperatorError("缺少本机 pg_dump/pg_restore；不自动改用 Docker。")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="monitor-health-0070-", suffix=".dump", dir=directory
    )
    os.close(fd)
    destination = Path(name)
    try:
        with tempfile.TemporaryDirectory(prefix="monitor-health-pgpass-") as temp:
            passfile = Path(temp) / "pgpass"
            fd = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            fields = (url.host, str(url.port), url.database, url.username, url.password)
            escaped = [str(v).replace("\\", "\\\\").replace(":", "\\:") for v in fields]
            with os.fdopen(fd, "w") as stream:
                stream.write(":".join(escaped) + "\n")
            env = _child_env()
            env.update(
                PGHOST=str(url.host),
                PGPORT=str(url.port),
                PGDATABASE=str(url.database),
                PGUSER=str(url.username),
                PGPASSFILE=str(passfile),
                PGCONNECT_TIMEOUT="10",
            )
            env.update({SSL_ENV[k]: str(v) for k, v in url.query.items()})
            cmd = [
                str(tools["pg_dump"]),
                "--format=custom",
                "--no-password",
                "--file",
                str(destination),
            ]
            subprocess.run(cmd, env=env, check=True, capture_output=True, timeout=1800)
            if destination.stat().st_size == 0:
                raise OperatorError("备份为空，未执行迁移。")
            cmd = [str(tools["pg_restore"]), "--list", str(destination)]
            subprocess.run(cmd, env=env, check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError, OperatorError):
        raise OperatorError("备份/核验失败，未迁移；可能留有未完成 dump。") from None
    return destination


def upgrade(url: URL) -> None:
    # DDL remains owned by alembic/env.py -> require_destructive_script_guard.
    # Only this HUMAN-confirmed child gets an opt-in, never the parent environment.
    env = _child_env()
    env.update(
        TTS_ERP_DB_URL=url.render_as_string(hide_password=False),
        ALLOW_PROD_DESTRUCTIVE="1",
    )
    cmd = [sys.executable, "-m", "alembic", "upgrade", TARGET]
    try:
        subprocess.run(
            cmd, cwd=ROOT, env=env, check=True, capture_output=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError):
        raise OperatorError(
            "迁移状态未确认；请核对版本与备份，不自动重试或恢复。"
        ) from None


def run(args: argparse.Namespace) -> int:
    url = read_db_url(args.env_file)
    print(f"目标：{url.host}:{url.port}/{url.database}")
    revision = probe(url)
    if revision not in {SOURCE, TARGET}:
        raise OperatorError("数据库版本不在允许的 0069/0070 范围，停止。")
    if revision == TARGET:
        print("已经是 0070，表结构核验通过；无需迁移。")
        return 0
    if not args.apply:
        print("只读检查通过；人工使用 --apply 后先备份再升级 0070。")
        return 0
    if (
        not sys.stdin.isatty()
        or input("输入上述库名确认（其他输入取消）：") != url.database
    ):
        raise OperatorError("未获得终端人工确认；没有备份或迁移。")
    destination = backup(url, args.backup_dir)
    print(f"完整备份及 archive 列举核验通过：{destination}")
    if probe(url) != SOURCE:
        raise OperatorError("备份后版本发生变化，停止；不会自动重试。")
    upgrade(url)
    if probe(url) != TARGET:
        raise OperatorError("迁移后版本未确认是 0070，请人工排查。")
    print("0070 迁移及结构核验完成；没有重启服务或修改运行配置。")
    return 0


def _ensure_clean_repo() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if (
        result.stdout.strip()
        or not (ROOT / "alembic/versions/0070_monitor_health.py").is_file()
    ):
        raise OperatorError("须从包含 0070 且无本地改动的干净发布目录执行。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="人工 0070 迁移；默认仅检查，不重启服务"
    )
    parser.add_argument("--apply", action="store_true", help="人工终端确认后备份并升级")
    parser.add_argument(
        "--env-file", type=Path, default=Path("/home/schan/tts-erp/.env")
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/home/schan/backups/tts_erp_monitor_health_0070"),
    )
    args = parser.parse_args(argv)
    try:
        _ensure_clean_repo()
        return run(args)
    except OperatorError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:  # noqa: BLE001
        # Single CLI redaction boundary: never expose arbitrary driver/parser errors.
        print(
            "操作未确认完成：请核对配置、连接、权限和版本；未自动恢复。",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
