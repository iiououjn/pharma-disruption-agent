# Pharmaceutical Supply Disruption Intelligence Agent

Answers: **"Which products in our portfolio need attention today, what changed, and what evidence supports that priority?"**

* **Public evidence:** FDA-reported drug shortages (openFDA). **US data only. It says nothing about UAE availability.**
* **Simulated assumptions:** the distributor catalogue, inventory, demand, open orders and lead times. They are invented and labelled as such everywhere (table prefix `sim_`, `source: simulated_catalogue` on every evidence/exposure block).
* **Scope:** monitoring and investigation of *current* reported shortages. It does **not** forecast.

## Quick start (no network, no dependencies)

```bash
export PYTHONPATH=src            # or: pip install -e .
python -m pdsia.cli demo         # end-to-end briefing on bundled SYNTHETIC fixtures
python -m pdsia.cli eval         # evaluation report (also saved in docs/EVAL_REPORT.md)
python -m unittest discover -s tests -v     # 57 tests (pytest also works)
```

Live data (needs network + a check of field names, see "Not verified"):

```bash
python -m pdsia.cli --db pdsia.db ingest --live            # run daily to build history
python -m pdsia.cli --db pdsia.db simulate --from-latest   # simulated catalogue + auto-labelled gold from real records
python -m pdsia.cli --db pdsia.db brief --out briefing.json
```

Optional layers: `pip install -r requirements.txt`, then `uvicorn pdsia.api:app` and `streamlit run app/streamlit_app.py`.

## What's in the box

| Requirement | Where | Status |
|---|---|---|
| Dated snapshots + change detection (`NEW_LISTING`, `STATUS_CHANGE`, `RESOLVED`, `REMOVED`, `AVAILABILITY_TEXT_CHANGED`, `REASON_CHANGED`; cosmetic whitespace/case edits ignored) | `ingest.py`, `diff.py` | tested |
| Tiered matching: identifier -> ingredient+strength+form -> review -> reject; salts, synonyms, % / mEq / units conversion, combination products, ER vs IR | `normalize.py`, `matcher.py` | tested |
| Deterministic exposure maths with `calc_id`, `None` instead of guesses | `exposure.py` | tested |
| Tool-using agent: `get_shortage_records`, `find_catalogue_matches`, `calculate_exposure`, `compare_snapshots`, `submit_briefing` (validating) | `tools.py`, `agent.py` | tested |
| Priority decided by code, abstain states `needs_review` / `insufficient_evidence` / `no_match` | `agent.py`, `exposure.py` | tested |
| Briefing: items, evidence (public vs simulated separated), match confidence, rationale, missing info, near-misses *not* flagged | `agent.py` | tested |
| Human review queue (decisions override matcher) | `tools.py`, `api.py`, `app/` | logic tested; API/UI not run |
| Evaluation: P/R vs keyword baseline, mutation tests, numeric faithfulness, abstention, change-detection | `evaluation.py` | run |
| Catalogue + gold generator for real pulls | `simulate.py` | run on fixtures |
| FastAPI, Streamlit, LangGraph wiring, PostgreSQL DDL | `api.py`, `app/`, `langgraph_agent.py`, `sql/` | **written, NOT run** |

## Results (offline synthetic fixtures; see `docs/EVAL_REPORT.md`)

| system | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| tiered matcher (auto-accepted) | 12 | 0 | 0 | 1.00 | 1.00 |
| keyword baseline (shared ingredient word) | 11 | 19 | 1 | 0.37 | 0.92 |

Also: 47/47 metamorphic checks, 0 of 28 numbers in rationales unbacked, change detection P=R=1.0 on 10 injected events, 7/7 abstention checks.

**Read these numbers honestly.** The 1.00 matcher score is a *regression harness result*, not evidence the matcher generalises. I wrote the matcher, the fixtures and the 31 gold labels together, knowing the traps. What the numbers do show is the contrast with the baseline on identical hand-labelled traps, and that the system is checked mechanically. A real estimate needs independent labels on live data (use `simulate`, then hand-review the flagged "unlisted auto-accepts"). The generator already surfaced one case where the gold was incomplete (the same product listed by two companies).

## Design notes

* **Near-match trap:** same ingredient but different strength / dosage form / release profile / salt / combination status -> Tier 4, rejected, reason stored, shown under "Not flagged (and why)".
* **Missing information is a result, not an error:** a record with no strength becomes `needs_review`; no demand data becomes `insufficient_evidence` with no priority; no resolution date is stated as such, never estimated.
* **Numbers can't be invented:** `submit_briefing` rejects a briefing if any exposure block lacks a matching `calculate_exposure` call, differs from tool output, or any number in a rationale isn't in that item's tool output. An optional LLM `narrator` hook is validated the same way and falls back to the template (tested with a lying narrator).
* **Priority rule** (`exposure.prioritise`): cover < lead time -> HIGH; cover < 1.5x lead time -> MEDIUM; listed *To Be Discontinued* -> at least MEDIUM; otherwise LOW; missing demand/lead time -> INSUFFICIENT_EVIDENCE. The 1.5 buffer is an arbitrary demo threshold.
* **Order exposure** = open order qty x unit cost, counted "at risk" only if the order's supplier is named in a matched shortage record (0 if not named, `None` if unknown).

## Verification status: what was and wasn't actually run

Built in a sandbox with **no network and no FastAPI / Streamlit / LangGraph / pytest installed**.

* **Run and passing:** everything in the standard-library core (normaliser, matcher, diff, exposure, tools, agent, CLI, evaluation, simulate) via 57 `unittest` tests. I also broke the code on purpose in three places (form-conflict check, priority buffer, percent conversion) and confirmed the suite fails each time. The harness caught one genuine matcher bug during the build (a record with no form words in its name was treated as an unknown "other" form and wrongly rejected instead of sent to review) and one unfair baseline.
* **Not verified:**
  * `ingest.fetch_openfda` and the **openFDA field names** (`generic_name`, `company_name`, `dosage_form`, `strength`, `package_ndc`, `status`, `availability`, `shortage_reason`, `related_info`, `resolved_note`, `update_date`, ...) are written from memory of the docs. Check them against the openFDA drug-shortages searchable-fields page and fix `ingest.normalise_raw` if needed. The fixtures are **invented**, shaped like openFDA results; they are not real FDA data.
  * `api.py`, `app/streamlit_app.py`, `langgraph_agent.py` and `sql/postgres_schema.sql` have never been executed.

## What's left to do

1. **Run against live openFDA**: fix field mapping; schedule a daily pull (cron / Airflow) so real history accumulates; then `simulate` and review results.
2. **Real-data matcher hardening** (will need work): records with several strengths per entry, ratio strengths (`1:100,000` currently routes to review), powder vs solution injectables, brand names (`proprietary_name` unused), NDC formats, free-text strength notes, more synonyms and salt rules. Expect the review rate to be much higher than on fixtures.
3. **Independent gold set** on live data, labelled by someone other than the matcher's author (a pharmacist, ideally), with confidence intervals; hold-out split.
4. **LLM-driven agent (optional):** today the "agent" is a deterministic tool-calling pipeline (same tools, same guards, no model deciding anything). To have an LLM plan its own tool calls or write the prose, plug a model into `narrator` or replace the planner; the existing `ToolBox` and `submit_briefing` guards still apply. Not implemented or tested.
5. **Run and test** the API, Streamlit UI and LangGraph graph (add `fastapi.testclient` tests); port `db.py` to PostgreSQL (psycopg) using `sql/postgres_schema.sql`.
6. **Resolution dates:** the "possible timeline" check is a crude regex on availability text; it only flags that something may be there.
7. **Productionisation:** auth, audit log for review decisions, stable record keys for live data (hash keys change if identity fields change), alert delivery, `EXPECTED_EVENTS` in `evaluation.py` is tied to the bundled fixtures.
8. **Real catalogue/inventory integration** (ERP/WMS) replacing the simulated CSV, and a UAE-relevant data source if the question is UAE availability.
