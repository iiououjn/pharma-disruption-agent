"""Tiered shortage-record -> catalogue matcher.

Tier 1  identifier (NDC) match, no attribute conflict           -> auto
Tier 2  ingredient set + strength + dosage form all agree       -> auto
Tier 3  ingredient matches but something is missing/uncomparable,
        or NDC matches while an attribute conflicts             -> review
Tier 4  ingredient overlaps but an attribute CONFLICTS          -> reject (the near-match trap)
No overlap in ingredients -> no candidate (None).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import ShortageRecord, Sku
from .normalize import (
    parse_components, parse_form, parse_strength_pieces, strength_options,
    strengths_equal, ndc_match,
)

AUTO, REVIEW, REJECT = "auto", "review", "reject"


@dataclass
class MatchResult:
    record_key: str
    sku: str
    tier: int
    outcome: str
    score: float
    reasons: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)
    human_decision: str | None = None

    @property
    def confidence(self) -> str:
        if self.human_decision == "accept":
            return "high (human-confirmed)"
        if self.outcome == AUTO:
            return "high" if self.tier == 1 else ("medium-high" if self.score >= 0.9 else "medium")
        return "low" if self.outcome == REVIEW else "none"

    def to_dict(self) -> dict:
        return {"record_key": self.record_key, "sku": self.sku, "tier": self.tier, "outcome": self.outcome,
                "score": round(self.score, 2), "confidence": self.confidence, "reasons": self.reasons,
                "conflicts": self.conflicts, "unknowns": self.unknowns, "human_decision": self.human_decision}


def _strength_verdict(rc, sc, rec_strengths, sku_strength):
    ropt = strength_options(len(rc), parse_strength_pieces(rec_strengths or None))
    sopt = strength_options(len(sc), parse_strength_pieces(sku_strength or None))
    if ropt is None:
        return "unknown", "record strength missing or unparseable"
    if sopt is None:
        return "unknown", "catalogue strength missing or unparseable"
    r_index = {c.base: i for i, c in enumerate(rc)}
    s_index = {c.base: i for i, c in enumerate(sc)}
    bases = [b for b in r_index if b in s_index]
    comparable = False
    for ro in ropt:
        for so in sopt:
            dims_ok = all(ro[r_index[b]].dim == so[s_index[b]].dim for b in bases)
            if not dims_ok:
                continue
            comparable = True
            if all(strengths_equal(ro[r_index[b]], so[s_index[b]]) for b in bases):
                return "equal", "strength agrees numerically"
    if not comparable:
        return "unknown", "strengths expressed in non-comparable units"
    return "differs", "strength differs"


def match(record: ShortageRecord, sku: Sku) -> MatchResult | None:
    rc, sc = parse_components(record.generic_name), parse_components(sku.name)
    rb, sb = {c.base for c in rc}, {c.base for c in sc}
    if not rb & sb:
        return None
    reasons: list[str] = []
    conflicts: list[str] = []
    unknowns: list[str] = []
    score_adj = 0.0

    # ingredient set
    if rb != sb:
        conflicts.append(f"ingredient set differs (record {sorted(rb)} vs catalogue {sorted(sb)}): "
                         "combination product vs single agent or different mix")
    else:
        reasons.append("ingredient set equal")

    # salts (only when both sides state one for the same base)
    rs = {c.base: c.salts for c in rc}
    ss = {c.base: c.salts for c in sc}
    for b in rb & sb:
        if rs[b] and ss[b] and rs[b] != ss[b]:
            conflicts.append(f"salt differs for {b}: {sorted(rs[b])} vs {sorted(ss[b])}")
        elif bool(rs[b]) != bool(ss[b]):
            reasons.append(f"salt unspecified on one side for {b}")
            score_adj -= 0.05

    # dosage form
    rform = parse_form(record.dosage_form)
    if rform is None:                      # infer from the name only if it actually names a form
        inferred = parse_form(record.generic_name)
        rform = inferred if inferred and inferred.family != "other" else None
    sform = parse_form(sku.dosage_form)
    if rform is None or sform is None:
        unknowns.append("dosage form missing on one side")
    elif "unspecified" in (rform.family, sform.family):
        unknowns.append("dosage form too generic to compare (e.g. bare 'Solution')")
    else:
        if rform.family != sform.family:
            conflicts.append(f"dosage form family differs ({rform.family} vs {sform.family})")
        elif rform.release != sform.release:
            conflicts.append(f"release profile differs ({rform.release} vs {sform.release})")
        else:
            reasons.append(f"dosage form agrees ({sform.family}, {sform.release} release)")

    # strength (only meaningful when ingredient sets are equal)
    if rb == sb:
        verdict, why = _strength_verdict(rc, sc, record.strengths, sku.strength)
        if verdict == "equal":
            reasons.append(why)
        elif verdict == "differs":
            conflicts.append(why)
        else:
            unknowns.append(why)

    ids = ndc_match(record.package_ndcs, sku.ndcs)
    if ids:
        if not conflicts:
            reasons.insert(0, "identifier (NDC) match")
            return MatchResult(record.record_key, sku.sku, 1, AUTO, 1.0, reasons, conflicts, unknowns)
        reasons.insert(0, "identifier (NDC) matches but attributes conflict -> needs human review")
        return MatchResult(record.record_key, sku.sku, 3, REVIEW, 0.4, reasons, conflicts, unknowns)
    if conflicts:
        return MatchResult(record.record_key, sku.sku, 4, REJECT, 0.0, reasons, conflicts, unknowns)
    if unknowns:
        return MatchResult(record.record_key, sku.sku, 3, REVIEW, 0.5, reasons, conflicts, unknowns)
    return MatchResult(record.record_key, sku.sku, 2, AUTO, round(0.9 + score_adj, 2), reasons, conflicts, unknowns)


def match_all(record: ShortageRecord, catalogue: list[Sku],
              decisions: dict[tuple[str, str], str] | None = None) -> list[MatchResult]:
    """All candidates for one record; human review decisions override the automatic outcome."""
    decisions = decisions or {}
    out = []
    for sku in catalogue:
        m = match(record, sku)
        if m is None:
            continue
        d = decisions.get((record.record_key, sku.sku))
        if d == "accept" and m.outcome != AUTO:
            m.outcome, m.human_decision = AUTO, "accept"
            m.reasons.append("accepted by human reviewer")
        elif d == "reject":
            m.outcome, m.human_decision = REJECT, "reject"
            m.reasons.append("rejected by human reviewer")
        out.append(m)
    return out
