"""Independent-label worksheet and scoring; never auto-create ground truth."""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from pdsia import db as dbm
from .matching import HardenedToolBox

HEADERS = ["snapshot_id", "record_key", "generic_name", "proprietary_name", "sku", "sku_name",
           "matcher_outcome", "matcher_tier", "gold_label", "annotator", "rationale", "split"]
LABELS = {"match", "non_match", "uncertain"}


def _split(record_key: str, sku: str) -> str:
    # Split by RECORD so the same source record doesn't leak between sets.
    h = int(hashlib.sha256(record_key.encode()).hexdigest()[:8], 16)
    return "holdout" if h % 5 == 0 else "train"


def export_review_sheet(conn, path: str | Path, snapshot_id: int | None = None, *, all_pairs=False) -> int:
    snaps = dbm.list_snapshots(conn)
    if not snaps:
        raise ValueError("ingest a snapshot before exporting labels")
    sid = snapshot_id or snaps[-1]["snapshot_id"]
    records = dbm.get_records(conn, sid)
    toolbox = HardenedToolBox(conn)
    skus = {s.sku: s for s in dbm.get_catalogue(conn)}
    rows = []
    for rec in records:
        candidates = {m.sku: m for m in toolbox.find_catalogue_matches(rec.record_key, sid)}
        for sku in skus.values():
            m = candidates.get(sku.sku)
            if m is None and not all_pairs:
                continue
            rows.append({"snapshot_id": sid, "record_key": rec.record_key,
                         "generic_name": rec.generic_name, "proprietary_name": rec.proprietary_name,
                         "sku": sku.sku, "sku_name": sku.name,
                         "matcher_outcome": m.outcome if m else "no_candidate",
                         "matcher_tier": m.tier if m else "",
                         "gold_label": "", "annotator": "", "rationale": "",
                         "split": _split(rec.record_key, sku.sku)})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def wilson_interval(success: int, total: int, z: float = 1.95996398454):
    if total == 0:
        return None
    p = success / total
    d = 1 + z*z / total
    center = (p + z*z / (2*total)) / d
    half = z * math.sqrt((p*(1-p) + z*z/(4*total))/total) / d
    return [max(0., center-half), min(1., center+half)]


def evaluate_annotated(path: str | Path, split: str = "holdout") -> dict:
    if split not in ("holdout", "train", "all"):
        raise ValueError("split must be holdout, train or all")
    with Path(path).open(newline="", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    labeled = []
    rejected = 0
    for row in records:
        if split != "all" and row.get("split") != split:
            continue
        label = row.get("gold_label", "").strip().lower()
        if label not in LABELS:
            rejected += 1
            continue
        if not row.get("annotator", "").strip():
            rejected += 1
            continue
        labeled.append(row)
    # Review is an abstention, not a 'non-match' guess.
    tp = sum(x["matcher_outcome"] == "auto" and x["gold_label"] == "match" for x in labeled)
    fp = sum(x["matcher_outcome"] == "auto" and x["gold_label"] == "non_match" for x in labeled)
    fn = sum(x["matcher_outcome"] != "auto" and x["gold_label"] == "match" for x in labeled)
    abstain = sum(x["matcher_outcome"] == "review" for x in labeled)
    return {"split": split, "reviewed": len(labeled), "unlabeled_or_invalid": rejected,
            "tp": tp, "fp": fp, "fn": fn, "abstained": abstain,
            "precision": tp/(tp+fp) if tp+fp else None,
            "precision_95pct_wilson": wilson_interval(tp, tp+fp),
            "recall_with_abstention_as_miss": tp/(tp+fn) if tp+fn else None,
            "recall_95pct_wilson": wilson_interval(tp, tp+fn),
            "note": "Recall is population-wide ONLY for an exhaustive all-pairs annotated sheet; "
                    "candidate-only exports omit missed candidates. Independent annotator identity "
                    "is entered by a human, not independently verified by software."}
