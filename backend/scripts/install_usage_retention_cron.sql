-- Install TarifeAI's daily privacy-retention job using Supabase's supported pg_cron.
-- Run as an administrator after the current baseline is installed. The job modifies only
-- message_usage and preserves the quota behavior documented in usage_retention.sql.
\set ON_ERROR_STOP on
BEGIN;
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.unschedule(jobid)
FROM cron.job
WHERE jobname = 'tarifeai-usage-retention-daily';

SELECT cron.schedule(
    'tarifeai-usage-retention-daily',
    '17 2 * * *',
    $job$
    UPDATE public.message_usage
       SET ip = NULL
     WHERE ip IS NOT NULL
       AND created_at < now() - interval '2 days';
    DELETE FROM public.message_usage
     WHERE NOT is_anonymous
       AND created_at < now() - interval '2 days';
    DELETE FROM public.quota_reservation
     WHERE expires_at <= now();
    $job$
);
COMMIT;
