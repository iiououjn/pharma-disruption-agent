"""Idempotent, archived daily FDA pulls, keeping original ingest.py untouched."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from pdsia import ingest


def fetch_retried(api_key: str | None = None, max_records: int | None = None,
                  *, attempts: int = 3, pause_seconds: float = 1.0, fetcher=None) -> list[dict]:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    fetcher = fetcher or ingest.fetch_openfda
    for index in range(attempts):
        try:
            rows = fetcher(api_key=api_key, max_records=max_records)
            if not isinstance(rows, list) or not rows or not all(isinstance(x, dict) for x in rows):
                raise ValueError("openFDA returned no valid records; refusing an empty/invalid snapshot")
            required = {"generic_name", "proprietary_name", "presentation"}
            if not any(required.intersection(r) for r in rows):
                raise ValueError("unexpected openFDA record structure; refusing snapshot")
            return rows
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise
            if index + 1 == attempts:
                raise
            time.sleep(pause_seconds * (2 ** index))
    raise AssertionError("unreachable")


def _canonical(rows: list[dict]) -> list[dict]:
    """Sort independent of source pagination/order to avoid false changes."""
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))


def _stable_raw(rows: list[dict]) -> list[dict]:
    """Prefer company+package NDC over volatile labels when possible.

    Only works with uniquely identifiable package NDCs. For collision-prone
    records leave the original identity algorithm intact; avoid false joins.
    """
    rows = _canonical(rows)
    candidate_keys: list[str | None] = []
    for r in rows:
        ndcs = ingest._as_list(r.get("package_ndc")) + ingest._as_list((r.get("openfda") or {}).get("package_ndc"))
        parts = sorted({str(n).strip().lower() for n in ndcs if str(n).strip()})
        company = str(r.get("company_name") or "").strip().lower()
        if parts and company:
            seed = json.dumps([company, parts], separators=(",", ":"))
            candidate_keys.append("FDA-" + hashlib.sha256(seed.encode()).hexdigest()[:20])
        else:
            candidate_keys.append(None)
    counts = {key: candidate_keys.count(key) for key in set(candidate_keys) if key}
    output = []
    for raw, stable_key in zip(rows, candidate_keys):
        record = dict(raw)
        if stable_key and counts[stable_key] == 1 and not record.get("fixture_id"):
            # `fixture_id` is the existing ingestion extension point for a stable key.
            # The original response remains unchanged in the saved daily raw archive.
            record["fixture_id"] = stable_key
        output.append(record)
    return output


def capture_live(conn, archive_dir: str | Path, *, api_key: str | None = None,
                 max_records: int | None = None, fetcher=None, at=None) -> dict:
    now = at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timestamp must have a time zone")
    rows = fetch_retried(api_key=api_key, max_records=max_records, fetcher=fetcher)
    return store_archive_and_snapshot(conn, rows, archive_dir, at=now, partial=max_records is not None)


def store_archive_and_snapshot(conn, rows: list[dict], archive_dir: str | Path,
                               *, at=None, partial=False) -> dict:
    if not rows:
        raise ValueError("refusing to ingest an empty snapshot")
    rows = _canonical(rows)
    stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    path = Path(archive_dir) / stamp.strftime("%Y-%m-%d") / f"{stamp.strftime('%H%M%S')}-{digest[:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"metadata": {"source": "openFDA:drug/shortages", "fetched_at": stamp.isoformat(),
                         "is_synthetic": False, "partial": partial, "sha256": digest}, "results": rows}
    if not path.exists():
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    sid, created = ingest.store_snapshot(conn, _stable_raw(rows), source="openFDA:drug/shortages",
                                         is_synthetic=False,
                                         label="partial live pull" if partial else "live pull",
                                         fetched_at=stamp.isoformat(timespec="seconds"))
    return {"snapshot_id": sid, "created": created, "records": len(rows),
            "archive": str(path), "sha256": digest, "partial": partial}


def replay_archives(conn, archive_dir: str | Path) -> list[dict]:
    outcomes = []
    for path in sorted(Path(archive_dir).glob("*/*.json")):
        obj = json.loads(path.read_text(encoding="utf-8"))
        meta, rows = obj["metadata"], obj["results"]
        if meta.get("source") != "openFDA:drug/shortages" or meta.get("is_synthetic") is not False:
            raise ValueError(f"unexpected archive source: {path}")
        digest = hashlib.sha256(json.dumps(_canonical(rows), ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        if digest != meta.get("sha256"):
            raise ValueError(f"archive checksum mismatch: {path}")
        sid, created = ingest.store_snapshot(conn, _stable_raw(rows),
                                             source="openFDA:drug/shortages", is_synthetic=False,
                                             label="partial live pull" if meta.get("partial") else "live pull",
                                             fetched_at=meta["fetched_at"])
        outcomes.append({"file": str(path), "snapshot_id": sid, "created": created})
    return outcomes
