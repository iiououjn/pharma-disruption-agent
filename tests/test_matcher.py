import unittest
import helpers  # noqa: F401
from pdsia import db as dbm, evaluation as ev
from pdsia.matcher import match, AUTO, REVIEW, REJECT


class GoldSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = helpers.fresh_db()
        cls.res = ev.evaluate_matching(cls.conn)
        cls.recs = ev.all_records(cls.conn)
        cls.cat = {s.sku: s for s in dbm.get_catalogue(cls.conn)}

    def test_gold_keys_exist(self):
        self.assertEqual(self.res["gold_keys_not_in_data"], [])

    def test_precision_and_recall(self):
        m = self.res["pairs"]["matcher"]
        self.assertEqual(m["fp"], 0)
        self.assertEqual(m["fn"], 0)

    def test_beats_keyword_baseline_on_precision(self):
        self.assertGreater(self.res["pairs"]["matcher"]["precision"], self.res["pairs"]["keyword_baseline"]["precision"] + 0.3)

    def test_baseline_misses_synonym(self):
        self.assertGreaterEqual(self.res["pairs"]["keyword_baseline"]["fn"], 1)

    def test_no_unlabelled_auto_accepts(self):
        self.assertEqual(self.res["unlisted_matcher_auto_accepts"], [])

    def test_uncertain_pairs_go_to_review(self):
        rq = self.res["review_queue"]
        self.assertEqual(rq["uncertain_routed_to_review"], rq["uncertain_total"])

    def test_each_gold_label_individually(self):
        for (key, sku), label in ev.load_gold().items():
            m = match(self.recs[key], self.cat[sku])
            outcome = m.outcome if m else None
            if label == "match":
                self.assertEqual(outcome, AUTO, (key, sku))
            elif label == "uncertain":
                self.assertEqual(outcome, REVIEW, (key, sku))
            else:
                self.assertNotEqual(outcome, AUTO, (key, sku))

    def test_specific_traps(self):
        r, c = self.recs, self.cat
        self.assertEqual(match(r["R-003"], c["SKU-102"]).outcome, REJECT)        # ER vs IR
        self.assertEqual(match(r["R-005"], c["SKU-106"]).outcome, REJECT)        # 2% vs 1%
        self.assertEqual(match(r["R-017"], c["SKU-108"]), None)                  # KCl vs NaCl: no ingredient overlap
        self.assertEqual(match(r["R-001"], c["SKU-104"]).tier, 1)                # identifier
        self.assertEqual(match(r["R-011"], c["SKU-119"]).tier, 2)                # synonym + percent conversion

    def test_ndc_match_with_conflict_goes_to_review_not_auto(self):
        import copy
        rec = copy.deepcopy(self.recs["R-001"]); rec.strengths = ["2 mg/mL"]
        m = match(rec, self.cat["SKU-104"])
        self.assertEqual((m.outcome, m.tier), (REVIEW, 3))

    def test_metamorphic(self):
        mm = ev.metamorphic_checks(self.conn)
        self.assertGreater(mm["checks"], 30)
        self.assertEqual(mm["failures"], [])

    def test_human_decision_overrides(self):
        from pdsia.matcher import match_all
        cat = list(self.cat.values())
        base = [m for m in match_all(self.recs["R-009"], cat) if m.sku == "SKU-111"][0]
        self.assertEqual(base.outcome, REVIEW)
        acc = [m for m in match_all(self.recs["R-009"], cat, {("R-009", "SKU-111"): "accept"}) if m.sku == "SKU-111"][0]
        self.assertEqual(acc.outcome, AUTO)
        self.assertEqual(acc.confidence, "high (human-confirmed)")


if __name__ == "__main__":
    unittest.main()
