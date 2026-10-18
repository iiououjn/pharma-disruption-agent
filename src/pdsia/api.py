"""FastAPI service.  NOT RUN in the build sandbox (FastAPI not installed there) - see README 'Verification status'.

  PDSIA_DB=pdsia.db uvicorn pdsia.api:app --reload
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db as dbm
from . import ingest
from .agent import run_briefing
from .diff import diff_snapshots

DB_PATH = os.environ.get("PDSIA_DB", "pdsia.db")
CATALOGUE_CSV = Path(__file__).resolve().parents[2] / "data" / "simulated" / "catalogue.csv"

app = FastAPI(title="Pharma Supply Disruption Agent", version="0.1.0",
              description="US FDA shortage data + SIMULATED catalogue. Not evidence of UAE availability.")


def _conn():
    return dbm.connect(DB_PATH)


class ReviewIn(BaseModel):
    record_key: str
    sku: str
    decision: Literal["accept", "reject"]
    reviewer: str = "unknown"
    note: str = ""


@app.get("/health")
def health():
    return {"status": "ok", "db": DB_PATH}


@app.get("/snapshots")
def snapshots():
    return dbm.list_snapshots(_conn())


@app.post("/snapshots/demo")
def load_demo():
    """Load the two synthetic fixture snapshots and the simulated catalogue."""
    c = _conn()
    ids = [ingest.load_fixture(c, f) for f in ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json")]
    n = dbm.load_catalogue_csv(c, CATALOGUE_CSV)
    return {"snapshots": ids, "catalogue_skus": n, "synthetic": True}


@app.post("/snapshots/live")
def load_live(max_records: int | None = None):
    try:
        sid, created = ingest.ingest_live(_conn(), os.environ.get("OPENFDA_API_KEY"), max_records)
    except Exception as exc:  # network / schema problems should be visible, not swallowed
        raise HTTPException(502, f"openFDA fetch failed: {exc}")
    return {"snapshot_id": sid, "created": created}


@app.get("/records")
def records(snapshot_id: int | None = None, status: str | None = None):
    c = _conn()
    snaps = dbm.list_snapshots(c)
    if not snaps:
        raise HTTPException(404, "no snapshots")
    sid = snapshot_id or snaps[-1]["snapshot_id"]
    return [r.summary() for r in dbm.get_records(c, sid) if not status or r.status.lower() == status.lower()]


@app.get("/changes")
def changes(from_id: int, to_id: int):
    c = _conn()
    return diff_snapshots(dbm.get_records(c, from_id), dbm.get_records(c, to_id))


@app.get("/briefing")
def briefing(snapshot_id: int | None = None, prev_snapshot_id: int | None = None):
    c = _conn()
    if not dbm.list_snapshots(c):
        raise HTTPException(404, "no snapshots; POST /snapshots/demo or /snapshots/live first")
    if not dbm.get_catalogue(c):
        raise HTTPException(409, "catalogue is empty (simulated catalogue not loaded)")
    return run_briefing(c, snapshot_id, prev_snapshot_id)


@app.get("/review-queue")
def review_queue():
    return run_briefing(_conn())["review_queue"]


@app.post("/reviews")
def submit_review(r: ReviewIn):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO review_decisions(record_key, sku, decision, reviewer, note, decided_at) "
              "VALUES (?,?,?,?,?,?)",
              (r.record_key, r.sku, r.decision, r.reviewer, r.note, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    c.commit()
    return {"saved": True}
