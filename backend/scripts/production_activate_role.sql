-- Run only after a backend host and secure secret store exist. Keep the grant-bearing
-- tarifeai_backend role NOLOGIN; create a separate constrained LOGIN that inherits it.
-- PostgreSQL applies grants and RLS policies for roles inherited by the session user.
\set ON_ERROR_STOP on
\prompt -s 'New password for tarifeai_backend_login: ' backend_password
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tarifeai_backend_login') THEN
        CREATE ROLE tarifeai_backend_login LOGIN INHERIT
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE tarifeai_backend_login WITH LOGIN INHERIT PASSWORD :'backend_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT tarifeai_backend TO tarifeai_backend_login;
\unset backend_password
