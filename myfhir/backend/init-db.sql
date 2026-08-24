-- Ensure the three MyHealth databases exist on the shared PostgreSQL instance.
-- myhealth_auth is created automatically by POSTGRES_DB; the other two are created
-- here. Tables (oauth_tokens, eob, claim_submission, diagnostic_report, lab_result,
-- pkce_verifiers, job_run, ...) are auto-created by the backend on first startup
-- (db.init_db → SQLAlchemy create_all), so no DDL is needed in this file.

SELECT 'CREATE DATABASE myhealth_auth'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'myhealth_auth') \gexec

SELECT 'CREATE DATABASE myhealth_anthem'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'myhealth_anthem') \gexec

SELECT 'CREATE DATABASE myhealth_ucla'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'myhealth_ucla') \gexec