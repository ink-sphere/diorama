"""Store ordered per-edition observations and read consistent activity snapshots."""

import json
import sqlite3
import time

from diorama.agent.preparation import PreparationUpdate
from diorama.agent.trace import TraceEvent


def migrate(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS ingestion_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT NOT NULL, origin TEXT NOT NULL,
        name TEXT NOT NULL, observed_at_ms INTEGER, run INTEGER, attempt INTEGER,
        thread_id TEXT, turn_id TEXT, item_id TEXT, payload TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS events_book ON ingestion_events(book,seq)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS events_origin ON ingestion_events(book,origin,seq)"
    )
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='preparation'"
    ).fetchone():
        db.execute("""INSERT INTO ingestion_events(seq,book,origin,name,payload)
            SELECT seq,book,'library',phase,json_object('note',note,'outline',json(outline))
            FROM preparation ORDER BY seq""")
        db.execute("DROP TABLE preparation")


def write_event(
    db: sqlite3.Connection,
    digest: str,
    origin: str,
    name: str,
    payload: dict,
    *,
    observed_at_ms: int | None,
    run: int | None = None,
    attempt: int | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    item_id: str | None = None,
) -> int:
    return db.execute(
        """INSERT INTO ingestion_events
        (book,origin,name,observed_at_ms,run,attempt,thread_id,turn_id,item_id,payload)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            digest,
            origin,
            name,
            observed_at_ms,
            run,
            attempt,
            thread_id,
            turn_id,
            item_id,
            json.dumps(payload, ensure_ascii=False),
        ),
    ).lastrowid


def record_update(
    db: sqlite3.Connection,
    digest: str,
    update: PreparationUpdate,
    run: int | None = None,
) -> int:
    payload = {"note": update.note}
    if update.outline is not None:
        payload["outline"] = update.outline
    if update.previews is not None:
        payload["previews"] = update.previews
    seq = write_event(
        db,
        digest,
        "library",
        update.phase,
        payload,
        run=run,
        observed_at_ms=time.time_ns() // 1_000_000,
    )
    if update.phase == "opening":
        db.execute("UPDATE ingestion_events SET run=seq WHERE seq=?", (seq,))
    return seq


def record_sdk(
    db: sqlite3.Connection, digest: str, run: int, event: TraceEvent
) -> None:
    write_event(
        db,
        digest,
        "sdk",
        event.name,
        {"fields": event.fields, "markers": event.markers, "purpose": event.purpose},
        observed_at_ms=event.observed_at_ms,
        run=run,
        attempt=event.attempt,
        thread_id=event.thread_id,
        turn_id=event.turn_id,
        item_id=event.item_id,
    )


def activity(db: sqlite3.Connection, digest: str) -> dict | None:
    book = db.execute(
        "SELECT title,status,message FROM books WHERE id=? AND status!='removing'",
        (digest,),
    ).fetchone()
    if book is None:
        return None
    revisions = db.execute(
        """SELECT min(seq) registration_seq,max(seq) trace_revision,
        max(CASE WHEN origin='library' THEN seq END) preparation_revision,
        count(CASE WHEN origin='sdk' AND name!='capture_gap' THEN 1 END) sdk_count
        FROM ingestion_events WHERE book=?""",
        (digest,),
    ).fetchone()
    head = db.execute(
        "SELECT name,payload FROM ingestion_events WHERE seq=?",
        (revisions["preparation_revision"],),
    ).fetchone()
    contents = db.execute(
        """SELECT payload FROM ingestion_events WHERE book=? AND origin='library'
        AND json_type(payload,'$.outline')='array' ORDER BY seq DESC LIMIT 1""",
        (digest,),
    ).fetchone()
    payload = json.loads(contents[0]) if contents else {}
    opening = db.execute(
        "SELECT observed_at_ms,json_extract(payload,'$.previews[0]') FROM ingestion_events WHERE seq=?",
        (revisions["registration_seq"],),
    ).fetchone()
    outline = payload.get("outline", [])
    entries = payload.get(
        "previews",
        [
            {"depth": depth, "title": title, "key": f"label:{i}:{title}"}
            for i, (depth, title) in enumerate(outline)
        ],
    )
    return {
        **dict(book),
        **dict(revisions),
        "id": digest,
        "phase": head["name"] if head else book["status"],
        "activity": json.loads(head["payload"])["note"] if head else book["message"],
        "entries": entries,
        "outline": outline,
        "registered_at_ms": opening[0] if opening else None,
        "opening": json.loads(opening[1]) if opening and opening[1] else None,
    }


def trace_page(db: sqlite3.Connection, digest: str, after: int, limit: int) -> dict:
    rows = db.execute(
        """SELECT seq,origin,name,observed_at_ms,run,attempt,
        json_extract(payload,'$.note') note, json_extract(payload,'$.fields.type') item_type,
        json_extract(payload,'$.fields.status') status,json_extract(payload,'$.fields.exitCode') exit_code
        FROM ingestion_events WHERE book=? AND seq>? ORDER BY seq LIMIT ?""",
        (digest, after, limit + 1),
    ).fetchall()
    events = [dict(row) for row in rows[:limit]]
    return {
        "events": events,
        "has_more": len(rows) > limit,
        "next_after": events[-1]["seq"] if events else after,
    }


def trace_detail(db: sqlite3.Connection, digest: str, seq: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM ingestion_events WHERE book=? AND seq=?", (digest, seq)
    ).fetchone()
    if row:
        result = dict(row)
        return {**json.loads(result.pop("payload")), **result}
    return None
