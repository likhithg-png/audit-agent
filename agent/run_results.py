"""
Persist the one part of a finished run that only ever lived in memory: the
drafted letter and who to reply to (audit.run_results).

Everything else /result returns is already written to out/<request_id>/ by
run_checks/pack/letter, so it can be re-read from disk. These two could not,
which meant a Gmail message sitting at NEEDS_REVIEW became unreachable after
an API restart -- the status row said "a human still needs to review this"
but the draft it pointed at was gone.
"""
from psycopg.types.json import Json

from . import db


def save_letter(request_id: str, letter: dict) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit.run_results (request_id, letter_text, needs_human_review, validation)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (request_id) DO UPDATE SET
                 letter_text = EXCLUDED.letter_text,
                 needs_human_review = EXCLUDED.needs_human_review,
                 validation = EXCLUDED.validation,
                 updated_at = now()""",
            (request_id, letter["text"], letter["needs_human_review"], Json(letter["validation"])),
        )
        conn.commit()


def save_reply_meta(request_id: str, reply_meta: dict) -> None:
    """Written when the run starts, not when it finishes -- a run that errors
    out still has a real sender we might want to reply to."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit.run_results
                 (request_id, reply_to, reply_subject, reply_thread_id, reply_in_reply_to)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (request_id) DO UPDATE SET
                 reply_to = EXCLUDED.reply_to,
                 reply_subject = EXCLUDED.reply_subject,
                 reply_thread_id = EXCLUDED.reply_thread_id,
                 reply_in_reply_to = EXCLUDED.reply_in_reply_to,
                 updated_at = now()""",
            (request_id, reply_meta.get("from"), reply_meta.get("subject"),
             reply_meta.get("thread_id"), reply_meta.get("message_id_header")),
        )
        conn.commit()


def mark_replied(request_id: str) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE audit.run_results SET replied = true, updated_at = now() WHERE request_id = %s",
                    (request_id,))
        conn.commit()


def reset_replied(request_id: str) -> None:
    """Called when a run restarts for this request_id (a human can click "Run
    again" on a message that's already REPLIED, e.g. after answering its own
    clarifying question). save_reply_meta()'s UPSERT only touches the reply_to/
    subject/thread columns -- without this, `replied` from a PREVIOUS run of
    this same request_id would carry over and wrongly look like the brand new
    run already sent something, before it's even started."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE audit.run_results SET replied = false, updated_at = now() WHERE request_id = %s",
                    (request_id,))
        conn.commit()


def load(request_id: str) -> dict | None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM audit.run_results WHERE request_id = %s", (request_id,))
        return cur.fetchone()


def reply_meta(request_id: str) -> dict | None:
    """Same shape gmail_client.send_reply expects, or None if this run has no
    originating email (a sample/upload run)."""
    row = load(request_id)
    if not row or not row.get("reply_to"):
        return None
    return {"from": row["reply_to"], "subject": row["reply_subject"],
            "thread_id": row["reply_thread_id"], "message_id_header": row["reply_in_reply_to"]}
