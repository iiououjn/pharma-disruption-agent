# PDSIA — GitHub-ready open-source extension

**Status:** The existing files from the supplied archive are retained byte-for-byte. All additions live in `src/pdsia_plus/`, `web/`, `tests_ext/`, `.github/`, and new standalone configuration/docs files. This is a monitoring and investigation tool for **US FDA shortage reports** paired with a **SIMULATED distributor catalogue**. It is **not a clinical decision-support tool**, and it does not establish availability anywhere outside the US, including the UAE.

## 1. Run locally in five minutes

Prerequisites: Python 3.10+ and pip. In the repository root:

```bash
python -m pip install -e .
python -m pip install -r requirements-extension.txt
python -m unittest discover -s tests -v
python -m unittest discover -s tests_ext -v
python -m pdsia_plus.cli demo
```

Start a persistent demo DB:

```bash
python -m pdsia.cli --db pdsia.db demo --keep
```

Then start the *guarded* server (choose your own long random token; never commit it):

```bash
# Linux/macOS
export PDSIA_DB=pdsia.db
export PDSIA_API_TOKEN='change-this-to-a-long-random-secret'
uvicorn pdsia_plus.api:app --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
$env:PDSIA_DB = "pdsia.db"
$env:PDSIA_API_TOKEN = "change-this-to-a-long-random-secret"
python -m uvicorn pdsia_plus.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/dashboard** and enter that token to see the working web dashboard: priority queue, changes, manual review decisions, snapshot history graph. [FastAPI API docs](http://127.0.0.1:8000/docs) are at `/docs`. No external JS libraries or paid API services are required for the browser UI.

Screenshot of the browser dashboard using **mocked responses generated from the bundled synthetic fixtures**: [Preview](docs/dashboard-preview.png). This is a visual smoke check; external HTTP navigation from the sandbox browser was blocked.

The original Streamlit UI, original `pdsia.cli`, original deterministic agent, matching logic and regression fixtures remain available exactly as delivered.

## 2. Daily live FDA pull + replayable history

The original ingestion field names and API endpoint were cross-checked with the official openFDA pages: https://open.fda.gov/apis/drug/drugshortages/searchable-fields/ and https://open.fda.gov/apis/drug/drugshortages/how-to-use-the-endpoint/. They support the fields used by the existing ingestion module. Live HTTP access may require a network connection and an optional openFDA API key.

```bash
# Captures an unchanged raw FDA response in data/live/YYYY-MM-DD/<time>-<hash>.json
python -m pdsia_plus.cli --db pdsia.db pull-live --archive-dir data/live

# Rebuild a new local database from all committed public snapshots
python -m pdsia_plus.cli --db rebuilt.db rebuild-history --archive-dir data/live

# For a real briefing you still need an appropriate catalogue; the bundled one is SIMULATED
python -m pdsia.cli --db rebuilt.db catalogue --csv data/simulated/catalogue.csv
python -m pdsia_plus.cli --db rebuilt.db brief --out briefing.json
```

- The pull retries transient HTTP/network failures, refuses empty or incompatible payloads, sorts records deterministically, and checks archive SHA-256 hashes on replay.
- It deduplicates unchanged consecutive database snapshots, so a daily archive may exist without creating a new database snapshot.
- When a distinct package NDC plus company identifies one source record within a pull, it derives an NDC-based stable ID; otherwise it falls back to the original ID logic. **This is best effort, not a globally guaranteed stable FDA key.**
- `--max-records N` is for troubleshooting only. Such snapshots are clearly marked **partial**. Do not use a partial pull to infer that records disappeared.
- No claims about live FDA fetch success are made until run in a networked environment; this sandbox could not reach `api.fda.gov`.

### GitHub Actions schedule

`.github/workflows/daily-openfda.yml` runs around **07:25 UTC every day**, plus manual `workflow_dispatch`, on the repository's `main` branch. It archives raw **public** FDA responses to `data/live/` and commits them back to the repository. The SQLite DB itself is ignored, because ephemeral GitHub runners cannot persist their local DB files between jobs. The workflow relies on repository Actions write permissions and unprotected bot commits to `main`; if branch rules prevent direct commits, change the workflow to open a PR instead.

This daily workflow is installed in the files but **not enabled or executed on GitHub** until the repository is uploaded and GitHub Actions permits it. An optional `OPENFDA_API_KEY` can be added as a repository Actions secret. The original code is still compatible with an interactive live pull.

## 3. Matcher guardrails

The enhanced agent runs **the original agent nodes with an overriding ToolBox** and a conservative safety filter. It never turns an original candidate into an automatically accepted match. In ambiguous cases, it changes an auto-accept to a manual review when the source reports multiple distinct strengths, has ambiguous ratio notation such as `1:100,000`, describes powder vs premixed injectable solution, or is brand-only. Brand-only NDC/name candidates are added **only as review candidates**. An explicit human accept remains respected. The original deterministic matcher is untouched.

```bash
python -m pdsia_plus.cli --db pdsia.db brief --out briefing.json
```

## 4. Independent annotation and evaluation

```bash
# Candidate-focused worksheet. The gold_label and annotator columns are BLANK.
python -m pdsia_plus.cli --db pdsia.db export-label-sheet --out manual-review.csv

# For population-wide recall analysis, include EVERY record x SKU pair (can be large):
python -m pdsia_plus.cli --db pdsia.db export-label-sheet --all-pairs --out all-pairs.csv

# After an INDEPENDENT reviewer has filled gold_label=(match|non_match|uncertain)
# and annotator fields:
python -m pdsia_plus.cli score-label-sheet --csv all-pairs.csv --split holdout
```

The worksheet cannot label itself. The reviewer should ideally be a pharmacist and should not be the matcher author. A deterministic 20% record-based holdout is used for basic evaluation separation. Confidence intervals use the Wilson method. Annotations are not cryptographically signed; the tool cannot verify independence or correctness. The candidate-only sheet **cannot estimate whole-population recall** because missed candidates are absent.

## 5. Review decisions and audit trail

The extension API requires `Authorization: Bearer <PDSIA_API_TOKEN>` for all data endpoints; it will return HTTP **503** unless a token is configured. `/health`, `/docs`, `/openapi.json`, and the static dashboard frontend are public, but data requests are authenticated.

You can record a review via the UI, POST `/reviews`, or:

```bash
python -m pdsia_plus.cli --db pdsia.db review \
  --record-key 'REPLACE_WITH_REAL_RECORD_KEY' --sku 'REPLACE_WITH_REAL_SKU' \
  --decision accept --reviewer independent-pharmacist \
  --note 'Documented formulation and NDC verified'
python -m pdsia_plus.cli --db pdsia.db review-audit
```

Each review writes the current decision to the original `review_decisions` table and appends the previous/new values, reviewer, note, and timestamp to `ops_review_audit`. The API ignores any caller-supplied reviewer identity and uses the configured `PDSIA_REVIEWER_ID` instead. This is a basic demo auth/audit design, **not a replacement for SSO, RBAC, a tamper-proof compliance log, or a regulated quality system**.

## 6. Optional notifications, LLM prose and PostgreSQL

- **Alerts:** `python -m pdsia_plus.cli --db pdsia.db alerts` previews priority-high notifications but excludes synthetic FDA fixture snapshots by default. Add `--send --webhook-url https://your-endpoint` only when ready. Successful sends are recorded so the same situation isn't notified twice. Failed sends are not marked delivered. Catalogue exposure remains simulated.
- **Narrator:** `pdsia_plus.llm.safe_narrator` is an optional HTTPS/OpenAI-compatible *prose* hook for `run_hardened_briefing(..., narrator=safe_narrator)`, configured with `PDSIA_LLM_API_KEY` and `PDSIA_LLM_MODEL`. The deterministic agent still validates numbers and falls back to templates. It is **not a model-directed tool planner**.
- **PostgreSQL mirror:** `pip install 'psycopg[binary]>=3.2'` then `python -m pdsia_plus.cli --db pdsia.db mirror-postgres --dsn 'postgresql://user:password@host:5432/pdsia'` mirrors the original relational data into the preexisting `pub`, `sim`, and `ops` PostgreSQL schemas. The PostgreSQL mirror also exports the review audit table if present. It is **not a native PostgreSQL replacement** for the SQLite-backed agent. It could not be integration-tested here without a running PostgreSQL instance.

## 7. Docker

```bash
# Put PDSIA_API_TOKEN in your environment or in a local, UNTRACKED .env file.
export PDSIA_API_TOKEN='replace-with-a-long-random-secret'
export PDSIA_ALLOW_DEMO_WRITES=1

docker compose up --build -d
# POST /snapshots/demo with Authorization: Bearer ... OR populate via CLI
# Then open http://127.0.0.1:8000/dashboard
```

The container runs as a non-root user and stores its SQLite DB and FDA archives in the Docker volume `pdsia-data`. Port 8000 is bound to the host loopback address by default. TLS, backups, reverse proxies, stronger authentication, operational alerts and monitoring are still required before public production deployment.

## 8. Publish the repository to GitHub

A real GitHub URL requires a GitHub account with permission to publish. This package is **ready to upload**, but it has **not been pushed to GitHub** from the sandbox. From the unpacked project folder:

```bash
# First create an EMPTY public repository on GitHub called pharma-disruption-agent.
git init
git branch -M main
git add .
git commit -m "Open-source PDSIA with optional guarded extensions"
git remote add origin https://github.com/YOUR_USERNAME/pharma-disruption-agent.git
git push -u origin main
```

Do not commit real `.env` secrets, personal inventory, private supplier data or tokens. Verify the GitHub Actions configuration, repository branch protection settings and Actions workflow permissions before enabling the daily archive job. This project is MIT-licensed for code; verify public source-data and third-party license requirements independently.

## 9. Verification matrix

| Component | Current verification |
|---|---|
| Original core and fixtures | Original 57 `unittest` tests run and passing in the build environment |
| Extensions: guarded matching, live-archive/replay mocks, audit, manual labels | New tests run against synthetic/offline data |
| Guarded API | HTTP TestClient integration tests run: authentication, review, audit, briefing, history, dashboard/static assets |
| Browser dashboard | Static assets served via HTTP TestClient; headless Chromium with mocked API exercised cards, tabs, graph and buttons (no JS errors); remote HTTP browser E2E blocked in sandbox |
| Actual remote openFDA pull | Not executable in sandbox due to network DNS restrictions; schedule is prepared |
| Daily GitHub Actions / remote push | Workflow authored; execution and publishing require your GitHub repository |
| PostgreSQL mirror | Implemented; requires psycopg and running Postgres; not integration-tested |
| Original Streamlit/LangGraph | Not modified; may need optional dependencies to run |
| Real catalogue integration / independent gold set | Not supplied: must be provided and verified independently |

**Important:** The project never claims to forecast shortages or make medical decisions. Priority categories are deterministic **demo business rules** and all portfolio exposure numbers use the original **simulated catalogue**.
