"""Snapshot ingestion.

- `fetch_openfda` pulls live records from the public openFDA drug-shortages endpoint.
  UNTESTED in the build sandbox (no network). Verify field names against
  https://open.fda.gov/apis/drug/drugshortages/searchable-fields/ before relying on it.
- `load_fixture` loads the bundled SYNTHETIC openFDA-shaped fixtures used for the offline demo.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OPENFDA_URL = "https://api.fda.gov/drug/shortages.json"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def fetch_openfda(api_key: str | None = None, page_size: int = 100,
                  max_records: int | None = None, timeout: int = 30) -> list[dict]:
    out: list[dict] = []
    skip = 0
    while True:
        params = {"limit": page_size, "skip": skip}
        if api_key:
            params["api_key"] = api_key
        url = OPENFDA_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            payload = json.loads(resp.read().decode("utf-8"))
        results = payload.get("results", [])
        out += results
        skip += page_size
        if len(results) < page_size or (max_records and len(out) >= max_records):
            break
    return out[:max_records] if max_records else out


def _as_list(x) -> list:
    if x is None or x == "":
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def _s(x) -> str:
    if isinstance(x, (list, tuple)):
        return "; ".join(str(i) for i in x)
    return "" if x is None else str(x)


def _identity_key(raw: dict) -> str:
    ident = "|".join(_s(raw.get(k)).strip().lower() for k in
                     ("generic_name", "company_name", "presentation", "dosage_form", "strength", "package_ndc"))
    return "H-" + hashlib.sha1(ident.encode()).hexdigest()[:12]


def normalise_raw(raw: dict) -> dict:
    """Map one openFDA-shaped record to our column set. Key excludes status/dates so it is
    stable across snapshots (needed for change detection)."""
    ndcs = _as_list(raw.get("package_ndc")) + _as_list((raw.get("openfda") or {}).get("package_ndc"))
    return {
        "record_key": raw.get("fixture_id") or _identity_key(raw),
        "generic_name": _s(raw.get("generic_name")),
        "proprietary_name": _s(raw.get("proprietary_name")),
        "company_name": _s(raw.get("company_name")),
        "presentation": _s(raw.get("presentation")),
        "dosage_form": _s(raw.get("dosage_form")),
        "strengths": [str(x) for x in _as_list(raw.get("strength"))],
        "package_ndcs": [str(x) for x in ndcs],
        "status": _s(raw.get("status")),
        "availability": _s(raw.get("availability")),
        "shortage_reason": _s(raw.get("shortage_reason")),
        "related_info": _s(raw.get("related_info")),
        "resolved_note": _s(raw.get("resolved_note")),
        "update_type": _s(raw.get("update_type")),
        "update_date": _s(raw.get("update_date")),
        "initial_posting_date": _s(raw.get("initial_posting_date")),
    }


def store_snapshot(conn: sqlite3.Connection, raw_records: list[dict], *, source: str,
                   is_synthetic: bool, label: str = "", fetched_at: str | None = None) -> tuple[int, bool]:
    """Store a snapshot. Returns (snapshot_id, created). If the content is identical to the latest
    snapshot, nothing new is stored (created=False)."""
    rows = [normalise_raw(r) for r in raw_records]
    seen: dict[str, int] = {}
    for r in rows:                      # guard against identity collisions inside one pull
        k = r["record_key"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            r["record_key"] = f"{k}#{seen[k]}"
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    last = conn.execute("SELECT snapshot_id, content_hash FROM snapshots ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    if last and last["content_hash"] == digest:
        return last["snapshot_id"], False
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO snapshots(fetched_at, source, is_synthetic, label, content_hash) VALUES (?,?,?,?,?)",
        (fetched_at, source, int(is_synthetic), label, digest))
    sid = cur.lastrowid
    for raw, r in zip(raw_records, rows):
        conn.execute(
            "INSERT INTO pub_shortage_records(snapshot_id, record_key, generic_name, proprietary_name, "
            "company_name, presentation, dosage_form, strengths_json, package_ndcs_json, status, availability, "
            "shortage_reason, related_info, resolved_note, update_type, update_date, initial_posting_date, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, r["record_key"], r["generic_name"], r["proprietary_name"], r["company_name"],
             r["presentation"], r["dosage_form"], json.dumps(r["strengths"]), json.dumps(r["package_ndcs"]),
             r["status"], r["availability"], r["shortage_reason"], r["related_info"], r["resolved_note"],
             r["update_type"], r["update_date"], r["initial_posting_date"], json.dumps(raw)))
    conn.commit()
    return sid, True


def load_fixture(conn: sqlite3.Connection, name: str) -> tuple[int, bool]:
    path = FIXTURE_DIR / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("meta", {})
    return store_snapshot(conn, payload["results"], source=f"fixture:{name}",
                          is_synthetic=bool(meta.get("synthetic", True)),
                          label=meta.get("label", name), fetched_at=meta.get("fetched_at"))


def ingest_live(conn: sqlite3.Connection, api_key: str | None = None, max_records: int | None = None):
    recs = fetch_openfda(api_key=api_key, max_records=max_records)
    return store_snapshot(conn, recs, source="openFDA:drug/shortages", is_synthetic=False, label="live pull")
