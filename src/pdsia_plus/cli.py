"""Additive CLI. Run `python -m pdsia_plus.cli --help`."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pdsia import db as dbm, ingest
from .alerts import deliver_alerts, prepare_alerts
from .gold import evaluate_annotated, export_review_sheet
from .live import capture_live, replay_archives
from .matching import run_hardened_briefing
from .reviews import list_audit, submit_review

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Optional improvements; existing pdsia.cli is untouched")
    ap.add_argument("--db", default=os.environ.get("PDSIA_DB", "pdsia.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    live = sub.add_parser("pull-live", help="archive and ingest a live FDA snapshot")
    live.add_argument("--archive-dir", default="data/live")
    live.add_argument("--max-records", type=int, default=None)
    rebuild = sub.add_parser("rebuild-history", help="replay archived live pulls in chronological order")
    rebuild.add_argument("--archive-dir", default="data/live")
    sub.add_parser("demo", help="run guarded briefing on the bundled synthetic fixtures")
    brief = sub.add_parser("brief", help="run guardrailed agent on existing DB")
    brief.add_argument("--out")
    sheet = sub.add_parser("export-label-sheet", help="make a BLANK worksheet for an independent reviewer")
    sheet.add_argument("--out", required=True)
    sheet.add_argument("--snapshot-id", type=int)
    sheet.add_argument("--all-pairs", action="store_true", help="include noncandidates for recall evaluation")
    evaluate = sub.add_parser("score-label-sheet", help="score labels supplied by a separate human")
    evaluate.add_argument("--csv", required=True)
    evaluate.add_argument("--split", choices=["train", "holdout", "all"], default="holdout")
    review = sub.add_parser("review", help="persist a human decision plus immutable audit record")
    review.add_argument("--record-key", required=True)
    review.add_argument("--sku", required=True)
    review.add_argument("--decision", choices=["accept", "reject"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")
    sub.add_parser("review-audit")
    alerts = sub.add_parser("alerts", help="preview (default) or send idempotent high-priority webhooks")
    alerts.add_argument("--send", action="store_true")
    alerts.add_argument("--webhook-url", default=os.environ.get("PDSIA_ALERT_WEBHOOK_URL"))
    alerts.add_argument("--minimum-priority", choices=["HIGH", "MEDIUM"], default="HIGH")
    alerts.add_argument("--include-synthetic", action="store_true")
    pg = sub.add_parser("mirror-postgres", help="optional SQLite-to-PostgreSQL mirror; requires psycopg")
    pg.add_argument("--dsn", default=os.environ.get("PDSIA_POSTGRES_DSN"))
    args = ap.parse_args(argv)

    if args.cmd == "score-label-sheet":
        print(json.dumps(evaluate_annotated(args.csv, args.split), indent=2))
        return 0
    if args.cmd == "pull-live" and args.max_records is not None and args.max_records < 1:
        ap.error("--max-records must be a positive integer")
    if args.cmd == "demo":
        conn = dbm.connect(":memory:")
        for f in ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json"):
            ingest.load_fixture(conn, f)
        dbm.load_catalogue_csv(conn, ROOT / "data/simulated/catalogue.csv")
        b = run_hardened_briefing(conn)
        print(json.dumps({"snapshot": b["snapshot"], "summary": b["summary"],
                          "matching_mode": b["matching_mode"]}, indent=2))
        return 0
    conn = dbm.connect(args.db)
    if args.cmd == "pull-live":
        result = capture_live(conn, args.archive_dir,
                              api_key=os.environ.get("OPENFDA_API_KEY"), max_records=args.max_records)
    elif args.cmd == "rebuild-history":
        result = replay_archives(conn, args.archive_dir)
    elif args.cmd == "brief":
        result = run_hardened_briefing(conn)
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif args.cmd == "export-label-sheet":
        result = {"rows": export_review_sheet(conn, args.out, args.snapshot_id, all_pairs=args.all_pairs), "file": args.out,
                  "warning": "blank annotations; this is not a gold set yet"}
    elif args.cmd == "review":
        result = submit_review(conn, record_key=args.record_key, sku=args.sku,
                               decision=args.decision, reviewer=args.reviewer, note=args.note)
    elif args.cmd == "review-audit":
        result = list_audit(conn)
    elif args.cmd == "alerts":
        if args.send:
            if not args.webhook_url:
                ap.error("--webhook-url required for --send")
            result = deliver_alerts(conn, args.webhook_url,
                                    minimum_priority=args.minimum_priority,
                                    include_synthetic=args.include_synthetic)
        else:
            result = prepare_alerts(conn, minimum_priority=args.minimum_priority,
                                    include_synthetic=args.include_synthetic)
    elif args.cmd == "mirror-postgres":
        if not args.dsn:
            ap.error("--dsn or PDSIA_POSTGRES_DSN is required")
        from .postgres import mirror
        result = mirror(conn, args.dsn)
    else:
        ap.error(f"unknown command {args.cmd}")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
