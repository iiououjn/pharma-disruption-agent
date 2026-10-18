"""Tests for additive extensions; all live API operations use fake, offline records."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pdsia import db as dbm, ingest
from pdsia.db import ShortageRecord, Sku
from pdsia.matcher import AUTO, MatchResult
from pdsia_plus.alerts import prepare_alerts
from pdsia_plus.gold import evaluate_annotated, export_review_sheet, wilson_interval
from pdsia_plus.live import capture_live, fetch_retried, replay_archives, store_archive_and_snapshot
from pdsia_plus.matching import harden_match, run_hardened_briefing, safety_reasons
from pdsia_plus.reviews import list_audit, submit_review

ROOT = Path(__file__).resolve().parents[1]


def fixture_db():
    conn = dbm.connect(":memory:")
    for name in ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json"):
        ingest.load_fixture(conn, name)
    dbm.load_catalogue_csv(conn, ROOT / "data/simulated/catalogue.csv")
    return conn


class MatchSafetyTests(unittest.TestCase):
    def make(self, strength, form, other_form="Injection"):
        r = ShortageRecord("FDA1", generic_name="Epinephrine", dosage_form=form, strengths=strength)
        s = Sku("SKU-1", name="Epinephrine", strength="1 mg/mL", dosage_form=other_form)
        m = MatchResult("FDA1", "SKU-1", 2, AUTO, 0.9, [])
        return r, s, m

    def test_many_strengths_abstain(self):
        r,s,m = self.make(["1 mg/mL", "2 mg/mL"], "Injection")
        self.assertEqual(harden_match(r,s,m).outcome, "review")

    def test_ratio_abstains_even_when_ndc_assumed(self):
        r,s,m = self.make(["1:100,000"], "Injection")
        m.tier = 1
        self.assertEqual(harden_match(r,s,m).outcome, "review")

    def test_powder_vs_solution_abstain(self):
        r,s,m = self.make(["1 mg/mL"], "Powder for Injection", "Solution for Injection")
        self.assertEqual(harden_match(r,s,m).outcome, "review")

    def test_human_accept_not_overridden(self):
        r,s,m = self.make(["1:100,000"], "Injection")
        m.human_decision = "accept"
        self.assertEqual(harden_match(r,s,m).outcome, "auto")

    def test_normal_strength_unchanged(self):
        r,s,m = self.make(["1 mg/mL"], "Injection")
        self.assertEqual(safety_reasons(r,s), [])
        self.assertEqual(harden_match(r,s,m).outcome, "auto")

    def test_original_fixture_guarded_pipeline(self):
        conn = fixture_db()
        b = run_hardened_briefing(conn)
        self.assertEqual(b["matching_mode"], "guarded_conservative")
        self.assertTrue(b["summary"]["flagged_skus"])
        self.assertEqual(b["snapshot"]["is_synthetic"], 1)
        self.assertTrue(all(it["matches"] for it in b["items"]))
        conn.close()

    def test_brand_only_ndc_is_review_only(self):
        conn = dbm.connect(":memory:")
        raw = [{"fixture_id":"B1", "generic_name":"", "proprietary_name":"Brand Y",
                "package_ndc":"12345-0001-01", "status":"Current"}]
        ingest.store_snapshot(conn, raw, source="fake", is_synthetic=True)
        conn.execute("""INSERT INTO sim_catalogue(sku,name,strength,dosage_form,ndcs_json)
                     VALUES ('Y1','Brand Y','1 mg','Tablet',?)""", (json.dumps(["12345-0001-01"]),))
        from pdsia_plus.matching import HardenedToolBox
        results = HardenedToolBox(conn).find_catalogue_matches("B1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "review")
        conn.close()


class LiveArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(":memory:")
        self.rows = [
            {"generic_name":"Med A", "company_name":"Maker One", "package_ndc":"00001-0002-03",
             "status":"Current", "strength":["1 mg"], "dosage_form":"Tablet"},
            {"generic_name":"Med B", "company_name":"Maker Two", "package_ndc":"00002-0002-03",
             "status":"Current", "strength":["2 mg"], "dosage_form":"Tablet"},
        ]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_archive_restore_and_dedupe(self):
        stamp = datetime(2026,10,18,10,0,tzinfo=timezone.utc)
        out = store_archive_and_snapshot(self.conn, self.rows, self.temp.name, at=stamp)
        self.assertTrue(out["created"])
        self.assertTrue(Path(out["archive"]).exists())
        second = store_archive_and_snapshot(self.conn, list(reversed(self.rows)),
                                            self.temp.name, at=stamp)
        self.assertFalse(second["created"])
        self.assertEqual(len(dbm.list_snapshots(self.conn)), 1)
        restored = dbm.connect(":memory:")
        self.assertTrue(replay_archives(restored, self.temp.name)[0]["created"])
        self.assertEqual(len(dbm.get_records(restored, 1)), 2)
        self.assertTrue(all(r.record_key.startswith("FDA-") for r in dbm.get_records(restored, 1)))
        restored.close()

    def test_checksum_tamper_rejected(self):
        out = store_archive_and_snapshot(self.conn, self.rows, self.temp.name)
        p=Path(out["archive"])
        obj=json.loads(p.read_text())
        obj["results"][0]["generic_name"]="tampered"
        p.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            replay_archives(dbm.connect(":memory:"), self.temp.name)

    def test_empty_live_pull_refused(self):
        with self.assertRaisesRegex(ValueError, "no valid records"):
            capture_live(self.conn,self.temp.name,fetcher=lambda **kw:[])

    def test_fake_capture_partial_flag(self):
        result = capture_live(self.conn,self.temp.name,fetcher=lambda **kw:self.rows,max_records=1)
        self.assertTrue(result["partial"])
        self.assertEqual(result["records"], 2)  # fake intentionally did not apply limit
        self.assertEqual(dbm.list_snapshots(self.conn)[0]["label"], "partial live pull")

    def test_retry_transient_failure(self):
        import urllib.error
        calls=[0]
        def fetcher(**kwargs):
            calls[0]+=1
            if calls[0]==1: raise urllib.error.URLError("temporary")
            return self.rows
        result = fetch_retried(fetcher=fetcher, pause_seconds=0)
        self.assertEqual(len(result),2)
        self.assertEqual(calls[0],2)

    def test_unexpected_schema_refused(self):
        with self.assertRaisesRegex(ValueError, "unexpected openFDA"):
            fetch_retried(fetcher=lambda **kw:[{"random":"value"}])


class ReviewAndEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn=fixture_db()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_audit_records_both_old_and_new(self):
        record = dbm.get_records(self.conn, 2)[0]
        sku = dbm.get_catalogue(self.conn)[0]
        submit_review(self.conn, record_key=record.record_key, sku=sku.sku, reviewer="independent",
                      decision="reject", note="not equivalent")
        submit_review(self.conn, record_key=record.record_key, sku=sku.sku, reviewer="independent",
                      decision="accept", note="pharmacist confirmed")
        audit = list_audit(self.conn)
        self.assertEqual(len(audit),2)
        self.assertEqual(audit[0]["previous_decision"], "reject")
        self.assertEqual(audit[0]["new_decision"], "accept")
        self.assertEqual(audit[1]["previous_decision"], None)

    def test_review_rejects_unknown_record(self):
        with self.assertRaisesRegex(ValueError, "unknown record_key"):
            submit_review(self.conn,record_key="bogus",sku="SKU-100",reviewer="a",decision="accept")

    def test_sheet_export_blank_and_no_gold_claim(self):
        p=Path(self.temp.name)/"gold.csv"
        n=export_review_sheet(self.conn,p)
        self.assertGreater(n,0)
        with p.open() as f: rows=list(csv.DictReader(f))
        self.assertTrue(all(not row["gold_label"] for row in rows))
        result=evaluate_annotated(p,"all")
        self.assertEqual(result["reviewed"],0)
        self.assertIsNone(result["precision"])

    def test_all_pairs_includes_non_candidates(self):
        p=Path(self.temp.name)/"all_pairs.csv"
        n=export_review_sheet(self.conn,p,all_pairs=True)
        self.assertEqual(n,len(dbm.get_records(self.conn,2))*len(dbm.get_catalogue(self.conn)))
        self.assertGreater(n,0)
        with p.open() as f: rows=list(csv.DictReader(f))
        self.assertIn("no_candidate",{x["matcher_outcome"] for x in rows})

    def test_scoring_manual_labels_and_ci(self):
        p=Path(self.temp.name)/"manual.csv"
        with p.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=["gold_label","matcher_outcome","annotator","split"])
            w.writeheader()
            w.writerows([
                {"gold_label":"match","matcher_outcome":"auto","annotator":"reviewer","split":"holdout"},
                {"gold_label":"non_match","matcher_outcome":"auto","annotator":"reviewer","split":"holdout"},
                {"gold_label":"match","matcher_outcome":"review","annotator":"reviewer","split":"holdout"},
            ])
        result=evaluate_annotated(p)
        self.assertEqual((result["tp"],result["fp"],result["fn"]),(1,1,1))
        self.assertEqual(result["precision"],0.5)
        self.assertTrue(result["precision_95pct_wilson"][0]<0.5)

    def test_no_alerts_from_synthetic_by_default(self):
        self.assertEqual(prepare_alerts(self.conn),[])


try:
    from fastapi.testclient import TestClient
    from pdsia_plus.api import app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None,"fastapi/httpx optional dependencies unavailable")
class SecureApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db_path=str(Path(self.temp.name)/"test.db")
        conn=dbm.connect(self.db_path)
        for name in ("snapshot_2026-10-04.json","snapshot_2026-10-18.json"):
            ingest.load_fixture(conn,name)
        dbm.load_catalogue_csv(conn,ROOT/"data/simulated/catalogue.csv")
        conn.close()
        self.env=patch.dict(os.environ,{"PDSIA_DB":self.db_path,"PDSIA_API_TOKEN":"test-secret"},clear=False)
        self.env.start()
        self.client=TestClient(app)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_dashboard_and_history(self):
        self.assertEqual(self.client.get("/dashboard").status_code,200)
        self.assertEqual(self.client.get("/assets/dashboard.js").status_code,200)
        points=self.client.get("/metrics/history",headers={"Authorization":"Bearer test-secret"})
        self.assertEqual(points.status_code,200)
        self.assertEqual(len(points.json()),2)
        self.assertGreater(points.json()[0]["record_count"],0)

    def test_health_public_but_briefing_restricted(self):
        self.assertEqual(self.client.get("/health").status_code,200)
        self.assertEqual(self.client.get("/briefing").status_code,401)
        b=self.client.get("/briefing",headers={"Authorization":"Bearer test-secret"})
        self.assertEqual(b.status_code,200)
        self.assertEqual(b.json()["matching_mode"],"guarded_conservative")

    def test_no_configured_token_is_fail_closed(self):
        with patch.dict(os.environ,{"PDSIA_API_TOKEN":""}):
            self.assertEqual(self.client.get("/snapshots").status_code,503)

    def test_review_auth_and_audit(self):
        data={"record_key":"R-NOT-REAL","sku":"SKU-100","decision":"accept"}
        url="/reviews"
        self.assertEqual(self.client.post(url,json=data).status_code,401)
        self.assertEqual(self.client.post(url,json=data,headers={"Authorization":"Bearer test-secret"}).status_code,422)
        conn=dbm.connect(self.db_path)
        data["record_key"]=dbm.get_records(conn,2)[0].record_key
        data["sku"]=dbm.get_catalogue(conn)[0].sku
        conn.close()
        r=self.client.post(url,json=data,headers={"Authorization":"Bearer test-secret"})
        self.assertEqual(r.status_code,200,r.text)
        aud=self.client.get("/audit/reviews",headers={"Authorization":"Bearer test-secret"})
        self.assertEqual(len(aud.json()),1)

    def test_demo_writes_disabled_by_default(self):
        with patch.dict(os.environ,{"PDSIA_ALLOW_DEMO_WRITES":"0"}):
            self.assertEqual(self.client.post("/snapshots/demo",headers={"Authorization":"Bearer test-secret"}).status_code,403)


if __name__ == '__main__':
    unittest.main()
