import unittest
import helpers  # noqa: F401
from pdsia import db as dbm, evaluation as ev
from pdsia.agent import run_briefing
from pdsia.tools import ToolBox, numbers_ok


class Briefing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = helpers.fresh_db()
        cls.b = run_briefing(cls.conn)
        cls.items = {i["sku"]: i for i in cls.b["items"]}

    def test_illustrative_example_from_the_brief(self):
        i = self.items["SKU-104"]
        self.assertEqual(i["priority"], "HIGH")
        self.assertIn("its identifier matches a current shortage record", i["rationale"])
        self.assertIn("covers 12 days against a 21-day replenishment assumption", i["rationale"])
        self.assertIn("The source gives no confirmed resolution date.", i["rationale"])

    def test_expected_flagged_set(self):
        self.assertEqual(set(self.items), {"SKU-101", "SKU-104", "SKU-105", "SKU-107", "SKU-109", "SKU-110",
                                           "SKU-116", "SKU-117", "SKU-119"})

    def test_near_miss_products_not_flagged(self):
        for s in ("SKU-102", "SKU-103", "SKU-106", "SKU-108", "SKU-112", "SKU-113", "SKU-114", "SKU-121", "SKU-115"):
            self.assertNotIn(s, self.items)

    def test_duplicate_listings_merge_into_one_item(self):
        self.assertEqual(len(self.items["SKU-101"]["matches"]), 2)

    def test_priorities(self):
        self.assertEqual({s: i["priority"] for s, i in self.items.items()},
                         {"SKU-101": "HIGH", "SKU-104": "HIGH", "SKU-116": "HIGH", "SKU-105": "MEDIUM",
                          "SKU-109": "MEDIUM", "SKU-119": "MEDIUM", "SKU-107": "LOW", "SKU-110": "LOW", "SKU-117": "LOW"})

    def test_sorted_high_first(self):
        order = [i["priority"] for i in self.b["items"]]
        self.assertEqual(order, sorted(order, key=lambda p: {"HIGH": 0, "MEDIUM": 1, "LOW": 3}[p]))

    def test_changes_attached_to_items(self):
        kinds = {c["event"] for c in self.items["SKU-109"]["changes"]}
        self.assertIn("STATUS_CHANGE", kinds)
        self.assertEqual({c["sku"] for c in self.b["recently_cleared"]}, {"SKU-118", "SKU-120"})

    def test_review_and_unmatched_states(self):
        self.assertEqual([(r["record_key"], r["sku"]) for r in self.b["review_queue"]], [("R-009", "SKU-111")])
        self.assertIn("R-014", {u["record_key"] for u in self.b["unmatched_records"]})

    def test_public_and_simulated_evidence_are_labelled_separately(self):
        sources = {e["source"] for e in self.items["SKU-104"]["evidence"]}
        self.assertEqual(sources, {"public_fda_shortage", "simulated_catalogue"})
        self.assertEqual(self.items["SKU-104"]["exposure"]["source"], "simulated_catalogue")

    def test_resolution_hint_branch(self):
        self.assertIn("may mention a timeline", self.items["SKU-105"]["rationale"])
        self.assertNotIn("no confirmed resolution date in source", self.items["SKU-105"]["missing_info"])

    def test_numeric_faithfulness(self):
        r = ev.numeric_faithfulness(self.conn, self.b)
        self.assertGreater(r["numbers_checked"], 20)
        self.assertEqual((r["unbacked_numbers"], r["exposure_recompute_mismatches"]), (0, 0))

    def test_trace_records_tool_calls(self):
        tools = {t["tool"] for t in self.b["trace"]}
        self.assertTrue({"get_shortage_records", "find_catalogue_matches", "calculate_exposure",
                         "compare_snapshots", "submit_briefing"} <= tools)

    def test_scope_notes_present(self):
        self.assertTrue(any("UAE" in n for n in self.b["scope_notes"]))


class Guards(unittest.TestCase):
    def test_submit_rejects_exposure_not_backed_by_a_tool_call(self):
        conn = helpers.fresh_db()
        b = run_briefing(conn)
        tb = ToolBox(conn)
        # fresh toolbox has no calc registry -> exposures are "not backed by a tool call"
        with self.assertRaises(ValueError):
            tb.submit_briefing(b)

    def _run_to_compose(self, conn):
        from pdsia import agent
        state = {"tb": ToolBox(conn), "snapshot_id": None, "prev_snapshot_id": None, "narrator": None}
        for name, fn in agent.NODES[:-1]:           # everything except compose
            state.update(fn(state))
        return state, agent

    def test_submit_rejects_invented_number_in_rationale(self):
        state, agent = self._run_to_compose(helpers.fresh_db())
        state["items"][0]["rationale"] += " Stock will last 99 days."
        with self.assertRaises(ValueError) as cm:
            agent.node_compose(state)
        self.assertIn("unbacked numbers", str(cm.exception))

    def test_submit_rejects_altered_exposure_block(self):
        state, agent = self._run_to_compose(helpers.fresh_db())
        state["items"][0]["exposure"] = {**state["items"][0]["exposure"], "cover_days": 99.0}
        with self.assertRaises(ValueError) as cm:
            agent.node_compose(state)
        self.assertIn("differs from tool output", str(cm.exception))

    def test_submit_rejects_illegal_priority(self):
        state, agent = self._run_to_compose(helpers.fresh_db())
        state["items"][0]["priority"] = "CRITICAL"
        with self.assertRaises(ValueError):
            agent.node_compose(state)

    def test_numbers_ok_flags_invented_figures(self):
        it = {"matches": [{"tier": 1}], "exposure": {"cover_days": 12.0, "lead_time_days": 21.0}}
        self.assertTrue(numbers_ok("covers 12 days against 21 days", it)[0])
        ok, bad = numbers_ok("covers 14 days against 21 days", it)
        self.assertFalse(ok); self.assertEqual(bad, [14.0])

    def test_llm_narrator_with_invented_number_is_rejected_and_falls_back(self):
        conn = helpers.fresh_db()
        liar = lambda item: f"Review {item['sku']}: stock lasts 99 days."
        b = run_briefing(conn, narrator=liar)
        i = next(x for x in b["items"] if x["sku"] == "SKU-104")
        self.assertFalse(i["narrator_used"])
        self.assertIn("covers 12 days", i["rationale"])

    def test_llm_narrator_with_faithful_text_is_accepted(self):
        conn = helpers.fresh_db()
        honest = lambda item: f"{item['sku']} has {item['exposure']['cover_days']:g} days of simulated cover."
        b = run_briefing(conn, narrator=honest)
        i = next(x for x in b["items"] if x["sku"] == "SKU-104")
        self.assertTrue(i["narrator_used"])

    def test_human_accept_promotes_review_item_to_prioritised(self):
        conn = helpers.fresh_db()
        conn.execute("INSERT INTO review_decisions VALUES ('R-009','SKU-111','accept','tester','',NULL)")
        conn.commit()
        b = run_briefing(conn)
        self.assertIn("SKU-111", {i["sku"] for i in b["items"]})
        self.assertEqual(b["review_queue"], [])

    def test_abstention_suite(self):
        failed = [a for a in ev.abstention_checks() if not a["passed"]]
        self.assertEqual(failed, [])

    def test_runs_with_a_single_snapshot(self):
        b = run_briefing(helpers.fresh_db(["snapshot_2026-10-18.json"]))
        self.assertIsNone(b["previous_snapshot"])
        self.assertEqual(b["changes"], [])


if __name__ == "__main__":
    unittest.main()
