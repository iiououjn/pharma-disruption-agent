"""Investigation agent.

The agent is a tool-calling pipeline expressed as small node functions over a state dict, so the
same nodes run (a) sequentially via `run_briefing` and (b) as a LangGraph graph (langgraph_agent.py).

Design rules:
  * Matching, numbers and priority are decided by tools/code. The language layer (template or an
    optional LLM `narrator`) only phrases them, and its output is re-validated.
  * Explicit abstain states: needs_review, insufficient_evidence, no_match.
  * Public FDA evidence and SIMULATED catalogue assumptions are labelled separately in every item.
"""
from __future__ import annotations

import re
from typing import Callable

from . import db as dbm
from .exposure import prioritise, fmt_num, fmt_money
from .matcher import AUTO, REVIEW, REJECT
from .tools import ToolBox, is_active, numbers_ok, now_iso

SCOPE_NOTES = [
    "Public evidence is limited to FDA-reported drug shortages (US). It says nothing about UAE availability.",
    "FDA shortage listings are reported shortages, not a complete view of every potential disruption.",
    "Catalogue, inventory, demand, orders and lead times are SIMULATED assumptions.",
    "This is monitoring and investigation of current records; it does not forecast future shortages.",
]
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "INSUFFICIENT_EVIDENCE": 2, "LOW": 3}
RULE_TEXT = {
    "cover_below_lead_time": "stock cover is shorter than the replenishment lead time",
    "thin_buffer_vs_lead_time": "stock cover is only modestly above the replenishment lead time (thin buffer)",
    "listed_as_to_be_discontinued": "the product is listed as to be discontinued",
    "adequate_cover": "stock cover exceeds the lead-time buffer",
    "no_demand_or_lead_time_data": "demand or lead-time data is missing",
}
_HINT = re.compile(r"\b(20\d{2}|q[1-4]|january|february|march|april|may|june|july|august|september|"
                   r"october|november|december|expected|anticipated|resum\w+)\b", re.I)


# ------------------------------------------------------------------ nodes
def node_load(state: dict) -> dict:
    snaps = dbm.list_snapshots(state["tb"].conn)
    if not snaps:
        raise ValueError("no snapshots: ingest first")
    cur = state.get("snapshot_id") or snaps[-1]["snapshot_id"]
    earlier = [s for s in snaps if s["snapshot_id"] < cur]
    prev = state.get("prev_snapshot_id") or (earlier[-1]["snapshot_id"] if earlier else None)
    by_id = {s["snapshot_id"]: s for s in snaps}
    return {"snapshot_id": cur, "prev_snapshot_id": prev, "snap_meta": by_id[cur],
            "prev_meta": by_id.get(prev)}


def node_diff(state: dict) -> dict:
    tb: ToolBox = state["tb"]
    events = tb.compare_snapshots(state["prev_snapshot_id"], state["snapshot_id"]) if state["prev_snapshot_id"] else []
    return {"events": events}


def node_match(state: dict) -> dict:
    tb: ToolBox = state["tb"]
    cur_recs = tb.get_shortage_records(snapshot_id=state["snapshot_id"])
    matches: dict[str, list] = {}
    for r in cur_recs:
        matches[r.record_key] = tb.find_catalogue_matches(r.record_key, state["snapshot_id"])
    prev_matches: dict[str, list] = {}
    cur_keys = {r.record_key for r in cur_recs}
    if state["prev_snapshot_id"]:
        for e in state["events"]:
            k = e["record_key"]
            if k not in cur_keys and k not in prev_matches:      # e.g. REMOVED records
                prev_matches[k] = tb.find_catalogue_matches(k, state["prev_snapshot_id"])
    return {"records": {r.record_key: r for r in cur_recs}, "matches": matches, "prev_matches": prev_matches}


def _resolution_hint(recs) -> bool:
    return any(_HINT.search(f"{r.availability} {r.related_info}") for r in recs)


def node_exposure(state: dict) -> dict:
    """Group auto-accepted matches by SKU and call the exposure tool."""
    tb: ToolBox = state["tb"]
    by_sku: dict[str, list] = {}
    for key, ms in state["matches"].items():
        rec = state["records"][key]
        for m in ms:
            if m.outcome == AUTO and is_active(rec.status):
                by_sku.setdefault(m.sku, []).append((rec, m))
    exposures = {}
    for sku, pairs in by_sku.items():
        exposures[sku] = tb.calculate_exposure(sku, [r.company_name for r, _ in pairs])
    return {"by_sku": by_sku, "exposures": exposures}


def _rec_phrase(n: int) -> str:
    return "a current shortage record" if n == 1 else f"{n} current shortage records"


def compose_rationale(item: dict) -> str:
    sku, exp, ms = item["sku"], item["exposure"], item["matches"]
    n = len(ms)
    if item["priority"] == "INSUFFICIENT_EVIDENCE":
        return (f"Cannot prioritise {sku}: it matches {_rec_phrase(n)}, but the simulated catalogue lacks "
                "demand or lead-time data. No priority is assigned.")
    tiers = {m["tier"] for m in ms}
    if tiers == {1}:
        how = f"its identifier matches {_rec_phrase(n)}"
    elif tiers == {2}:
        how = f"ingredient, strength and dosage form match {_rec_phrase(n)} (no identifier match)"
    else:
        how = f"{_rec_phrase(n)} match (strongest evidence: tier {min(tiers)})"
    parts = [f"Review {sku}: {how}."]
    s = f"Simulated inventory covers {fmt_num(exp['cover_days'])} days against a {fmt_num(exp['lead_time_days'])}-day replenishment assumption"
    if exp["shortfall_days"]:
        s += f", a shortfall of {fmt_num(exp['shortfall_days'])} days"
    parts.append(s + ".")
    if exp["order_value_at_risk"]:
        when = "before" if exp["order_arrives_before_stockout"] else "after"
        parts.append(f"Open orders worth {fmt_money(exp['order_value_at_risk'])} (simulated) with a supplier named in the "
                     f"shortage record are due in {fmt_num(exp['open_order_due_days'])} days, {when} projected stock-out.")
    parts.append("The source text may mention a timeline, but it is not parsed or confirmed." if item["resolution_hint"]
                 else "The source gives no confirmed resolution date.")
    parts.append(f"Priority {item['priority']}: {RULE_TEXT[item['priority_rule']]}.")
    return " ".join(parts)


def _missing_info(recs, exp, matches) -> list[str]:
    out = []
    if not any(r.availability.strip() for r in recs):
        out.append("availability text is empty in the source record")
    if not any(r.shortage_reason.strip() for r in recs):
        out.append("no shortage reason given in the source record")
    if not _resolution_hint(recs):
        out.append("no confirmed resolution date in source")
    if any(not r.strengths for r in recs):
        out.append("source record lists no strength (match rests on identifier)")
    if exp["supplier_named_in_shortage"] is None and exp["open_order_qty"]:
        out.append("cannot tell whether open orders are with the affected supplier")
    if exp["cover_days"] is None:
        out.append("simulated demand data missing")
    return out


def node_prioritise(state: dict) -> dict:
    items = []
    for sku, pairs in state["by_sku"].items():
        exp = state["exposures"][sku]
        recs = [r for r, _ in pairs]
        priority, rule = prioritise(exp, any(r.status.lower() == "to be discontinued" for r in recs))
        keys = {r.record_key for r in recs}
        sku_obj = dbm.get_sku(state["tb"].conn, sku)
        item = {
            "sku": sku, "name": f"{sku_obj.name} {sku_obj.strength} {sku_obj.dosage_form}".strip(),
            "state": "insufficient_evidence" if priority == "INSUFFICIENT_EVIDENCE" else "prioritised",
            "priority": priority, "priority_rule": rule,
            "matches": [{**m.to_dict(), "company": r.company_name, "status": r.status,
                         "source": "public_fda_shortage"} for r, m in pairs],
            "exposure": exp,
            "changes": [e for e in state["events"] if e["record_key"] in keys],
            "missing_info": _missing_info(recs, exp, pairs),
            "resolution_hint": _resolution_hint(recs),
            "evidence": (
                [{"source": "public_fda_shortage", **r.summary()} for r in recs] +
                [{"source": "simulated_catalogue", "sku": sku, "on_hand": exp["on_hand"],
                  "daily_demand": exp["daily_demand"], "lead_time_days": exp["lead_time_days"],
                  "calc_id": exp["calc_id"]}]),
        }
        item["rationale"] = compose_rationale(item)
        narrator: Callable | None = state.get("narrator")
        if narrator:
            draft = narrator({k: v for k, v in item.items() if k != "rationale"})
            ok, bad = numbers_ok(draft or "", item)
            if draft and ok and sku in draft:
                item["rationale"], item["narrator_used"] = draft, True
            else:
                item["narrator_used"] = False
                item["narrator_rejected_numbers"] = bad
        items.append(item)
    items.sort(key=lambda i: (PRIORITY_ORDER[i["priority"]], -(i["exposure"]["shortfall_days"] or 0), i["sku"]))
    return {"items": items}


def node_compose(state: dict) -> dict:
    tb: ToolBox = state["tb"]
    recs, matches = state["records"], state["matches"]
    review_queue, rejected, unmatched = [], [], []
    for key, ms in matches.items():
        rec = recs[key]
        if not is_active(rec.status):
            continue
        for m in ms:
            if m.outcome == REVIEW:
                review_queue.append({"state": "needs_review", **m.to_dict(), "record": rec.summary(),
                                     "why": m.unknowns + m.conflicts or m.reasons})
            elif m.outcome == REJECT:
                rejected.append({"record_key": key, "generic_name": rec.generic_name, "sku": m.sku,
                                 "why_not_flagged": m.conflicts})
        if not any(m.outcome in (AUTO, REVIEW) for m in ms):
            unmatched.append({"state": "no_match", "record_key": key, "generic_name": rec.generic_name,
                              "status": rec.status,
                              "reason": "no catalogue candidate" if not ms else "all candidates rejected on attribute conflicts"})
    # recently cleared: RESOLVED / REMOVED events attributable to a catalogue SKU
    cleared = []
    for e in state["events"]:
        if e["event"] in ("RESOLVED", "REMOVED"):
            ms = matches.get(e["record_key"]) or state["prev_matches"].get(e["record_key"], [])
            for m in ms:
                if m.outcome == AUTO:
                    cleared.append({"sku": m.sku, "event": e["event"], "record_key": e["record_key"],
                                    "generic_name": e["generic_name"],
                                    "note": "no longer an active shortage in the latest snapshot; verify before standing down"})
    briefing = {
        "generated_at": now_iso(),
        "snapshot": {k: state["snap_meta"][k] for k in ("snapshot_id", "fetched_at", "source", "is_synthetic", "label")},
        "previous_snapshot": ({k: state["prev_meta"][k] for k in ("snapshot_id", "fetched_at", "is_synthetic", "label")}
                              if state["prev_meta"] else None),
        "question": "Which products in our portfolio need attention today, what changed, and what evidence supports that priority?",
        "summary": {
            "flagged_skus": len(state["items"]),
            "high": sum(i["priority"] == "HIGH" for i in state["items"]),
            "medium": sum(i["priority"] == "MEDIUM" for i in state["items"]),
            "low": sum(i["priority"] == "LOW" for i in state["items"]),
            "insufficient_evidence": sum(i["priority"] == "INSUFFICIENT_EVIDENCE" for i in state["items"]),
            "needs_review": len(review_queue), "unmatched_records": len(unmatched),
            "near_matches_not_flagged": len(rejected), "changes": len(state["events"]),
        },
        "items": state["items"], "review_queue": review_queue, "recently_cleared": cleared,
        "near_matches_not_flagged": rejected, "unmatched_records": unmatched,
        "changes": state["events"], "scope_notes": SCOPE_NOTES,
    }
    briefing = tb.submit_briefing(briefing)
    briefing["trace"] = tb.trace
    return {"briefing": briefing}


NODES = [("load", node_load), ("diff", node_diff), ("match", node_match),
         ("exposure", node_exposure), ("prioritise", node_prioritise), ("compose", node_compose)]


def run_briefing(conn, snapshot_id: int | None = None, prev_snapshot_id: int | None = None,
                 narrator: Callable | None = None) -> dict:
    state: dict = {"tb": ToolBox(conn), "snapshot_id": snapshot_id, "prev_snapshot_id": prev_snapshot_id,
                   "narrator": narrator}
    for _, fn in NODES:
        state.update(fn(state))
    return state["briefing"]
