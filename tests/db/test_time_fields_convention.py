"""DB 双时间字段约定测试(ADR-0001)。

验证 v2 schema 全部 42 张表满足:
1. 每张表有"创建时间"列(created_at / synced_at / captured_at / received_at / started_at)
2. 每张表有"更新时间"列(updated_at)+ BEFORE UPDATE trigger
3. trigger function `public.fn_touch_updated_at()` 自动维护 updated_at
4. 关键列都有 COMMENT ON COLUMN 业务语义

测试用真实 DB 验证,不在沙箱,需要 db session fixture。
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.db.base import get_engine

# v2 schema 清单(per ADR-0001 §1.1 audit)
V2_SCHEMAS = (
    "commerce",
    "analytics",
    "integration",
    "linkage",
    "procurement",
    "after_sales",
    "finance",
    "fulfillment",
    "reporting",
    "security",
)

# 创建时间列(多选一,各表允许不同名)
CREATE_TIME_COLS = (
    "created_at",
    "synced_at",
    "captured_at",
    "received_at",
    "inserted_at",
    "started_at",
    "detected_at",  # integration.sync_issues
)


def _all_v2_tables(sess: Session) -> list[tuple[str, str]]:
    """列出所有 v2 schema 的表(不含 view / mat view)。"""
    rows = sess.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = ANY(:schemas)
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        ),
        {"schemas": list(V2_SCHEMAS)},
    ).all()
    return [(r[0], r[1]) for r in rows]


def _table_columns(sess: Session, schema: str, table: str) -> set[str]:
    rows = sess.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            """
        ),
        {"schema": schema, "table": table},
    ).all()
    return {r[0] for r in rows}


def _table_triggers(sess: Session, schema: str, table: str) -> list[dict]:
    rows = sess.execute(
        text(
            """
            SELECT trigger_name, action_timing, event_manipulation
            FROM information_schema.triggers
            WHERE event_object_schema = :schema AND event_object_table = :table
            """
        ),
        {"schema": schema, "table": table},
    ).all()
    return [
        {"name": r[0], "timing": r[1], "event": r[2]} for r in rows
    ]


def _table_column_comments(sess: Session, schema: str, table: str) -> dict[str, str]:
    rows = sess.execute(
        text(
            """
            SELECT a.attname, d.description
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_description d
              ON d.objoid = c.oid AND d.objsubid = a.attnum
            WHERE n.nspname = :schema
              AND c.relname = :table
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ),
        {"schema": schema, "table": table},
    ).all()
    return {r[0]: (r[1] or "").strip() for r in rows}


# ─── 测试 ───


def test_touch_updated_at_function_exists():
    """通用 trigger function `public.fn_touch_updated_at()` 必须存在。"""
    eng = get_engine()
    with Session(eng) as sess:
        row = sess.execute(
            text(
                "SELECT 1 FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = 'fn_touch_updated_at'"
            )
        ).first()
    assert row is not None, (
        "public.fn_touch_updated_at() 触发器函数不存在;需先跑 migration 0001"
    )


def test_every_v2_table_has_create_time():
    """每张 v2 表必须有创建时间列(命名允许不同)。"""
    eng = get_engine()
    with Session(eng) as sess:
        bad: list[str] = []
        for schema, table in _all_v2_tables(sess):
            cols = _table_columns(sess, schema, table)
            if not (cols & set(CREATE_TIME_COLS)):
                bad.append(f"{schema}.{table}: cols={sorted(cols)}")
    assert not bad, f"v2 表缺创建时间列: {bad}"


def test_every_v2_table_has_updated_at():
    """每张 v2 表必须有 updated_at 列。"""
    eng = get_engine()
    with Session(eng) as sess:
        bad: list[str] = []
        for schema, table in _all_v2_tables(sess):
            cols = _table_columns(sess, schema, table)
            if "updated_at" not in cols:
                bad.append(f"{schema}.{table}")
    assert not bad, f"v2 表缺 updated_at: {bad}"


def test_every_v2_table_has_update_trigger():
    """每张 v2 表必须有 BEFORE UPDATE trigger 自动维护 updated_at。"""
    eng = get_engine()
    with Session(eng) as sess:
        bad: list[str] = []
        for schema, table in _all_v2_tables(sess):
            triggers = _table_triggers(sess, schema, table)
            has_touch = any(
                t["timing"] == "BEFORE" and t["event"] == "UPDATE"
                for t in triggers
            )
            if not has_touch:
                bad.append(f"{schema}.{table}")
    assert not bad, f"v2 表缺 BEFORE UPDATE 触发器: {bad}"


def test_updated_at_changes_on_update(db_session):
    """UPDATE 任意 v2 表的行,updated_at 应当被 trigger 刷新为 now()。"""
    from tts_erp_v2.db.models.commerce import SalesOrder

    # 用 sync_cursors 临时插入 + UPDATE + 检查 updated_at
    import time

    test_job = "test.touch_updated_at"
    test_scope = "test_scope"
    db_session.execute(
        text("DELETE FROM integration.sync_cursors WHERE job_name = :j"),
        {"j": test_job},
    )
    db_session.execute(
        text(
            "INSERT INTO integration.sync_cursors (job_name, scope, cursor_value) "
            "VALUES (:j, :s, 'init')"
        ),
        {"j": test_job, "s": test_scope},
    )
    db_session.commit()
    first = db_session.execute(
        text("SELECT updated_at FROM integration.sync_cursors "
             "WHERE job_name = :j AND scope = :s"),
        {"j": test_job, "s": test_scope},
    ).scalar()
    assert first is not None

    time.sleep(0.1)  # 确保 now() 递增

    db_session.execute(
        text(
            "UPDATE integration.sync_cursors SET cursor_value = :v "
            "WHERE job_name = :j AND scope = :s"
        ),
        {"v": "updated", "j": test_job, "s": test_scope},
    )
    db_session.commit()

    second = db_session.execute(
        text("SELECT updated_at FROM integration.sync_cursors "
             "WHERE job_name = :j AND scope = :s"),
        {"j": test_job, "s": test_scope},
    ).scalar()
    assert second > first, (
        f"updated_at 没在 UPDATE 后刷新: before={first}, after={second}"
    )

    # cleanup
    db_session.execute(
        text("DELETE FROM integration.sync_cursors WHERE job_name = :j"),
        {"j": test_job},
    )
    db_session.commit()


def test_critical_columns_have_comments():
    """关键时间列必须有 COMMENT ON COLUMN(语义清晰)。"""
    eng = get_engine()
    critical_cols = {
        ("commerce", "sales_orders", "synced_at"),
        ("commerce", "sales_orders", "channel_account_id"),
        ("integration", "sync_cursors", "updated_at"),
        ("integration", "raw_records", "captured_at"),
        ("analytics", "ad_raw", "endpoint"),
    }
    with Session(eng) as sess:
        bad: list[str] = []
        for schema, table, col in critical_cols:
            comments = _table_column_comments(sess, schema, table)
            if not comments.get(col):
                bad.append(f"{schema}.{table}.{col}")
    assert not bad, f"关键列缺 COMMENT ON COLUMN: {bad}"
