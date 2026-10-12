-- Current-schema operational verification. Read-only; prints aggregates and metadata only.
\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;

SELECT operator, line_type, count(*)
FROM public.packages GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) AS total, count(DISTINCT id) AS distinct_ids,
       count(*) FILTER (WHERE embedding IS NULL) AS missing_embeddings,
       count(DISTINCT embedding_model) AS embedding_models,
       count(*) FILTER (
           WHERE doc_hash IS DISTINCT FROM encode(sha256(convert_to(doc_text, 'UTF8')), 'hex')
       ) AS bad_doc_hashes
FROM public.packages;
SELECT vector_dims(embedding) AS dimensions, count(*)
FROM public.packages WHERE embedding IS NOT NULL GROUP BY 1;
SELECT count(*) AS usage_rows FROM public.message_usage;
SELECT count(*) AS reservation_rows FROM public.quota_reservation;
SELECT count(*) FILTER (WHERE NOT is_anonymous) AS permanent_auth_users,
       count(*) FILTER (WHERE is_anonymous) AS anonymous_auth_users
FROM auth.users;
SELECT conname, convalidated
FROM pg_constraint
WHERE conrelid = 'public.packages'::regclass
ORDER BY conname;

SELECT tablename, policyname, roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('packages', 'message_usage', 'quota_reservation')
ORDER BY tablename, policyname;

SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('anon', 'authenticated', 'tarifeai_backend')
  AND table_name IN ('packages', 'message_usage', 'quota_reservation')
ORDER BY grantee, table_name, privilege_type;

SELECT has_table_privilege('tarifeai_backend', 'public.packages', 'SELECT') AS packages_select,
       has_table_privilege('tarifeai_backend', 'public.packages', 'INSERT,UPDATE,DELETE,TRUNCATE') AS packages_write,
       has_table_privilege('tarifeai_backend', 'public.message_usage', 'SELECT,INSERT,DELETE') AS usage_required,
       has_table_privilege('tarifeai_backend', 'public.message_usage', 'UPDATE,TRUNCATE') AS usage_excess;

SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
       rolreplication, rolbypassrls
FROM pg_roles WHERE rolname = 'tarifeai_backend';

SELECT jobname, schedule, active
FROM cron.job WHERE jobname = 'tarifeai-usage-retention-daily';

ROLLBACK;
