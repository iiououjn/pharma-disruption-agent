"""Conservative real-world matching guardrails over the original matcher.

No new candidate is automatically accepted. Multiple strengths, ratio notation,
brand-only identities and incompatible injectable presentations require a human.
The original matcher and all its regression tests remain unmodified.
"""
from __future__ import annotations

import json
import re

from pdsia import db as dbm
from pdsia.agent import NODES
from pdsia.matcher import AUTO, REVIEW, MatchResult
from pdsia.normalize import ndc_match, norm_text, parse_strength_pieces, strength_options
from pdsia.tools import ToolBox

_RATIO = re.compile(r"\b\d+(?:\.\d+)?\s*:\s*\d[\d,]*(?:\.\d+)?\b")
_POWDER = re.compile(r"\b(?:powder|lyophili[sz]ed|freeze[- ]dried)\b", re.I)
_SOLUTION = re.compile(r"\b(?:solution|premixed|ready[- ]to[- ]use)\b", re.I)
_INJECTABLE = re.compile(r"inject|intravenous|\biv\b|infusion", re.I)


def ambiguous_strenghs(strengths: list[str]) -> bool:
    """A single ingredient with several *distinct* offered strengths is ambiguous.

    Combination products are handled by the original component-aware matcher;
    guard conservatively whenever several source entries are present.
    """
    if len(strengths) < 2:
        return False
    values = [norm_text(s) for s in strengths if norm_text(s)]
    return len(set(values)) > 1


def safety_reasons(record: dbm.ShortageRecord, sku: dbm.Sku) -> list[str]:
    flags = []
    if ambiguous_strenghs(record.strengths):
        flags.append("multiple record strengths: confirm the exact strength for this SKU")
    if any(_RATIO.search(s) for s in record.strengths) or _RATIO.search(record.presentation):
        flags.append("ratio concentration (e.g. 1:100,000) requires pharmacist verification")
    rf = f"{record.dosage_form} {record.presentation}"
    sf = f"{sku.dosage_form} {sku.name}"
    if _INJECTABLE.search(rf) and _INJECTABLE.search(sf):
        if (_POWDER.search(rf) and _SOLUTION.search(sf)) or (_SOLUTION.search(rf) and _POWDER.search(sf)):
            flags.append("injectable powder versus ready-made solution: do not equate without review")
    if not record.generic_name.strip() and record.proprietary_name.strip():
        flags.append("brand-only FDA record: active ingredient cannot be verified")
    return flags


def harden_match(record: dbm.ShortageRecord, sku: dbm.Sku, result: MatchResult) -> MatchResult:
    """Downgrade unverified auto-accepts. Explicit human accept decisions remain authoritative."""
    flags = safety_reasons(record, sku)
    if flags and result.outcome == AUTO and result.human_decision != "accept":
        result.outcome, result.tier = REVIEW, 3
        result.score = min(result.score, 0.5)
        result.unknowns.extend(flags)
        result.reasons.append("safety guardrail: automatic acceptance withheld")
    return result


class HardenedToolBox(ToolBox):
    def find_catalogue_matches(self, record_key: str, snapshot_id: int | None = None):
        results = super().find_catalogue_matches(record_key, snapshot_id)
        records = self.get_shortage_records(snapshot_id=snapshot_id, record_key=record_key)
        if not records:
            return results
        record = records[0]
        catalogue = dbm.get_catalogue(self.conn)
        by_sku = {sku.sku: sku for sku in catalogue}
        for r in results:
            harden_match(record, by_sku[r.sku], r)
        # A brand-only entry that the original matcher cannot reason about is
        # an abstention, even if brand or NDC agrees; never turn it into AUTO.
        if not record.generic_name.strip() and record.proprietary_name.strip():
            seen = {r.sku for r in results}
            for sku in catalogue:
                if sku.sku in seen:
                    continue
                brand_agrees = norm_text(record.proprietary_name) == norm_text(sku.name)
                ndc_agrees = ndc_match(record.package_ndcs, sku.ndcs)
                if brand_agrees or ndc_agrees:
                    results.append(MatchResult(record.record_key, sku.sku, 3, REVIEW, 0.3,
                        reasons=["brand or NDC candidate only"],
                        unknowns=["brand-only record: independently verify ingredient, strength and form"]))
        # Keep persisted candidate states in step with the returned guarded states.
        for r in results:
            self.conn.execute("INSERT OR REPLACE INTO match_candidates VALUES (?,?,?,?,?,?,?)",
                              (record.snapshot_id, record.record_key, r.sku, r.tier, r.outcome,
                               r.score, json.dumps({"reasons": r.reasons, "conflicts": r.conflicts,
                                                    "unknowns": r.unknowns})))
        self.conn.commit()
        return results


def run_hardened_briefing(conn, snapshot_id=None, prev_snapshot_id=None, narrator=None) -> dict:
    state = {"tb": HardenedToolBox(conn), "snapshot_id": snapshot_id,
             "prev_snapshot_id": prev_snapshot_id, "narrator": narrator}
    for _, node in NODES:
        state.update(node(state))
    state["briefing"]["matching_mode"] = "guarded_conservative"
    return state["briefing"]
