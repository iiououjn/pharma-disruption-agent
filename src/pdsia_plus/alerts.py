"""Webhook alerts with idempotent keys, explicit opt-in, and simulated-data exclusion."""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .matching import run_hardened_briefing

SCHEMA = """CREATE TABLE IF NOT EXISTS ops_alert_log (
 alert_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
 payload_json TEXT NOT NULL
);"""


def prepare_alerts(conn, *, minimum_priority="HIGH", include_synthetic=False) -> list[dict]:
    if minimum_priority not in ("HIGH", "MEDIUM"):
        raise ValueError("minimum_priority must be HIGH or MEDIUM")
    briefing = run_hardened_briefing(conn)
    if briefing["snapshot"]["is_synthetic"] and not include_synthetic:
        return []
    priorities = {"HIGH"} if minimum_priority == "HIGH" else {"HIGH", "MEDIUM"}
    conn.executescript(SCHEMA)
    messages = []
    for item in briefing["items"]:
        if item["priority"] not in priorities:
            continue
        fingerprint = hashlib.sha256(json.dumps({"sku": item["sku"], "priority": item["priority"],
                         "matches": sorted(m["record_key"] for m in item["matches"]),
                         "changes": item["changes"]}, sort_keys=True).encode()).hexdigest()
        if conn.execute("SELECT 1 FROM ops_alert_log WHERE alert_key=?", (fingerprint,)).fetchone():
            continue
        messages.append({"key": fingerprint, "snapshot_id": briefing["snapshot"]["snapshot_id"],
                         "sku": item["sku"], "priority": item["priority"],
                         "summary": item["rationale"], "simulated_assumptions": True})
    return messages


def deliver_alerts(conn, url: str, *, minimum_priority="HIGH", include_synthetic=False) -> list[dict]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("alerts require an HTTPS webhook URL")
    alerts = prepare_alerts(conn, minimum_priority=minimum_priority, include_synthetic=include_synthetic)
    for entry in alerts:
        req = urllib.request.Request(url, data=json.dumps(entry).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"webhook returned {resp.status}")
        with conn:
            conn.execute("INSERT INTO ops_alert_log VALUES (?,?,?,?)",
                         (entry["key"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          entry["snapshot_id"], json.dumps(entry)))
    return alerts
