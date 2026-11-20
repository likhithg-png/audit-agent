-- Generic key-value store for app-wide toggles (e.g. "auto-reply to a
-- clarifying question without a human click"). Persisted, not an in-memory
-- flag -- this runs against a real mailbox, so a setting silently resetting
-- to its default on every API restart would be a real surprise in prod.
CREATE TABLE IF NOT EXISTS audit.app_settings (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
