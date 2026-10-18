-- PostgreSQL equivalent of the SQLite schema in src/pdsia/db.py.  UNTESTED against a live Postgres.
-- Real schemas keep PUBLIC evidence (pub) apart from SIMULATED assumptions (sim).
CREATE SCHEMA IF NOT EXISTS pub;
CREATE SCHEMA IF NOT EXISTS sim;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS pub.snapshots (
    snapshot_id   BIGSERIAL PRIMARY KEY,
    fetched_at    TIMESTAMPTZ NOT NULL,
    source        TEXT NOT NULL,
    is_synthetic  BOOLEAN NOT NULL DEFAULT FALSE,
    label         TEXT,
    content_hash  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pub.shortage_records (
    snapshot_id   BIGINT NOT NULL REFERENCES pub.snapshots(snapshot_id),
    record_key    TEXT NOT NULL,
    generic_name  TEXT, proprietary_name TEXT, company_name TEXT, presentation TEXT, dosage_form TEXT,
    strengths     JSONB, package_ndcs JSONB,
    status TEXT, availability TEXT, shortage_reason TEXT, related_info TEXT, resolved_note TEXT,
    update_type TEXT, update_date TEXT, initial_posting_date TEXT,
    raw           JSONB,
    PRIMARY KEY (snapshot_id, record_key)
);
CREATE INDEX IF NOT EXISTS shortage_generic_idx ON pub.shortage_records (lower(generic_name));

CREATE TABLE IF NOT EXISTS sim.catalogue (
    sku TEXT PRIMARY KEY, name TEXT NOT NULL, strength TEXT, dosage_form TEXT, ndcs JSONB, supplier TEXT,
    on_hand NUMERIC, daily_demand NUMERIC, open_order_qty NUMERIC, open_order_due_days NUMERIC,
    unit_cost NUMERIC, lead_time_days NUMERIC,
    is_simulated BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_simulated)
);
CREATE TABLE IF NOT EXISTS ops.match_candidates (
    snapshot_id BIGINT NOT NULL, record_key TEXT NOT NULL, sku TEXT NOT NULL REFERENCES sim.catalogue(sku),
    tier SMALLINT NOT NULL, outcome TEXT NOT NULL CHECK (outcome IN ('auto','review','reject')),
    score NUMERIC NOT NULL, reasons JSONB,
    PRIMARY KEY (snapshot_id, record_key, sku)
);
CREATE TABLE IF NOT EXISTS ops.review_decisions (
    record_key TEXT NOT NULL, sku TEXT NOT NULL, decision TEXT NOT NULL CHECK (decision IN ('accept','reject')),
    reviewer TEXT, note TEXT, decided_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (record_key, sku)
);
