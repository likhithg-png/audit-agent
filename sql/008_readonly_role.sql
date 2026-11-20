-- Read-only role for LLM-issued SQL (agent/decompose_agent.py's `run_sql` tool).
-- Previously the only guard was a string check in agent/tools.py (reject queries
-- that aren't SELECT/WITH, or that contain an insert/update/delete/drop/create/
-- alter/copy keyword) -- a text filter, not a real permission boundary. This role
-- makes read-only a database-enforced property: even a query that slips past the
-- text filter (or a future caller that skips it entirely) physically cannot write,
-- because the role has no INSERT/UPDATE/DELETE/DDL grant on anything.
--
-- NOLOGIN would be the obvious hardening but breaks psycopg's ability to connect
-- as this role directly, so it's a real LOGIN role with a fixed password -- fine
-- for a role that can only ever read.
CREATE ROLE audit_readonly WITH LOGIN PASSWORD 'audit_readonly_ro' NOSUPERUSER NOCREATEDB NOCREATEROLE;

GRANT CONNECT ON DATABASE audit TO audit_readonly;
GRANT USAGE ON SCHEMA audit TO audit_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA audit TO audit_readonly;

-- Covers tables created by later migrations too, not just what exists today.
ALTER DEFAULT PRIVILEGES IN SCHEMA audit GRANT SELECT ON TABLES TO audit_readonly;
