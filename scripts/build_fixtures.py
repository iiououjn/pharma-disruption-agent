"""Writes two SYNTHETIC openFDA-shaped snapshots to data/fixtures/.

These are NOT real FDA records. Company names and NDCs (labeler 99999) are invented. They exist so the
pipeline, change detection and the near-match traps can be exercised offline and reproducibly.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "fixtures"


def rec(fid, generic, company, form, strength, status="Current", availability="", reason="",
        ndc=None, presentation="", related="", update_date="10/01/2026", resolved_note=""):
    return {
        "fixture_id": fid, "generic_name": generic, "company_name": company, "dosage_form": form,
        "strength": strength, "status": status, "availability": availability, "shortage_reason": reason,
        "package_ndc": ndc or [], "presentation": presentation, "related_info": related,
        "update_date": update_date, "resolved_note": resolved_note, "update_type": "Revised",
        "initial_posting_date": "09/01/2026",
    }


S1 = [
    rec("R-001", "Cisplatin Injection", "Meridian Oncology Supply", "Injection, Solution", ["1 mg/mL"],
        availability="Limited supply.", reason="Manufacturing delay", ndc=["99999-1040-01"]),
    rec("R-003", "Metformin Hydrochloride Tablet, Extended Release", "Northwind Generics",
        "Tablet, Extended Release", ["500 mg"], availability="Limited supply.", reason="Increased demand"),
    rec("R-004", "Amoxicillin Powder for Oral Suspension", "Harbor Pharma", "Powder, for Suspension",
        ["250 mg/5 mL"], availability="Intermittent backorders.", reason="Increased demand"),
    rec("R-005", "Lidocaine Hydrochloride Injection", "Lakeside Sterile Products", "Injection, Solution",
        ["20 mg/mL"], availability="Allocated.", reason="Increased demand"),
    rec("R-006", "Lidocaine Hydrochloride and Epinephrine Injection", "Lakeside Sterile Products",
        "Injection, Solution", ["10 mg/mL", "0.01 mg/mL"], availability="Limited supply.", reason="Raw material shortage"),
    rec("R-007", "Sodium Chloride Injection", "Lakeside Sterile Products", "Injection, Solution", ["23.4%"],
        availability="Limited supply.", reason="Manufacturing delay"),
    rec("R-008", "Methylphenidate Hydrochloride Tablet, Extended Release", "Capital Neuro Labs",
        "Tablet, Extended Release", ["36 mg"], availability="Product available; intermittent backorders.",
        reason="Increased demand"),
    rec("R-009", "Ondansetron Injection", "Cobalt Sterile Inj", "Injection, Solution", [],
        availability="Limited supply.", reason="Manufacturing delay"),
    rec("R-010", "Heparin Sodium Injection", "Cobalt Sterile Inj", "Injection, Solution", ["1,000 units/mL"],
        availability="Limited supply.", reason="Raw material shortage"),
    rec("R-011", "Albuterol Sulfate Inhalation Solution", "Eastgate Pharma", "Inhalation Solution", ["0.083%"],
        availability="Limited supply.", reason="Increased demand"),
    rec("R-012", "Cefazolin Sodium Injection", "Harbor Pharma", "Injection, Powder, for Solution", ["1 g"],
        availability="Limited supply.", reason="Manufacturing delay"),
    rec("R-015", "Bupivacaine Hydrochloride Injection", "Cobalt Sterile Inj", "Injection, Solution", ["2.5 mg/mL"],
        availability="Limited supply.", reason="Manufacturing delay"),
    rec("R-016", "Amoxicillin and Clavulanate Potassium Tablet", "Harbor Pharma", "Tablet, Film Coated",
        ["875 mg/125 mg"], availability="Limited supply.", reason="Increased demand"),
    rec("R-017", "Potassium Chloride Injection", "Lakeside Sterile Products", "Injection, Solution", ["2 mEq/mL"],
        availability="Limited supply.", reason="Manufacturing delay"),
    rec("R-021", "Metoprolol Tartrate Tablet", "Northwind Generics", "Tablet", ["50 mg"],
        availability="Limited supply.", reason="Increased demand"),
]

# Snapshot 2 = S1 with injected, known changes (the change-detection test checks these exactly).
S2 = [dict(r) for r in S1]
by_id = {r["fixture_id"]: r for r in S2}
by_id["R-001"]["availability"] = "Allocated to existing customers only."          # AVAILABILITY_TEXT_CHANGED
by_id["R-005"]["shortage_reason"] = "Manufacturing delay"                           # REASON_CHANGED
by_id["R-007"]["status"] = "To Be Discontinued"                                     # STATUS_CHANGE
by_id["R-008"]["availability"] = "Limited availability; supplier indicates recovery in Q1 2027 (unconfirmed)."  # AVAILABILITY_TEXT_CHANGED
by_id["R-012"]["status"] = "Resolved"                                               # RESOLVED
by_id["R-011"]["availability"] = "  limited   supply. "                             # whitespace/case only -> NO event
S2 = [r for r in S2 if r["fixture_id"] != "R-017"]                                  # REMOVED
S2 += [
    rec("R-002", "Carboplatin Injection", "Meridian Oncology Supply", "Injection, Solution", ["10 mg/mL"],
        availability="Limited supply.", reason="Manufacturing delay"),             # NEW_LISTING
    rec("R-013", "Epinephrine Injection", "Lakeside Sterile Products", "Injection, Solution", ["1 mg/mL"],
        availability="", reason=""),                                                # NEW_LISTING, sparse evidence
    rec("R-014", "Vancomycin Injection", "Eastgate Pharma", "Injection, Powder, for Solution", ["1 g"],
        availability="Limited supply.", reason="Increased demand"),                # NEW_LISTING, not in catalogue
    rec("R-019", "Metformin Hydrochloride Tablet, Extended Release", "Eastgate Pharma",
        "Tablet, Extended Release", ["500 mg"], availability="Limited supply.", reason="Increased demand"),  # NEW_LISTING, duplicate product
]

for name, data, fetched, label in [
    ("snapshot_2026-10-04.json", S1, "2026-10-04T08:00:00+00:00", "SYNTHETIC snapshot 1"),
    ("snapshot_2026-10-18.json", S2, "2026-10-18T08:00:00+00:00", "SYNTHETIC snapshot 2"),
]:
    payload = {"meta": {"synthetic": True, "label": label, "fetched_at": fetched,
                        "note": "Invented records shaped like openFDA drug-shortage results. Not real FDA data."},
               "results": data}
    (OUT / name).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote", name, len(data), "records")
