-- 0001_initial_schema.sql
-- Initial schema for analysis history and the human review workflow.
--
-- The automated compliance result, evidence, and ReviewAudit are stored
-- as IMMUTABLE historical records. Human review is an additional layer:
-- review_decisions hold the mutable lifecycle state (with an optimistic-
-- concurrency version) and review_decision_events is an append-only audit
-- log. Reviewer decisions NEVER overwrite the original result.
--
-- Idempotent: safe to run repeatedly (CREATE ... IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id    TEXT PRIMARY KEY,
    contract_id    TEXT,
    generated_at   TEXT NOT NULL DEFAULT '',
    total_clauses  INTEGER NOT NULL DEFAULT 0,
    compliant      INTEGER NOT NULL DEFAULT 0,
    non_compliant  INTEGER NOT NULL DEFAULT 0,
    needs_review   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clause_analyses (
    id                  BIGSERIAL PRIMARY KEY,
    analysis_id         TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    clause_id           TEXT NOT NULL,
    clause_title        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    confidence          DOUBLE PRECISION NOT NULL DEFAULT 0,
    reason              TEXT NOT NULL DEFAULT '',
    verification_status TEXT NOT NULL DEFAULT 'not_verified',
    review_reason       TEXT NOT NULL DEFAULT '',
    memory_participated BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (analysis_id, clause_id)
);
CREATE INDEX IF NOT EXISTS idx_clause_analyses_analysis ON clause_analyses(analysis_id);

CREATE TABLE IF NOT EXISTS evidence (
    id                 BIGSERIAL PRIMARY KEY,
    clause_analysis_id BIGINT NOT NULL REFERENCES clause_analyses(id) ON DELETE CASCADE,
    title              INTEGER NOT NULL DEFAULT 0,
    part               TEXT,
    section            TEXT,
    date               TEXT,
    text_span          TEXT NOT NULL DEFAULT '',
    citation           TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    retrieved_at       TEXT NOT NULL DEFAULT '',
    retrieval_method   TEXT NOT NULL DEFAULT '',
    version            TEXT NOT NULL DEFAULT '',
    confidence         DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_evidence_clause ON evidence(clause_analysis_id);

CREATE TABLE IF NOT EXISTS review_audits (
    id                      BIGSERIAL PRIMARY KEY,
    clause_analysis_id      BIGINT NOT NULL UNIQUE
                            REFERENCES clause_analyses(id) ON DELETE CASCADE,
    final_status            TEXT NOT NULL DEFAULT '',
    proposed_status         TEXT NOT NULL DEFAULT '',
    proposed_confidence     DOUBLE PRECISION NOT NULL DEFAULT 0,
    review_reason           TEXT NOT NULL DEFAULT '',
    verifier_recommendation TEXT NOT NULL DEFAULT '',
    verifier_notes          TEXT NOT NULL DEFAULT '',
    deterministic_status    TEXT NOT NULL DEFAULT '',
    evidence_citations      JSONB NOT NULL DEFAULT '[]',
    reviewed_at             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_review_audits_clause ON review_audits(clause_analysis_id);

CREATE TABLE IF NOT EXISTS review_decisions (
    id                BIGSERIAL PRIMARY KEY,
    analysis_id       TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    clause_id         TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN
                      ('needs_review','under_review','approved','rejected','escalated')),
    version           INTEGER NOT NULL DEFAULT 1,
    original_status   TEXT NOT NULL DEFAULT '',
    reviewer_identity TEXT NOT NULL DEFAULT '',
    decision_reason   TEXT NOT NULL DEFAULT '',
    decided_at        TEXT NOT NULL DEFAULT '',
    UNIQUE (analysis_id, clause_id)
);
CREATE INDEX IF NOT EXISTS idx_review_decisions_state ON review_decisions(state);

CREATE TABLE IF NOT EXISTS review_decision_events (
    id                 BIGSERIAL PRIMARY KEY,
    review_decision_id BIGINT NOT NULL REFERENCES review_decisions(id) ON DELETE CASCADE,
    state              TEXT NOT NULL,
    reason             TEXT NOT NULL DEFAULT '',
    reviewer_identity  TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_review_events_decision ON review_decision_events(review_decision_id);
