-- TarifeAI usage-log retention. Run daily by the operator (cron / Supabase
-- scheduled job); it is NOT run automatically by the app.
--   psql "<connection string>" -f scripts/usage_retention.sql
--
-- What the quota needs (app/quota.py):
--   * guest lifetime cap      -> anonymous rows by user_id, forever (no IP needed);
--   * guest per-IP daily cap  -> IPs of TODAY's anonymous rows only;
--   * signed-in daily cap     -> TODAY's signed-in rows only.
-- So after two days (UTC-day boundary plus slack) an IP address and a signed-in usage
-- row serve no purpose and are removed. Quota decisions are unchanged
-- (tests/test_quota_postgres.py::test_retention_keeps_every_quota_decision).
\set ON_ERROR_STOP on
BEGIN;
UPDATE message_usage SET ip = NULL
WHERE ip IS NOT NULL AND created_at < now() - interval '2 days';
DELETE FROM message_usage
WHERE is_anonymous = false AND created_at < now() - interval '2 days';
DELETE FROM quota_reservation WHERE expires_at <= now();
COMMIT;
