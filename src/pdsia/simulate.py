"""Generate a SIMULATED catalogue (and auto-labelled gold) from a snapshot's records.

Use this on a LIVE pull so the demo has catalogue entries that actually relate to real shortage
records. Each derived SKU is perturbed in a known way, so its gold label is known by construction
rather than by the matcher's own opinion:

  exact / exact_ndc  -> match
  strength_x2        -> non_match   (same ingredient, wrong strength)
  form_swap          -> non_match   (same ingredient, wrong dosage form)
  strength_blank     -> uncertain   (strength unknown, should go to review)

Caveat: the same real product can be listed by several companies, so a derived SKU may truly match
records beyond its source record. Such pairs show up as 'unlisted matcher auto-accepts' in the
evaluation and need a manual look; they are not counted as errors automatically.
"""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path

from .db import ShortageRecord
from .normalize import FORM_WORDS, parse_form, parse_strength_pieces

RANDOM_NAMES = ["Atorvastatin Calcium", "Amlodipine Besylate", "Omeprazole", "Losartan Potassium",
                "Sertraline Hydrochloride", "Levothyroxine Sodium", "Montelukast Sodium", "Gabapentin",
                "Pantoprazole Sodium", "Rosuvastatin Calcium"]
FIELDS = ["sku", "name", "strength", "dosage_form", "ndcs", "supplier", "on_hand", "daily_demand",
          "open_order_qty", "open_order_due_days", "unit_cost", "lead_time_days"]


def _catalogue_name(generic: str) -> str:
    words = [w for w in re.split(r"\s+", generic.replace(",", " ")) if w.lower() not in FORM_WORDS]
    return " ".join(words) or generic


def _scale(strength: str, factor: float) -> str | None:
    pieces = parse_strength_pieces([strength])
    if not pieces or any(len(p) != 1 for p in pieces) or len(pieces) != 1:
        return None
    s = pieces[0][0]
    return f"{s.value * factor:g} {s.dim.replace('ml', 'mL')}"


def _other_form(form: str) -> str:
    f = parse_form(form)
    return "Capsule" if not f or f.family != "capsule" else "Tablet"


def generate(records: list[ShortageRecord], n_skus: int = 40, n_random: int = 10, seed: int = 7):
    rng = random.Random(seed)
    usable = [r for r in records
              if r.generic_name and r.dosage_form and r.strengths and parse_strength_pieces(r.strengths)
              and len(r.strengths) == 1 and (r.status or "").lower() != "resolved"]
    rng.shuffle(usable)
    rows, gold = [], []

    def numbers():
        demand = rng.choice([10, 20, 40, 80, 150])
        cover = rng.choice([6, 12, 20, 35, 60])
        qty = rng.choice([0, 0, 500, 1000, 3000])
        return {"on_hand": demand * cover, "daily_demand": demand, "open_order_qty": qty,
                "open_order_due_days": rng.choice([5, 12, 30]) if qty else 0,
                "unit_cost": round(rng.uniform(0.1, 20), 2), "lead_time_days": rng.choice([14, 21, 28])}

    variants = ["exact", "exact_ndc", "strength_x2", "form_swap", "strength_blank"]
    for i, rec in enumerate(usable[:n_skus]):
        v = variants[i % len(variants)]
        sku = f"SIM-{1000 + i}"
        strength, form, ndcs, label = rec.strengths[0], rec.dosage_form, [], "match"
        if v == "exact_ndc" and rec.package_ndcs:
            ndcs = rec.package_ndcs[:1]
        if v == "strength_x2":
            strength, label = _scale(rec.strengths[0], 2.0), "non_match"
        elif v == "form_swap":
            form, label = _other_form(rec.dosage_form), "non_match"
        elif v == "strength_blank":
            strength, label = "", "uncertain"
        if strength is None:
            continue
        rows.append({"sku": sku, "name": _catalogue_name(rec.generic_name), "strength": strength,
                     "dosage_form": form, "ndcs": "|".join(ndcs), "supplier": rec.company_name, **numbers()})
        gold.append({"record_key": rec.record_key, "sku": sku, "label": label, "trap_type": v,
                     "note": "auto-labelled by construction"})
    for j in range(n_random):
        rows.append({"sku": f"SIM-{2000 + j}", "name": RANDOM_NAMES[j % len(RANDOM_NAMES)], "strength": "10 mg",
                     "dosage_form": "Tablet", "ndcs": "", "supplier": "Generic Supplier", **numbers()})
    return rows, gold


def write_csvs(rows, gold, catalogue_path: Path, gold_path: Path) -> None:
    with open(catalogue_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(gold_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["record_key", "sku", "label", "trap_type", "note"])
        w.writeheader()
        w.writerows(gold)
