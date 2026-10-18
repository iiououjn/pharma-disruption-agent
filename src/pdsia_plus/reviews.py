"""Authenticated decision recording with append-only audit history."""
from __future__ import annotations

from datetime import datetime, timezone

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_review_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL,
    sku TEXT NOT NULL,
    previous_decision TEXT,
    new_decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
"""


def submit_review(conn, *, record_key: str, sku: str, decision: str,
                  reviewer: str, note: str = "") -> dict:
    if decision not in ("accept", "reject"):
        raise ValueError("decision must be accept or reject")
    if not all(isinstance(v, str) and 0 < len(v.strip()) <= 256 for v in (record_key, sku, reviewer)):
        raise ValueError("record_key, sku, reviewer are mandatory and <=256 characters")
    if len(note) > 2000:
        raise ValueError("note must be <=2000 characters")
    conn.executescript(AUDIT_SCHEMA)
    if not conn.execute("SELECT 1 FROM pub_shortage_records WHERE record_key=? LIMIT 1", (record_key,)).fetchone():
        raise ValueError("unknown record_key")
    if not conn.execute("SELECT 1 FROM sim_catalogue WHERE sku=?", (sku,)).fetchone():
        raise ValueError("unknown sku")
    previous = conn.execute("SELECT decision FROM review_decisions WHERE record_key=? AND sku=?",
                            (record_key, sku)).fetchone()
    old = previous[0] if previous else None
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("INSERT INTO ops_review_audit(record_key,sku,previous_decision,new_decision,reviewer,note,decided_at) "
                     "VALUES (?,?,?,?,?,?,?)", (record_key, sku, old, decision, reviewer, note, when))
        conn.execute("INSERT INTO review_decisions(record_key,sku,decision,reviewer,note,decided_at) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(record_key,sku) DO UPDATE SET decision=excluded.decision, "
                     "reviewer=excluded.reviewer,note=excluded.note,decided_at=excluded.decided_at",
                     (record_key, sku, decision, reviewer, note, when))
    return {"saved": True, "record_key": record_key, "sku": sku, "old": old, "new": decision,
            "reviewer": reviewer, "decided_at": when}


def list_audit(conn, limit: int = 100) -> list[dict]:
    conn.executescript(AUDIT_SCHEMA)
    return [dict(row) for row in conn.execute(
        "SELECT * FROM ops_review_audit ORDER BY audit_id DESC LIMIT ?", (max(1, min(limit, 1000)),))]
