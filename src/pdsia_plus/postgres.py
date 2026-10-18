"""Optional SQLite -> PostgreSQL durable mirror (not a native PostgreSQL driver for the core).

The original engine continues to use SQLite; this adapter syncs its public,
simulated and review data into separate PostgreSQL schemas. An explicit mirror,
never a pretend native PostgreSQL port. Requires psycopg 3 and a running server.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def mirror(conn, dsn: str) -> dict:
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as e:
        raise RuntimeError("Install optional PostgreSQL dependency: pip install 'psycopg[binary]>=3.2'") from e

    tables = {"snapshots": "pub.snapshots", "pub_shortage_records": "pub.shortage_records",
              "sim_catalogue": "sim.catalogue", "match_candidates": "ops.match_candidates",
              "review_decisions": "ops.review_decisions"}
    counts = {key: 0 for key in tables}
    counts["ops_review_audit"] = 0
    with psycopg.connect(dsn) as pg:
        with pg.transaction():
            pg.execute((ROOT / "sql" / "postgres_schema.sql").read_text(encoding="utf-8"))
            pg.execute((ROOT / "sql" / "extension_postgres.sql").read_text(encoding="utf-8"))
            for row in conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id"):
                pg.execute("""INSERT INTO pub.snapshots(snapshot_id,fetched_at,source,is_synthetic,label,content_hash)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id) DO UPDATE SET
                    fetched_at=excluded.fetched_at,source=excluded.source,is_synthetic=excluded.is_synthetic,
                    label=excluded.label,content_hash=excluded.content_hash""",
                    (row["snapshot_id"],row["fetched_at"],row["source"],bool(row["is_synthetic"]),
                     row["label"],row["content_hash"]))
                counts["snapshots"] += 1
            for row in conn.execute("SELECT * FROM pub_shortage_records"):
                pg.execute("""INSERT INTO pub.shortage_records(snapshot_id,record_key,generic_name,proprietary_name,
                    company_name,presentation,dosage_form,strengths,package_ndcs,status,availability,
                    shortage_reason,related_info,resolved_note,update_type,update_date,initial_posting_date,raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(snapshot_id,record_key) DO UPDATE SET
                    status=excluded.status,availability=excluded.availability,shortage_reason=excluded.shortage_reason,
                    related_info=excluded.related_info,resolved_note=excluded.resolved_note,raw=excluded.raw""",
                    (row["snapshot_id"],row["record_key"],row["generic_name"],row["proprietary_name"],
                     row["company_name"],row["presentation"],row["dosage_form"],
                     Jsonb(json.loads(row["strengths_json"] or "[]")),Jsonb(json.loads(row["package_ndcs_json"] or "[]")),
                     row["status"],row["availability"],row["shortage_reason"],row["related_info"],
                     row["resolved_note"],row["update_type"],row["update_date"],row["initial_posting_date"],
                     Jsonb(json.loads(row["raw_json"] or "{}"))))
                counts["pub_shortage_records"] += 1
            for row in conn.execute("SELECT * FROM sim_catalogue"):
                pg.execute("""INSERT INTO sim.catalogue(sku,name,strength,dosage_form,ndcs,supplier,on_hand,
                    daily_demand,open_order_qty,open_order_due_days,unit_cost,lead_time_days,is_simulated)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(sku) DO UPDATE SET name=excluded.name,strength=excluded.strength,
                    dosage_form=excluded.dosage_form,ndcs=excluded.ndcs,supplier=excluded.supplier,
                    on_hand=excluded.on_hand,daily_demand=excluded.daily_demand,
                    open_order_qty=excluded.open_order_qty,open_order_due_days=excluded.open_order_due_days,
                    unit_cost=excluded.unit_cost,lead_time_days=excluded.lead_time_days""",
                    (row["sku"],row["name"],row["strength"],row["dosage_form"],
                     Jsonb(json.loads(row["ndcs_json"] or "[]")),row["supplier"],row["on_hand"],
                     row["daily_demand"],row["open_order_qty"],row["open_order_due_days"],
                     row["unit_cost"],row["lead_time_days"],True))
                counts["sim_catalogue"] += 1
            for row in conn.execute("SELECT * FROM match_candidates"):
                pg.execute("""INSERT INTO ops.match_candidates(snapshot_id,record_key,sku,tier,outcome,score,reasons)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id,record_key,sku) DO UPDATE SET
                    tier=excluded.tier,outcome=excluded.outcome,score=excluded.score,reasons=excluded.reasons""",
                    (row["snapshot_id"],row["record_key"],row["sku"],row["tier"],row["outcome"],
                     row["score"],Jsonb(json.loads(row["reasons_json"] or "{}"))))
                counts["match_candidates"] += 1
            for row in conn.execute("SELECT * FROM review_decisions"):
                pg.execute("""INSERT INTO ops.review_decisions(record_key,sku,decision,reviewer,note,decided_at)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(record_key,sku) DO UPDATE SET
                    decision=excluded.decision,reviewer=excluded.reviewer,note=excluded.note,
                    decided_at=excluded.decided_at""",
                    (row["record_key"],row["sku"],row["decision"],row["reviewer"],row["note"],
                     row["decided_at"]))
                counts["review_decisions"] += 1
            has_audit = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_review_audit'").fetchone()
            if has_audit:
                for row in conn.execute("SELECT * FROM ops_review_audit"):
                    pg.execute("""INSERT INTO ops.review_audit(audit_id,record_key,sku,previous_decision,
                        new_decision,reviewer,note,decided_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(audit_id) DO NOTHING""",
                        (row["audit_id"],row["record_key"],row["sku"],row["previous_decision"],
                         row["new_decision"],row["reviewer"],row["note"],row["decided_at"]))
                    counts["ops_review_audit"] += 1
            pg.execute("""SELECT setval(pg_get_serial_sequence('pub.snapshots','snapshot_id'),
                        GREATEST((SELECT COALESCE(MAX(snapshot_id),1) FROM pub.snapshots),1))""")
    return counts
