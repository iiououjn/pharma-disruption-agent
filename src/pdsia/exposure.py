"""Pure, deterministic exposure calculations on SIMULATED inventory assumptions.

Definitions (documented so the briefing can quote them):
  cover_days              = on_hand / daily_demand                       (stock only, orders excluded)
  shortfall_days          = max(lead_time_days - cover_days, 0)
  order_arrives_before_stockout = open_order_due_days <= cover_days
  open_order_value        = open_order_qty * unit_cost
  order_value_at_risk     = open_order_value if the order's supplier is named in a matched shortage
                            record, 0 if it is not, None if no company information is available.
Missing inputs return None instead of a guess. Every result carries a deterministic calc_id.
"""
from __future__ import annotations

import hashlib
import json
import re

from .db import Sku
from .normalize import norm_text

BUFFER_FACTOR = 1.5   # cover below 1.5x lead time counts as a thin buffer


def _r(x):
    return None if x is None else round(float(x), 1)


def days_of_cover(on_hand, daily_demand):
    if on_hand is None or daily_demand is None or daily_demand <= 0:
        return None
    return on_hand / daily_demand


def supplier_named(supplier: str, companies: list[str]) -> bool | None:
    comps = [norm_text(c) for c in companies if norm_text(c)]
    if not comps or not norm_text(supplier):
        return None
    s = norm_text(supplier)
    return any(s in c or c in s for c in comps)


def calculate_exposure(sku: Sku, companies: list[str]) -> dict:
    cover = days_of_cover(sku.on_hand, sku.daily_demand)
    lead = sku.lead_time_days
    shortfall = None if (cover is None or lead is None) else max(lead - cover, 0.0)
    qty, due, cost = sku.open_order_qty, sku.open_order_due_days, sku.unit_cost
    arrives = None if (cover is None or due is None or not qty) else (due <= cover)
    value = None if (qty is None or cost is None) else qty * cost
    named = supplier_named(sku.supplier, companies)
    if value is None:
        at_risk = None
    elif not qty:
        at_risk = 0.0
    elif named is None:
        at_risk = None
    else:
        at_risk = value if named else 0.0
    out = {
        "sku": sku.sku,
        "on_hand": _r(sku.on_hand), "daily_demand": _r(sku.daily_demand),
        "cover_days": _r(cover), "lead_time_days": _r(lead),
        "shortfall_days": _r(shortfall),
        "open_order_qty": _r(qty), "open_order_due_days": _r(due),
        "order_arrives_before_stockout": arrives,
        "open_order_value": _r(value), "order_value_at_risk": _r(at_risk),
        "supplier_named_in_shortage": named,
        "source": "simulated_catalogue",
    }
    out["calc_id"] = "calc-" + hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()[:10]
    return out


def prioritise(exp: dict, any_discontinued: bool) -> tuple[str, str]:
    """Priority is decided by code, never by a language model."""
    cover, lead, short = exp["cover_days"], exp["lead_time_days"], exp["shortfall_days"]
    if cover is None or lead is None:
        return "INSUFFICIENT_EVIDENCE", "no_demand_or_lead_time_data"
    if short and short > 0:
        return "HIGH", "cover_below_lead_time"
    if cover < BUFFER_FACTOR * lead:
        return "MEDIUM", "thin_buffer_vs_lead_time"
    if any_discontinued:
        return "MEDIUM", "listed_as_to_be_discontinued"
    return "LOW", "adequate_cover"


def fmt_num(x) -> str:
    if x is None:
        return "n/a"
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.1f}"


def fmt_money(x) -> str:
    return f"{float(x):,.0f}"


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """All numbers in text after stripping identifiers (SKU-/R-/calc- ids, dates, quarters)."""
    t = re.sub(r"\bSKU-\d+\b|\bR-\d+\b|\bH-[0-9a-f]+\b|\bcalc-[0-9a-f]+\b", " ", text)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|\bQ[1-4]\b", " ", t)
    return [float(m.replace(",", "")) for m in _NUM.findall(t)]
