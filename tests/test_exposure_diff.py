import unittest
import helpers  # noqa: F401
from pdsia import db as dbm, evaluation as ev
from pdsia.diff import diff_snapshots
from pdsia.exposure import calculate_exposure, days_of_cover, prioritise, supplier_named, numbers_in


def sku(**kw):
    base = dict(sku="T", name="X", on_hand=252, daily_demand=21, lead_time_days=21, open_order_qty=120,
                open_order_due_days=30, unit_cost=18.5, supplier="Acme Pharma")
    base.update(kw)
    return dbm.Sku(**base)


class Exposure(unittest.TestCase):
    def test_arithmetic(self):
        e = calculate_exposure(sku(), ["Acme Pharma Ltd"])
        self.assertEqual(e["cover_days"], 12.0)
        self.assertEqual(e["shortfall_days"], 9.0)
        self.assertEqual(e["open_order_value"], 2220.0)
        self.assertEqual(e["order_value_at_risk"], 2220.0)
        self.assertFalse(e["order_arrives_before_stockout"])

    def test_no_shortfall_when_cover_exceeds_lead(self):
        self.assertEqual(calculate_exposure(sku(on_hand=1000), [])["shortfall_days"], 0.0)

    def test_missing_demand_returns_none_not_a_guess(self):
        for d in (None, 0):
            e = calculate_exposure(sku(daily_demand=d), ["Acme"])
            self.assertIsNone(e["cover_days"]); self.assertIsNone(e["shortfall_days"])
            self.assertEqual(prioritise(e, False)[0], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(days_of_cover(10, 0))

    def test_supplier_not_named_means_zero_at_risk_unknown_means_none(self):
        self.assertEqual(calculate_exposure(sku(), ["Other Co"])["order_value_at_risk"], 0.0)
        self.assertIsNone(calculate_exposure(sku(), [])["order_value_at_risk"])
        self.assertIsNone(supplier_named("", ["a"]))

    def test_calc_id_is_deterministic_and_input_sensitive(self):
        a, b = calculate_exposure(sku(), ["Acme"]), calculate_exposure(sku(), ["Acme"])
        self.assertEqual(a["calc_id"], b["calc_id"])
        self.assertNotEqual(a["calc_id"], calculate_exposure(sku(on_hand=253), ["Acme"])["calc_id"])

    def test_priority_rules(self):
        self.assertEqual(prioritise(calculate_exposure(sku(), []), False), ("HIGH", "cover_below_lead_time"))
        self.assertEqual(prioritise(calculate_exposure(sku(on_hand=525), []), False)[0], "MEDIUM")   # 25d vs 21d lead
        self.assertEqual(prioritise(calculate_exposure(sku(on_hand=2100), []), False)[0], "LOW")
        self.assertEqual(prioritise(calculate_exposure(sku(on_hand=2100), []), True)[0], "MEDIUM")

    def test_numbers_in_ignores_ids_and_dates(self):
        self.assertEqual(numbers_in("SKU-104 R-001 calc-ab12cd34ef on 2026-10-18 covers 12 days, 2,220 value Q1"), [12.0, 2220.0])


class Diff(unittest.TestCase):
    def test_injected_changes_detected_exactly(self):
        r = ev.change_detection_eval(helpers.fresh_db())
        self.assertEqual((r["precision"], r["recall"]), (1.0, 1.0), r)

    def test_whitespace_only_edit_is_not_an_event(self):
        c = helpers.fresh_db()
        snaps = dbm.list_snapshots(c)
        events = diff_snapshots(dbm.get_records(c, snaps[0]["snapshot_id"]), dbm.get_records(c, snaps[1]["snapshot_id"]))
        self.assertFalse([e for e in events if e["record_key"] == "R-011"])

    def test_resolved_is_typed_separately_from_status_change(self):
        c = helpers.fresh_db()
        s = dbm.list_snapshots(c)
        kinds = {e["record_key"]: e["event"] for e in diff_snapshots(dbm.get_records(c, s[0]["snapshot_id"]), dbm.get_records(c, s[1]["snapshot_id"]))
                 if e["event"] in ("RESOLVED", "STATUS_CHANGE")}
        self.assertEqual(kinds, {"R-012": "RESOLVED", "R-007": "STATUS_CHANGE"})

    def test_identical_snapshot_is_not_stored_twice(self):
        from pdsia import ingest
        c = helpers.fresh_db(["snapshot_2026-10-18.json"])
        sid, created = ingest.load_fixture(c, "snapshot_2026-10-18.json")
        self.assertFalse(created)
        self.assertEqual(len(dbm.list_snapshots(c)), 1)


if __name__ == "__main__":
    unittest.main()
