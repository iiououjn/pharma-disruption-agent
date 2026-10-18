-- Additive schema: audit history and operational logs for the optional mirror.
-- Do not alter original sql/postgres_schema.sql.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.review_audit (
    audit_id BIGINT PRIMARY KEY,
    record_key TEXT NOT NULL,
    sku TEXT NOT NULL,
    previous_decision TEXT,
    new_decision TEXT NOT NULL CHECK (new_decision IN ('accept','reject')),
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS review_audit_record_idx ON ops.review_audit(record_key,sku,decided_at);
