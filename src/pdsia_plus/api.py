"""Opt-in secured API; original pdsia.api is not modified.

Run PDSIA_API_TOKEN=<secret> uvicorn pdsia_plus.api:app --host 0.0.0.0 --port 8000
Unauthenticated requests to /health are allowed; all other routes fail closed.
"""
from __future__ import annotations

import os
import secrets
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal

from pdsia import db as dbm, ingest
from pdsia.agent import run_briefing
from pdsia.diff import diff_snapshots
from .matching import run_hardened_briefing
from .reviews import list_audit, submit_review
from .live import capture_live

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(title="Pharmaceutical Supply Disruption Agent - Guarded API", version="0.2.0",
              description="US FDA data; catalogue remains SIMULATED. Never use for medical decisions.")


def _conn():
    return dbm.connect(os.environ.get("PDSIA_DB", "pdsia.db"))


@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.url.path in ("/health", "/openapi.json", "/docs", "/redoc", "/dashboard") or request.url.path.startswith("/assets/"):
        return await call_next(request)
    token = os.environ.get("PDSIA_API_TOKEN", "")
    if not token:
        return JSONResponse(status_code=503, content={"detail": "PDSIA_API_TOKEN must be configured"})
    supplied = request.headers.get("Authorization", "")
    if not supplied.startswith("Bearer ") or not secrets.compare_digest(supplied[7:], token):
        return JSONResponse(status_code=401, content={"detail": "valid bearer token required"},
                            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


class ReviewInput(BaseModel):
    record_key: str = Field(min_length=1, max_length=256)
    sku: str = Field(min_length=1, max_length=256)
    decision: Literal["accept", "reject"]
    note: str = Field(default="", max_length=2000)


@app.get("/health")
def health():
    return {"status": "ok", "data_scope": "US_only", "simulation": "portfolio_data_simulated"}


@app.get("/snapshots")
def snapshots():
    with closing(_conn()) as conn:
        return dbm.list_snapshots(conn)


@app.post("/snapshots/demo")
def demo():
    if os.environ.get("PDSIA_ALLOW_DEMO_WRITES") != "1":
        raise HTTPException(403, "Demo writes disabled. Set PDSIA_ALLOW_DEMO_WRITES=1 explicitly")
    with closing(_conn()) as conn:
        ids = [ingest.load_fixture(conn, f) for f in
               ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json")]
        n = dbm.load_catalogue_csv(conn, ROOT / "data" / "simulated" / "catalogue.csv")
        return {"snapshots": ids, "catalogue_skus": n, "synthetic": True}


@app.post("/snapshots/live")
def live(max_records: int | None = None):
    if max_records is not None and not (1 <= max_records <= 5000):
        raise HTTPException(422, "max_records must be between 1 and 5000")
    try:
        with closing(_conn()) as conn:
            return capture_live(conn, os.environ.get("PDSIA_ARCHIVE_DIR", "data/live"),
                                api_key=os.environ.get("OPENFDA_API_KEY"), max_records=max_records)
    except Exception as exc:
        raise HTTPException(502, f"FDA pull failed: {exc.__class__.__name__}") from exc


@app.get("/records")
def records(snapshot_id: int | None = None, status: str | None = None):
    with closing(_conn()) as conn:
        snaps = dbm.list_snapshots(conn)
        if not snaps:
            raise HTTPException(404, "no snapshots")
        sid = snapshot_id or snaps[-1]["snapshot_id"]
        return [r.summary() for r in dbm.get_records(conn, sid)
                if status is None or r.status.lower() == status.lower()]


@app.get("/changes")
def changes(from_id: int, to_id: int):
    with closing(_conn()) as conn:
        return diff_snapshots(dbm.get_records(conn, from_id), dbm.get_records(conn, to_id))


@app.get("/briefing")
def briefing(snapshot_id: int | None = None, prev_snapshot_id: int | None = None):
    with closing(_conn()) as conn:
        if not dbm.list_snapshots(conn):
            raise HTTPException(404, "no snapshots")
        if not dbm.get_catalogue(conn):
            raise HTTPException(409, "no catalogue; only simulated source bundled")
        return run_hardened_briefing(conn, snapshot_id, prev_snapshot_id)


@app.get("/briefing/original")
def original_briefing():
    with closing(_conn()) as conn:
        if not dbm.list_snapshots(conn) or not dbm.get_catalogue(conn):
            raise HTTPException(409, "load snapshot and catalogue first")
        return run_briefing(conn)


@app.get("/review-queue")
def review_queue():
    return briefing()["review_queue"]


@app.post("/reviews")
def review(body: ReviewInput):
    with closing(_conn()) as conn:
        try:
            return submit_review(conn, record_key=body.record_key, sku=body.sku,
                                 decision=body.decision,
                                 reviewer=os.environ.get("PDSIA_REVIEWER_ID", "authenticated-api-client"),
                                 note=body.note)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@app.get("/audit/reviews")
def audit(limit: int = 100):
    with closing(_conn()) as conn:
        return list_audit(conn, limit)


@app.get("/metrics/history")
def history_metrics():
    with closing(_conn()) as conn:
        return [dict(row) for row in conn.execute("""SELECT s.snapshot_id,s.fetched_at,s.is_synthetic,
                 COUNT(r.record_key) AS record_count FROM snapshots AS s
                 LEFT JOIN pub_shortage_records AS r ON r.snapshot_id=s.snapshot_id
                 GROUP BY s.snapshot_id,s.fetched_at,s.is_synthetic ORDER BY s.snapshot_id""")]


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(ROOT / "web" / "index.html", media_type="text/html")


app.mount("/assets", StaticFiles(directory=str(ROOT / "web")), name="assets")
