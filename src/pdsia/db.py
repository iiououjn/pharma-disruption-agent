"""SQLite storage. Table prefixes keep PUBLIC evidence (pub_*) apart from
SIMULATED assumptions (sim_*). The PostgreSQL DDL in sql/postgres_schema.sql uses
real schemas (pub / sim) for the same separation.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    source        TEXT NOT NULL,
    is_synthetic  INTEGER NOT NULL DEFAULT 0,
    label         TEXT,
    content_hash  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pub_shortage_records (
    snapshot_id       INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
    record_key        TEXT NOT NULL,
    generic_name      TEXT,
    proprietary_name  TEXT,
    company_name      TEXT,
    presentation      TEXT,
    dosage_form       TEXT,
    strengths_json    TEXT,
    package_ndcs_json TEXT,
    status            TEXT,
    availability      TEXT,
    shortage_reason   TEXT,
    related_info      TEXT,
    resolved_note     TEXT,
    update_type       TEXT,
    update_date       TEXT,
    initial_posting_date TEXT,
    raw_json          TEXT,
    PRIMARY KEY (snapshot_id, record_key)
);
CREATE TABLE IF NOT EXISTS sim_catalogue (
    sku                 TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    strength            TEXT,
    dosage_form         TEXT,
    ndcs_json           TEXT,
    supplier            TEXT,
    on_hand             REAL,
    daily_demand        REAL,
    open_order_qty      REAL,
    open_order_due_days REAL,
    unit_cost           REAL,
    lead_time_days      REAL,
    is_simulated        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS match_candidates (
    snapshot_id  INTEGER NOT NULL,
    record_key   TEXT NOT NULL,
    sku          TEXT NOT NULL,
    tier         INTEGER NOT NULL,
    outcome      TEXT NOT NULL,
    score        REAL NOT NULL,
    reasons_json TEXT,
    PRIMARY KEY (snapshot_id, record_key, sku)
);
CREATE TABLE IF NOT EXISTS review_decisions (
    record_key  TEXT NOT NULL,
    sku         TEXT NOT NULL,
    decision    TEXT NOT NULL CHECK (decision IN ('accept','reject')),
    reviewer    TEXT,
    note        TEXT,
    decided_at  TEXT,
    PRIMARY KEY (record_key, sku)
);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------ dataclasses
@dataclass
class ShortageRecord:
    record_key: str
    generic_name: str = ""
    proprietary_name: str = ""
    company_name: str = ""
    presentation: str = ""
    dosage_form: str = ""
    strengths: list = field(default_factory=list)
    package_ndcs: list = field(default_factory=list)
    status: str = ""
    availability: str = ""
    shortage_reason: str = ""
    related_info: str = ""
    resolved_note: str = ""
    update_type: str = ""
    update_date: str = ""
    initial_posting_date: str = ""
    snapshot_id: int | None = None

    def summary(self) -> dict:
        return {
            "record_key": self.record_key, "generic_name": self.generic_name,
            "company_name": self.company_name, "dosage_form": self.dosage_form,
            "strengths": self.strengths, "status": self.status,
            "availability": self.availability, "shortage_reason": self.shortage_reason,
            "update_date": self.update_date, "snapshot_id": self.snapshot_id,
        }


@dataclass
class Sku:
    sku: str
    name: str
    strength: str = ""
    dosage_form: str = ""
    ndcs: list = field(default_factory=list)
    supplier: str = ""
    on_hand: float | None = None
    daily_demand: float | None = None
    open_order_qty: float | None = None
    open_order_due_days: float | None = None
    unit_cost: float | None = None
    lead_time_days: float | None = None


def row_to_record(r: sqlite3.Row) -> ShortageRecord:
    return ShortageRecord(
        record_key=r["record_key"], generic_name=r["generic_name"] or "",
        proprietary_name=r["proprietary_name"] or "", company_name=r["company_name"] or "",
        presentation=r["presentation"] or "", dosage_form=r["dosage_form"] or "",
        strengths=json.loads(r["strengths_json"] or "[]"),
        package_ndcs=json.loads(r["package_ndcs_json"] or "[]"),
        status=r["status"] or "", availability=r["availability"] or "",
        shortage_reason=r["shortage_reason"] or "", related_info=r["related_info"] or "",
        resolved_note=r["resolved_note"] or "", update_type=r["update_type"] or "",
        update_date=r["update_date"] or "", initial_posting_date=r["initial_posting_date"] or "",
        snapshot_id=r["snapshot_id"],
    )


def row_to_sku(r: sqlite3.Row) -> Sku:
    return Sku(
        sku=r["sku"], name=r["name"], strength=r["strength"] or "",
        dosage_form=r["dosage_form"] or "", ndcs=json.loads(r["ndcs_json"] or "[]"),
        supplier=r["supplier"] or "", on_hand=r["on_hand"], daily_demand=r["daily_demand"],
        open_order_qty=r["open_order_qty"], open_order_due_days=r["open_order_due_days"],
        unit_cost=r["unit_cost"], lead_time_days=r["lead_time_days"],
    )


def _f(x):
    x = (x or "").strip() if isinstance(x, str) else x
    return None if x in ("", None) else float(x)


def load_catalogue_csv(conn: sqlite3.Connection, path: str | Path, replace: bool = True) -> int:
    """Load a SIMULATED catalogue CSV. Columns: sku,name,strength,dosage_form,ndcs('|'-sep),
    supplier,on_hand,daily_demand,open_order_qty,open_order_due_days,unit_cost,lead_time_days."""
    if replace:
        conn.execute("DELETE FROM sim_catalogue")
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ndcs = [x for x in (row.get("ndcs") or "").split("|") if x]
            conn.execute(
                "INSERT OR REPLACE INTO sim_catalogue(sku,name,strength,dosage_form,ndcs_json,supplier,"
                "on_hand,daily_demand,open_order_qty,open_order_due_days,unit_cost,lead_time_days) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["sku"], row["name"], row.get("strength", ""), row.get("dosage_form", ""),
                 json.dumps(ndcs), row.get("supplier", ""), _f(row.get("on_hand")),
                 _f(row.get("daily_demand")), _f(row.get("open_order_qty")),
                 _f(row.get("open_order_due_days")), _f(row.get("unit_cost")),
                 _f(row.get("lead_time_days"))),
            )
            n += 1
    conn.commit()
    return n


def get_catalogue(conn: sqlite3.Connection) -> list[Sku]:
    return [row_to_sku(r) for r in conn.execute("SELECT * FROM sim_catalogue ORDER BY sku")]


def get_sku(conn: sqlite3.Connection, sku: str) -> Sku | None:
    r = conn.execute("SELECT * FROM sim_catalogue WHERE sku=?", (sku,)).fetchone()
    return row_to_sku(r) if r else None


def list_snapshots(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id")]


def get_records(conn: sqlite3.Connection, snapshot_id: int) -> list[ShortageRecord]:
    return [row_to_record(r) for r in conn.execute(
        "SELECT * FROM pub_shortage_records WHERE snapshot_id=? ORDER BY record_key", (snapshot_id,))]
