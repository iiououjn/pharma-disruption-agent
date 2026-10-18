"""The tool surface the agent is allowed to use. Every call is logged to a trace.

The agent never computes numbers itself: numbers come from `calculate_exposure`, and
`submit_briefing` rejects any briefing whose text or calc ids are not backed by tool output.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import db as dbm
from .diff import diff_snapshots
from .exposure import calculate_exposure as _calc, numbers_in
from .matcher import match_all

ACTIVE_STATUSES = {"current", "to be discontinued"}
PRIORITIES = {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT_EVIDENCE"}


def is_active(status: str) -> bool:
    return (status or "").strip().lower() in ACTIVE_STATUSES


def allowed_numbers(item: dict) -> set[float]:
    """Numbers a rationale for this item may legitimately contain (all from tool outputs)."""
    allowed: set[float] = set()
    for v in (item.get("exposure") or {}).values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            allowed.add(round(float(v), 1))
    for m in item.get("matches", []):
        allowed.add(float(m["tier"]))
    allowed.add(float(len(item.get("matches", []))))
    return allowed


def numbers_ok(text: str, item: dict) -> tuple[bool, list[float]]:
    allowed = allowed_numbers(item)
    bad = [n for n in numbers_in(text) if not any(abs(n - a) < 1e-6 for a in allowed)]
    return (not bad), bad


class ToolBox:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.trace: list[dict] = []
        self.calcs: dict[str, dict] = {}

    def _log(self, tool: str, args: dict, summary: str) -> None:
        self.trace.append({"tool": tool, "args": args, "result": summary})

    def _decisions(self) -> dict:
        return {(r["record_key"], r["sku"]): r["decision"]
                for r in self.conn.execute("SELECT * FROM review_decisions")}

    # 1 -------------------------------------------------------------------
    def get_shortage_records(self, snapshot_id: int | None = None, query: str | None = None,
                             record_key: str | None = None, status: str | None = None):
        if snapshot_id is None:
            snaps = dbm.list_snapshots(self.conn)
            if not snaps:
                raise ValueError("no snapshots stored")
            snapshot_id = snaps[-1]["snapshot_id"]
        recs = dbm.get_records(self.conn, snapshot_id)
        if record_key:
            recs = [r for r in recs if r.record_key == record_key]
        if query:
            q = query.lower()
            recs = [r for r in recs if q in r.generic_name.lower() or q in r.company_name.lower()]
        if status:
            recs = [r for r in recs if r.status.lower() == status.lower()]
        self._log("get_shortage_records", {"snapshot_id": snapshot_id, "query": query,
                                           "record_key": record_key, "status": status}, f"{len(recs)} records")
        return recs

    # 2 -------------------------------------------------------------------
    def find_catalogue_matches(self, record_key: str, snapshot_id: int | None = None):
        recs = self.get_shortage_records(snapshot_id=snapshot_id, record_key=record_key)
        if not recs:
            return []
        rec = recs[0]
        results = match_all(rec, dbm.get_catalogue(self.conn), self._decisions())
        self.conn.execute("DELETE FROM match_candidates WHERE snapshot_id=? AND record_key=?",
                          (rec.snapshot_id, record_key))
        for m in results:
            self.conn.execute("INSERT INTO match_candidates VALUES (?,?,?,?,?,?,?)",
                              (rec.snapshot_id, record_key, m.sku, m.tier, m.outcome, m.score,
                               json.dumps({"reasons": m.reasons, "conflicts": m.conflicts, "unknowns": m.unknowns})))
        self.conn.commit()
        self._log("find_catalogue_matches", {"record_key": record_key, "snapshot_id": rec.snapshot_id},
                  ", ".join(f"{m.sku}:{m.outcome}(t{m.tier})" for m in results) or "no candidates")
        return results

    # 3 -------------------------------------------------------------------
    def calculate_exposure(self, sku: str, companies: list[str]):
        s = dbm.get_sku(self.conn, sku)
        if s is None:
            raise KeyError(sku)
        result = _calc(s, companies)
        self.calcs[result["calc_id"]] = result
        self._log("calculate_exposure", {"sku": sku, "companies": companies},
                  f"{result['calc_id']} cover={result['cover_days']} shortfall={result['shortfall_days']}")
        return result

    # 4 -------------------------------------------------------------------
    def compare_snapshots(self, from_id: int, to_id: int):
        events = diff_snapshots(dbm.get_records(self.conn, from_id), dbm.get_records(self.conn, to_id))
        self._log("compare_snapshots", {"from": from_id, "to": to_id}, f"{len(events)} events")
        return events

    # 5 -------------------------------------------------------------------
    def submit_briefing(self, briefing: dict) -> dict:
        """Validate: priorities legal, every exposure calc_id came from a tool call,
        every number in each rationale is backed by tool output."""
        problems = []
        for it in briefing.get("items", []):
            if it["priority"] not in PRIORITIES:
                problems.append(f"{it['sku']}: illegal priority {it['priority']}")
            exp = it.get("exposure")
            if exp is None or exp.get("calc_id") not in self.calcs:
                problems.append(f"{it['sku']}: exposure not backed by a calculate_exposure call")
            elif self.calcs[exp["calc_id"]] != exp:
                problems.append(f"{it['sku']}: exposure differs from tool output")
            if not it.get("matches"):
                problems.append(f"{it['sku']}: no supporting match evidence")
            ok, bad = numbers_ok(it.get("rationale", ""), it)
            if not ok:
                problems.append(f"{it['sku']}: unbacked numbers in rationale {bad}")
        if problems:
            raise ValueError("briefing rejected: " + "; ".join(problems))
        self._log("submit_briefing", {}, f"accepted {len(briefing.get('items', []))} items")
        return briefing


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
