-- ============================================================================
-- tts-erp sync_log retention: 60-day rolling deletion
-- ----------------------------------------------------------------------------
-- 目的: sync_log 表只保留最近 60 天数据，防止长期累积。
--
-- 设计: PG trigger 不能做"每 60 天"周期任务,只能用事件驱动。
--       方案 = AFTER INSERT trigger (懒清理) + 每日 crontab 调 cleanup_sync_log()
--
-- 时间字段选择: 用 COALESCE(finished_at, started_at) —
--   - 正常完成的 sync: finished_at 是真结束时间
--   - 还在跑/挂了的 sync: finished_at 是 NULL,fallback 到 started_at
--   - 60 天还没结束的 sync 几乎一定是卡死,直接清掉
--
-- 部署: cat retention.sql | docker exec -i postgres psql -U postgres -d tts_erp
-- ============================================================================

-- 1) 清理函数 — trigger 和 crontab 都调它,逻辑单一真理源
CREATE OR REPLACE FUNCTION cleanup_sync_log(retention_days INT DEFAULT 60)
RETURNS TABLE(deleted_count BIGINT, cutoff_timestamp TIMESTAMPTZ) AS $$
DECLARE
    cutoff TIMESTAMPTZ;
    rows_deleted BIGINT;
BEGIN
    cutoff := now() - (retention_days || ' days')::INTERVAL;

    DELETE FROM sync_log
    WHERE COALESCE(finished_at, started_at) < cutoff;

    GET DIAGNOSTICS rows_deleted = ROW_COUNT;

    RETURN QUERY SELECT rows_deleted, cutoff;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_sync_log(INT) IS
'Delete sync_log rows older than retention_days. Returns (deleted_count, cutoff_ts).';


-- 2) AFTER INSERT trigger — 每次 sync_log 有新写入,顺手清老的
--    用 STATEMENT-level 而非 ROW-level:
--      - 一次 INSERT 不管插几行,DELETE 只跑一次
--      - 避免 _sync_* 一次写一行反复触发清理(虽然现在是单行,但保不齐以后批量)
--    用 AFTER 而非 BEFORE:
--      - AFTER STATEMENT trigger RETURN 值被忽略,不会因为误返 NULL 而 cancel 整条 INSERT
CREATE OR REPLACE FUNCTION trg_sync_log_retention_fn()
RETURNS TRIGGER AS $$
DECLARE
    rows_deleted BIGINT;
BEGIN
    DELETE FROM sync_log
    WHERE COALESCE(finished_at, started_at) < now() - INTERVAL '60 days';
    GET DIAGNOSTICS rows_deleted = ROW_COUNT;
    IF rows_deleted > 0 THEN
        RAISE NOTICE '[sync_log retention] cleaned % old row(s)', rows_deleted;
    END IF;
    RETURN NULL;  -- AFTER STATEMENT trigger 忽略返回值
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_log_retention ON sync_log;
CREATE TRIGGER trg_sync_log_retention
    AFTER INSERT ON sync_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION trg_sync_log_retention_fn();

COMMENT ON TRIGGER trg_sync_log_retention ON sync_log IS
'After every INSERT, delete sync_log rows older than 60 days (lazily).';


-- 3) 验证 trigger 已装上
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_sync_log_retention'
    ) THEN
        RAISE EXCEPTION 'trg_sync_log_retention not created';
    END IF;
    RAISE NOTICE 'sync_log 60-day retention: trigger + cleanup function installed OK';
END $$;
