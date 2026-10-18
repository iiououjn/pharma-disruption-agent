"""Streamlit front-end.  NOT RUN in the build sandbox (Streamlit not installed there).

    pip install -r requirements.txt && pip install -e .
    streamlit run app/streamlit_app.py
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from pdsia import db as dbm
from pdsia import ingest
from pdsia.agent import run_briefing

ROOT = Path(__file__).resolve().parents[1]
st.set_page_config(page_title="Pharma Supply Disruption Agent", layout="wide")
st.title("Pharmaceutical Supply Disruption Briefing")
st.caption("US FDA shortage records + a SIMULATED catalogue. This says nothing about UAE availability.")

db_path = st.sidebar.text_input("SQLite DB path", os.environ.get("PDSIA_DB", "pdsia.db"))
conn = dbm.connect(db_path)

with st.sidebar:
    if st.button("Load synthetic demo data"):
        for f in ("snapshot_2026-10-04.json", "snapshot_2026-10-18.json"):
            ingest.load_fixture(conn, f)
        dbm.load_catalogue_csv(conn, ROOT / "data" / "simulated" / "catalogue.csv")
        st.success("Loaded 2 synthetic snapshots + simulated catalogue")
    if st.button("Pull live openFDA snapshot"):
        try:
            sid, created = ingest.ingest_live(conn, os.environ.get("OPENFDA_API_KEY"))
            st.success(f"snapshot {sid} {'stored' if created else 'unchanged'}")
        except Exception as exc:
            st.error(f"fetch failed: {exc}")

snaps = dbm.list_snapshots(conn)
if not snaps or not dbm.get_catalogue(conn):
    st.info("No data yet. Use the sidebar to load the synthetic demo data.")
    st.stop()

b = run_briefing(conn)
snap = b["snapshot"]
if snap["is_synthetic"]:
    st.warning("These snapshots are SYNTHETIC (invented, openFDA-shaped). Not real FDA data.")
cols = st.columns(5)
for col, (label, key) in zip(cols, [("High", "high"), ("Medium", "medium"), ("Low", "low"),
                                    ("Needs review", "needs_review"), ("Changes", "changes")]):
    col.metric(label, b["summary"][key])

t1, t2, t3, t4, t5 = st.tabs(["Priorities", "What changed", "Review queue", "Not flagged (and why)", "Scope"])
with t1:
    for it in b["items"]:
        with st.expander(f"[{it['priority']}] {it['sku']} - {it['name']}", expanded=it["priority"] == "HIGH"):
            st.write(it["rationale"])
            if it["missing_info"]:
                st.markdown("**Missing / uncertain:** " + "; ".join(it["missing_info"]))
            st.markdown("**Public FDA evidence**")
            st.dataframe(pd.DataFrame([e for e in it["evidence"] if e["source"] == "public_fda_shortage"]))
            st.markdown("**Simulated assumptions and calculation** (`" + it["exposure"]["calc_id"] + "`)")
            st.json(it["exposure"])
            st.markdown("**Match evidence**")
            st.dataframe(pd.DataFrame([{k: m[k] for k in ("record_key", "tier", "confidence", "score", "company", "status")}
                                       | {"reasons": "; ".join(m["reasons"])} for m in it["matches"]]))
    if b["recently_cleared"]:
        st.subheader("Recently cleared")
        st.dataframe(pd.DataFrame(b["recently_cleared"]))
with t2:
    st.dataframe(pd.DataFrame(b["changes"]))
with t3:
    if not b["review_queue"]:
        st.write("Nothing awaiting review.")
    for r in b["review_queue"]:
        st.markdown(f"**{r['record_key']} -> {r['sku']}** (tier {r['tier']}, {r['record']['generic_name']}) - " + "; ".join(r["why"]))
        c1, c2, _ = st.columns([1, 1, 6])
        for col, decision in ((c1, "accept"), (c2, "reject")):
            if col.button(decision.title(), key=f"{decision}-{r['record_key']}-{r['sku']}"):
                conn.execute("INSERT OR REPLACE INTO review_decisions VALUES (?,?,?,?,?,?)",
                             (r["record_key"], r["sku"], decision, "streamlit-user", "",
                              datetime.now(timezone.utc).isoformat(timespec="seconds")))
                conn.commit()
                st.rerun()
with t4:
    st.write("Near-matches rejected on attribute conflicts - these are the products the system deliberately did NOT flag:")
    st.dataframe(pd.DataFrame([{**n, "why_not_flagged": "; ".join(n["why_not_flagged"])} for n in b["near_matches_not_flagged"]]))
    st.write("Records with no usable catalogue match:")
    st.dataframe(pd.DataFrame(b["unmatched_records"]))
with t5:
    for n in b["scope_notes"]:
        st.write("- " + n)
    with st.expander("Agent tool-call trace"):
        st.json(b["trace"])
