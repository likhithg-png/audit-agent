"""Tiny persisted key-value store (audit.app_settings) for app-wide toggles.
Persisted rather than an in-memory flag so a setting survives an API restart --
this runs against a real mailbox, not just a demo, so "silently reset to the
default" would be a real surprise."""
from . import db


def get_bool(key: str, default: bool) -> bool:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM audit.app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return default if row is None else row["value"] == "true"


def set_bool(key: str, value: bool) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit.app_settings (key, value, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            (key, "true" if value else "false"),
        )
        conn.commit()
