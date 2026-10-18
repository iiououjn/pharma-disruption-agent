"""Command line interface.

  python -m pdsia.cli demo                       # offline end-to-end run on synthetic fixtures
  python -m pdsia.cli ingest --live              # snapshot the live openFDA endpoint (needs network)
  python -m pdsia.cli simulate --from-latest     # make a simulated catalogue + gold from latest snapshot
  python -m pdsia.cli brief --out briefing.json
  python -m pdsia.cli changes
  python -m pdsia.cli eval
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import db as dbm
from . import evaluation, ingest, simulate
from .agent import run_briefing

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = os.environ.get("PDSIA_DB", "pdsia.db")
DEFAULT_CATALOGUE = ROOT / "data" / "simulated" / "catalogue.csv"


def print_brief(b: dict) -> None:
    snap = b["snapshot"]
    print(f"\nBRIEFING  snapshot #{snap['snapshot_id']} ({snap['label']}, {snap['fetched_at']})"
          f"{'  [SYNTHETIC DATA]' if snap['is_synthetic'] else ''}")
    print("Summary:", json.dumps(b["summary"]))
    for it in b["items"]:
        print(f"\n[{it['priority']}] {it['sku']}  {it['name']}")
        print("  " + it["rationale"])
        if it["missing_info"]:
            print("  Missing:", "; ".join(it["missing_info"]))
        for c in it["changes"]:
            print(f"  Change: {c['event']} {c['record_key']}")
    if b["review_queue"]:
        print("\nNEEDS REVIEW")
        for r in b["review_queue"]:
            print(f"  {r['record_key']} -> {r['sku']} (tier {r['tier']}): {'; '.join(r['why'])}")
    if b["recently_cleared"]:
        print("\nRECENTLY CLEARED")
        for c in b["recently_cleared"]:
            print(f"  {c['sku']} {c['event']} ({c['record_key']}) - {c['note']}")
    if b["unmatched_records"]:
        print("\nNO CATALOGUE MATCH")
        for u in b["unmatched_records"]:
            print(f"  {u['record_key']} {u['generic_name']}: {u['reason']}")
    print("\nScope:")
    for n in b["scope_notes"]:
        print("  -", n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pdsia")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="offline demo on synthetic fixtures (uses an in-memory DB unless --keep)")
    d.add_argument("--keep", action="store_true", help="write to --db instead of memory")
    i = sub.add_parser("ingest")
    i.add_argument("--fixture", action="append", help="fixture file name in data/fixtures")
    i.add_argument("--live", action="store_true")
    i.add_argument("--api-key", default=os.environ.get("OPENFDA_API_KEY"))
    i.add_argument("--max-records", type=int)
    c = sub.add_parser("catalogue")
    c.add_argument("--csv", default=str(DEFAULT_CATALOGUE))
    s = sub.add_parser("simulate")
    s.add_argument("--from-latest", action="store_true", required=True)
    s.add_argument("--n-skus", type=int, default=40)
    s.add_argument("--seed", type=int, default=7)
    b = sub.add_parser("brief")
    b.add_argument("--out")
    sub.add_parser("changes")
    sub.add_parser("eval")
    args = ap.parse_args(argv)

    if args.cmd == "eval":
        res = evaluation.run_all()
        print(evaluation.format_report(res))
        failed = res["metamorphic"]["failures"] or any(not a["passed"] for a in res["abstention"])
        return 1 if failed else 0

    if args.cmd == "demo":
        conn = dbm.connect(args.db if args.keep else ":memory:")
        for f in ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json"):
            ingest.load_fixture(conn, f)
        dbm.load_catalogue_csv(conn, DEFAULT_CATALOGUE)
        print_brief(run_briefing(conn))
        return 0

    conn = dbm.connect(args.db)
    if args.cmd == "ingest":
        if args.live:
            sid, created = ingest.ingest_live(conn, args.api_key, args.max_records)
            print(f"live snapshot {sid} {'stored' if created else '(unchanged, not stored)'}")
        for f in args.fixture or []:
            sid, created = ingest.load_fixture(conn, f)
            print(f"fixture {f}: snapshot {sid} {'stored' if created else '(unchanged)'}")
    elif args.cmd == "catalogue":
        print("loaded SKUs:", dbm.load_catalogue_csv(conn, args.csv))
    elif args.cmd == "simulate":
        snaps = dbm.list_snapshots(conn)
        if not snaps:
            print("no snapshots; ingest first", file=sys.stderr)
            return 1
        rows, gold = simulate.generate(dbm.get_records(conn, snaps[-1]["snapshot_id"]), args.n_skus, seed=args.seed)
        cp, gp = ROOT / "data" / "simulated" / "catalogue_generated.csv", ROOT / "data" / "gold" / "gold_generated.csv"
        simulate.write_csvs(rows, gold, cp, gp)
        dbm.load_catalogue_csv(conn, cp)
        print(f"wrote {cp.name} ({len(rows)} SKUs) and {gp.name} ({len(gold)} auto-labelled pairs); catalogue loaded")
    elif args.cmd == "brief":
        briefing = run_briefing(conn)
        print_brief(briefing)
        if args.out:
            Path(args.out).write_text(json.dumps(briefing, indent=2, default=str), encoding="utf-8")
            print("\nwrote", args.out)
    elif args.cmd == "changes":
        snaps = dbm.list_snapshots(conn)
        if len(snaps) < 2:
            print("need at least two snapshots", file=sys.stderr)
            return 1
        from .diff import diff_snapshots
        for e in diff_snapshots(dbm.get_records(conn, snaps[-2]["snapshot_id"]), dbm.get_records(conn, snaps[-1]["snapshot_id"])):
            print(json.dumps(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
