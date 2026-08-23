-- analytics_sync retention cron
--
-- Recommended crontab (operator's choice of scheduler):
--
--   # Daily 03:00 UTC — purge analytics_records older than 90 days
--   0 3 * * * docker exec -i postgres psql -U postgres -d tts_erp \
--       -c "$(cat /home/schan/tts-erp/analytics_sync/retention.sql | head -1)"
--
--   # Daily 04:00 UTC — purge analytics_audit_log older than 30 days
--   0 4 * * * docker exec -i postgres psql -U postgres -d tts_erp \
--       -c "$(cat /home/schan/tts-erp/analytics_sync/retention.sql | sed -n '5p')"
--
-- See tech-doc/compatibility.md §2 for retention rationale.

DELETE FROM analytics_records
WHERE received_at < now() - INTERVAL '90 days';

DELETE FROM analytics_audit_log
WHERE created_at < now() - INTERVAL '30 days';

-- analytics_cursors, analytics_shop_timezones, analytics_sync_tokens are
-- retained indefinitely — see tech-doc/compatibility.md for the rationale.
