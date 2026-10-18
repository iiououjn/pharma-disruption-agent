"""Evaluation harness: matching P/R vs a keyword baseline, metamorphic (mutation) tests,
abstention checks, numeric faithfulness and change-detection accuracy.

Gold labels live in data/gold/gold_matches.csv (hand-written, see README for the circularity caveat).
Unlisted (record, sku) pairs default to non_match; any unlisted pair the matcher AUTO-accepts is
reported separately for manual checking rather than silently counted.
"""
from __future__ import annotations

import copy
import csv
import re
from pathlib import Path

from . import db as dbm
from . import ingest
from .agent import run_briefing
from .diff import diff_snapshots
from .exposure import calculate_exposure
from .matcher import AUTO, REVIEW, REJECT, match, match_all
from .normalize import FORM_WORDS, SALTS, parse_components, parse_strength_pieces, strength_options
from .tools import numbers_ok, is_active

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "data" / "gold" / "gold_matches.csv"


def load_gold(path: Path = GOLD_PATH) -> dict[tuple[str, str], str]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["record_key"], r["sku"]): r["label"] for r in csv.DictReader(fh)}


def all_records(conn) -> dict[str, dbm.ShortageRecord]:
    """Latest version of every record key across all stored snapshots."""
    out: dict[str, dbm.ShortageRecord] = {}
    for s in dbm.list_snapshots(conn):
        for r in dbm.get_records(conn, s["snapshot_id"]):
            out[r.record_key] = r
    return out


def keyword_baseline(record, sku) -> bool:
    """Naive baseline: any shared ingredient-like word (salts/dosage words removed)."""
    def toks(s):
        return {t for t in re.findall(r"[a-z]+", s.lower())
                if t not in FORM_WORDS and t not in SALTS and t not in {"and", "with"} and len(t) > 2}
    return bool(toks(sku.name) & toks(record.generic_name))


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}


def evaluate_matching(conn, gold: dict | None = None) -> dict:
    gold = gold or load_gold()
    recs, cat = all_records(conn), dbm.get_catalogue(conn)
    sys_c = {"tp": 0, "fp": 0, "fn": 0}
    base_c = {"tp": 0, "fp": 0, "fn": 0}
    review = {"uncertain_total": 0, "uncertain_routed_to_review": 0, "match_sent_to_review": 0,
              "non_match_sent_to_review": 0}
    unlisted_auto, unlisted_baseline = [], []
    missing_gold_keys = [k for k in gold if k[0] not in recs or k[1] not in {s.sku for s in cat}]
    for key, rec in recs.items():
        for sku in cat:
            label = gold.get((key, sku.sku), "non_match")
            m = match(rec, sku)
            sys_pos = bool(m and m.outcome == AUTO)
            base_pos = keyword_baseline(rec, sku)
            truth = label == "match"
            for pred, counts in ((sys_pos, sys_c), (base_pos, base_c)):
                if pred and truth:
                    counts["tp"] += 1
                elif pred and not truth:
                    counts["fp"] += 1
                elif truth and not pred:
                    counts["fn"] += 1
            if (key, sku.sku) not in gold:
                if sys_pos:
                    unlisted_auto.append((key, sku.sku))
                if base_pos:
                    unlisted_baseline.append((key, sku.sku))
            if label == "uncertain":
                review["uncertain_total"] += 1
                review["uncertain_routed_to_review"] += int(bool(m and m.outcome == REVIEW))
            if m and m.outcome == REVIEW:
                if label == "match":
                    review["match_sent_to_review"] += 1
                elif label == "non_match":
                    review["non_match_sent_to_review"] += 1
    return {
        "gold_pairs": len(gold), "gold_keys_not_in_data": missing_gold_keys,
        "pairs": {"matcher": _prf(**sys_c), "keyword_baseline": _prf(**base_c)},
        "review_queue": review,
        "unlisted_matcher_auto_accepts": unlisted_auto,
        "unlisted_baseline_hits": unlisted_baseline,
    }


# --------------------------------------------------------------- metamorphic
def _scaled_strength(sku, factor: float) -> list[str] | None:
    comps = parse_components(sku.name)
    opts = strength_options(len(comps), parse_strength_pieces(sku.strength or None))
    if not opts:
        return None
    parts = []
    for s in opts[0]:
        unit = s.dim.replace("ml", "mL")
        parts.append(f"{s.value * factor:g} {unit}")
    return ["; ".join(parts)] if len(parts) > 1 else parts


def metamorphic_checks(conn, gold: dict | None = None) -> dict:
    """Perturb every gold 'match' pair that is auto-accepted and check the matcher responds correctly."""
    gold = gold or load_gold()
    recs = all_records(conn)
    cat = {s.sku: s for s in dbm.get_catalogue(conn)}
    failures, checks = [], 0
    for (key, sku_id), label in gold.items():
        if label != "match" or key not in recs or sku_id not in cat:
            continue
        rec, sku = recs[key], cat[sku_id]
        base = match(rec, sku)
        if not base or base.outcome != AUTO:
            continue

        def check(name, mutated, predicate):
            nonlocal checks
            checks += 1
            m = match(mutated, sku)
            if not predicate(m):
                failures.append({"pair": (key, sku_id), "mutation": name, "got": m.outcome if m else None})

        scaled = _scaled_strength(sku, 2.0)
        if scaled:
            r = copy.deepcopy(rec); r.strengths = scaled
            check("strength x2 must not be auto-accepted", r, lambda m: m is None or m.outcome != AUTO)
        r = copy.deepcopy(rec); r.strengths = []
        if base.tier == 2:
            check("missing strength must route to review", r, lambda m: m is not None and m.outcome == REVIEW)
        r = copy.deepcopy(rec)
        r.dosage_form = "Capsule" if parse_form_family(rec.dosage_form) != "capsule" else "Tablet"
        check("different dosage-form family must not be auto-accepted", r, lambda m: m is None or m.outcome != AUTO)
        r = copy.deepcopy(rec); r.generic_name = "  " + rec.generic_name.upper().replace(" ", "   ") + " "
        check("case/whitespace noise must not change outcome", r, lambda m, b=base: m is not None and m.outcome == b.outcome and m.tier == b.tier)
    return {"checks": checks, "failures": failures}


def parse_form_family(text):
    from .normalize import parse_form
    f = parse_form(text)
    return f.family if f else None


# ---------------------------------------------------------- faithfulness
def numeric_faithfulness(conn, briefing: dict) -> dict:
    """Every number in every rationale must be backed by that item's tool output, and every exposure
    block must equal an independent recomputation from the catalogue."""
    n_numbers = bad_numbers = recompute_mismatch = 0
    from .exposure import numbers_in
    for it in briefing["items"]:
        ok, bad = numbers_ok(it["rationale"], it)
        n_numbers += len(numbers_in(it["rationale"]))
        bad_numbers += len(bad)
        sku = dbm.get_sku(conn, it["sku"])
        again = calculate_exposure(sku, [m["company"] for m in it["matches"]])
        if again != it["exposure"]:
            recompute_mismatch += 1
    return {"items": len(briefing["items"]), "numbers_checked": n_numbers,
            "unbacked_numbers": bad_numbers, "exposure_recompute_mismatches": recompute_mismatch}


# ----------------------------------------------------------- change detection
EXPECTED_EVENTS = {
    ("NEW_LISTING", "R-002"), ("NEW_LISTING", "R-013"), ("NEW_LISTING", "R-014"), ("NEW_LISTING", "R-019"),
    ("REMOVED", "R-017"), ("AVAILABILITY_TEXT_CHANGED", "R-001"), ("REASON_CHANGED", "R-005"),
    ("STATUS_CHANGE", "R-007"), ("AVAILABILITY_TEXT_CHANGED", "R-008"), ("RESOLVED", "R-012"),
}


def change_detection_eval(conn) -> dict:
    snaps = dbm.list_snapshots(conn)
    ev = diff_snapshots(dbm.get_records(conn, snaps[-2]["snapshot_id"]), dbm.get_records(conn, snaps[-1]["snapshot_id"]))
    got = {(e["event"], e["record_key"]) for e in ev}
    tp, fp, fn = len(got & EXPECTED_EVENTS), len(got - EXPECTED_EVENTS), len(EXPECTED_EVENTS - got)
    return {**_prf(tp, fp, fn), "unexpected": sorted(got - EXPECTED_EVENTS), "missed": sorted(EXPECTED_EVENTS - got)}


# ------------------------------------------------------------- abstention
def fresh_db(snapshots=("snapshot_2026-10-04.json", "snapshot_2026-10-18.json")):
    conn = dbm.connect(":memory:")
    for s in snapshots:
        ingest.load_fixture(conn, s)
    dbm.load_catalogue_csv(conn, ROOT / "data" / "simulated" / "catalogue.csv")
    return conn


def abstention_checks() -> list[dict]:
    out = []

    def rec(name, ok, detail=""):
        out.append({"check": name, "passed": bool(ok), "detail": detail})

    b = run_briefing(fresh_db())
    flagged = {i["sku"] for i in b["items"]}
    rec("missing strength -> needs_review, not prioritised",
        any(r["record_key"] == "R-009" and r["sku"] == "SKU-111" for r in b["review_queue"]) and "SKU-111" not in flagged)
    rec("no catalogue candidate -> no_match state",
        any(u["record_key"] == "R-014" and u["reason"] == "no catalogue candidate" for u in b["unmatched_records"]))
    wrong = {"SKU-102", "SKU-103", "SKU-106", "SKU-108", "SKU-112", "SKU-113", "SKU-114", "SKU-121"} & flagged
    rec("near-miss SKUs (same ingredient, wrong strength/form/salt) are not flagged", not wrong, f"wrongly flagged: {sorted(wrong)}")
    item13 = next(i for i in b["items"] if i["sku"] == "SKU-116")
    rec("sparse source text -> missing info reported, no invented resolution date",
        "availability text is empty in the source record" in item13["missing_info"]
        and "no confirmed resolution date" in item13["rationale"])
    rec("SKU with no shortage candidates is not flagged (SKU-115)", "SKU-115" not in flagged)

    conn = fresh_db()
    conn.execute("UPDATE sim_catalogue SET daily_demand=NULL WHERE sku='SKU-104'")
    conn.commit()
    b2 = run_briefing(conn)
    i104 = next(i for i in b2["items"] if i["sku"] == "SKU-104")
    rec("missing demand -> insufficient_evidence, no priority invented",
        i104["priority"] == "INSUFFICIENT_EVIDENCE" and i104["state"] == "insufficient_evidence"
        and "Cannot prioritise" in i104["rationale"])

    sparse = dbm.ShortageRecord(record_key="X", generic_name="Cisplatin")
    sku104 = next(s for s in dbm.get_catalogue(fresh_db()) if s.sku == "SKU-104")
    m = match(sparse, sku104)
    rec("ingredient-only record (no form/strength/id) is never auto-accepted", m is not None and m.outcome == REVIEW)
    return out


def run_all() -> dict:
    conn = fresh_db()
    briefing = run_briefing(conn)
    return {
        "matching": evaluate_matching(conn),
        "metamorphic": metamorphic_checks(conn),
        "numeric_faithfulness": numeric_faithfulness(conn, briefing),
        "change_detection": change_detection_eval(conn),
        "abstention": abstention_checks(),
    }


def format_report(res: dict) -> str:
    m = res["matching"]
    L = ["# Evaluation report (offline synthetic fixtures)", ""]
    L.append(f"Gold pairs hand-labelled: {m['gold_pairs']}")
    L += ["", "## Matching over every record x SKU pair (unlisted pairs = non_match; precision/recall ignore true negatives)",
          "", "| system | TP | FP | FN | precision | recall | F1 |", "|---|---|---|---|---|---|---|"]
    for name in ("matcher", "keyword_baseline"):
        x = m["pairs"][name]
        L.append(f"| {name} | {x['tp']} | {x['fp']} | {x['fn']} | {x['precision']} | {x['recall']} | {x['f1']} |")
    rq = m["review_queue"]
    L += ["", f"Review routing: {rq['uncertain_routed_to_review']}/{rq['uncertain_total']} genuinely uncertain pairs routed to review; "
          f"{rq['match_sent_to_review']} true matches and {rq['non_match_sent_to_review']} non-matches sent to review.",
          f"Unlisted pairs auto-accepted by matcher (need manual check): {m['unlisted_matcher_auto_accepts']}",
          f"Unlisted pairs hit by baseline: {len(m['unlisted_baseline_hits'])}"]
    mm = res["metamorphic"]
    L += ["", f"## Metamorphic checks: {mm['checks'] - len(mm['failures'])}/{mm['checks']} passed"]
    nf = res["numeric_faithfulness"]
    L += ["", f"## Numeric faithfulness: {nf['numbers_checked']} numbers in {nf['items']} rationales, "
          f"{nf['unbacked_numbers']} unbacked, {nf['exposure_recompute_mismatches']} recompute mismatches"]
    cd = res["change_detection"]
    L += ["", f"## Change detection: precision {cd['precision']} recall {cd['recall']} (unexpected {cd['unexpected']}, missed {cd['missed']})", "",
          "## Abstention"]
    for a in res["abstention"]:
        L.append(f"- [{'PASS' if a['passed'] else 'FAIL'}] {a['check']} {a['detail']}")
    return "\n".join(L)
