"""agent/persistence/postgres_repository.py

PostgreSQL persistence backend using psycopg 3 and explicit SQL (no ORM,
no Alembic).

Provides queryable analysis history plus the transactional human-review
workflow. The automated result, evidence, and `ReviewAudit` are stored as
IMMUTABLE historical records; reviewer decisions only mutate the review
record (with optimistic concurrency) and append to an immutable event
log. Nothing here ever overwrites the original compliance result.

The backend is selected explicitly via ``CFR_PERSISTENCE_BACKEND=postgres``
(never a silent fallback from ``file``). A missing/invalid DSN fails at
construction; an unreachable database surfaces as a clear error, never a
silent switch to files.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from agent.models import EvidencePassage, ReviewAudit
from agent.reporting import ClauseReport, ReportRecord

from .base import (
    AnalysisNotFoundError,
    ConcurrencyError,
    ReviewNotFoundError,
    ReviewStateError,
)
from .models import AnalysisSummary, MetricsSummary, ReviewDetail, ReviewEvent, ReviewItem
from .review_lifecycle import is_valid_state, validate_transition

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PostgresRepository:
    """PostgreSQL backend implementing report + review persistence."""

    backend = "postgres"

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError(
                "CFR_DATABASE_URL is required when CFR_PERSISTENCE_BACKEND=postgres"
            )
        self.dsn = dsn

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def ping(self) -> None:
        """Verify the database is reachable (fail fast at startup)."""
        with psycopg.connect(self.dsn) as conn:
            conn.execute("SELECT 1")

    # ------------------------------------------------------------------
    # Analysis persistence
    # ------------------------------------------------------------------

    def save_report(
        self,
        record: ReportRecord,
        *,
        reports_dir: str | Path | None = None,
    ) -> str:
        analysis_id = record.analysis_id
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analyses
                      (analysis_id, contract_id, generated_at, total_clauses,
                       compliant, non_compliant, needs_review)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (analysis_id) DO UPDATE SET
                        contract_id = EXCLUDED.contract_id,
                        generated_at = EXCLUDED.generated_at,
                        total_clauses = EXCLUDED.total_clauses,
                        compliant = EXCLUDED.compliant,
                        non_compliant = EXCLUDED.non_compliant,
                        needs_review = EXCLUDED.needs_review
                    """,
                    (
                        analysis_id,
                        record.contract_id,
                        record.generated_at,
                        record.total_clauses,
                        record.compliant,
                        record.non_compliant,
                        record.needs_review,
                    ),
                )
                # Replace the immutable clause snapshots for this analysis.
                # review_decisions / review_decision_events are keyed by
                # (analysis_id, clause_id), so reviewer progress survives a
                # re-save and is never deleted here.
                cur.execute(
                    "DELETE FROM evidence WHERE clause_analysis_id IN "
                    "(SELECT id FROM clause_analyses WHERE analysis_id = %s)",
                    (analysis_id,),
                )
                cur.execute(
                    "DELETE FROM review_audits WHERE clause_analysis_id IN "
                    "(SELECT id FROM clause_analyses WHERE analysis_id = %s)",
                    (analysis_id,),
                )
                cur.execute(
                    "DELETE FROM clause_analyses WHERE analysis_id = %s",
                    (analysis_id,),
                )

                for cr in record.clauses:
                    cur.execute(
                        """
                        INSERT INTO clause_analyses
                          (analysis_id, clause_id, clause_title, status, confidence,
                           reason, verification_status, review_reason, memory_participated)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            analysis_id,
                            cr.clause_id,
                            cr.clause_title,
                            cr.status,
                            cr.confidence,
                            cr.reason,
                            cr.verification_status,
                            cr.review_reason,
                            cr.memory_participated,
                        ),
                    )
                    ca_id = cur.fetchone()[0]
                    for ev in cr.evidence:
                        cur.execute(
                            """
                            INSERT INTO evidence
                              (clause_analysis_id, title, part, section, date,
                               text_span, citation, source, retrieved_at,
                               retrieval_method, version, confidence)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                ca_id,
                                ev.title,
                                ev.part,
                                ev.section,
                                ev.date,
                                ev.text_span,
                                ev.citation,
                                ev.source,
                                ev.retrieved_at,
                                ev.retrieval_method,
                                ev.version,
                                ev.confidence,
                            ),
                        )
                    if cr.review_audit is not None:
                        audit = cr.review_audit
                        cur.execute(
                            """
                            INSERT INTO review_audits
                              (clause_analysis_id, final_status, proposed_status,
                               proposed_confidence, review_reason,
                               verifier_recommendation, verifier_notes,
                               deterministic_status, evidence_citations, reviewed_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                ca_id,
                                audit.final_status,
                                audit.proposed_status,
                                audit.proposed_confidence,
                                audit.review_reason,
                                audit.verifier_recommendation,
                                audit.verifier_notes,
                                audit.deterministic_status,
                                Json(audit.evidence_citations or []),
                                audit.reviewed_at,
                            ),
                        )
                    # Initialize a review record for review-required clauses,
                    # preserving existing progress (ON CONFLICT DO NOTHING).
                    if cr.verification_status == "needs_review" or cr.status == "Needs Review":
                        cur.execute(
                            """
                            INSERT INTO review_decisions
                              (analysis_id, clause_id, state, version,
                               original_status, decided_at)
                            VALUES (%s, %s, %s, 1, %s, '')
                            ON CONFLICT (analysis_id, clause_id) DO NOTHING
                            """,
                            (analysis_id, cr.clause_id, "needs_review", cr.status),
                        )
        return analysis_id

    def get_analysis(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        return self._load_report(analysis_id)

    def load_report(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        return self._load_report(analysis_id)

    def _load_report(self, analysis_id: str) -> ReportRecord:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT contract_id, generated_at, total_clauses, compliant, "
                    "non_compliant, needs_review FROM analyses WHERE analysis_id=%s",
                    (analysis_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise AnalysisNotFoundError(f"Analysis {analysis_id!r} not found")
                contract_id, generated_at, total, compliant, non_compliant, needs_review = row

                cur.execute(
                    "SELECT id, clause_id, clause_title, status, confidence, reason, "
                    "verification_status, review_reason, memory_participated "
                    "FROM clause_analyses WHERE analysis_id=%s ORDER BY id",
                    (analysis_id,),
                )
                clauses: list[ClauseReport] = []
                for (
                    ca_id,
                    clause_id,
                    clause_title,
                    status,
                    confidence,
                    reason,
                    verification_status,
                    review_reason,
                    memory_participated,
                ) in cur.fetchall():
                    cur.execute(
                        "SELECT title, part, section, date, text_span, citation, source, "
                        "retrieved_at, retrieval_method, version, confidence "
                        "FROM evidence WHERE clause_analysis_id=%s ORDER BY id",
                        (ca_id,),
                    )
                    evidence = [
                        EvidencePassage(
                            title=t,
                            part=p,
                            section=s,
                            date=d,
                            text_span=ts,
                            citation=c,
                            source=src,
                            retrieved_at=ra,
                            retrieval_method=rm,
                            version=v,
                            confidence=conf,
                        )
                        for (t, p, s, d, ts, c, src, ra, rm, v, conf) in cur.fetchall()
                    ]
                    cur.execute(
                        "SELECT final_status, proposed_status, proposed_confidence, "
                        "review_reason, verifier_recommendation, verifier_notes, "
                        "deterministic_status, evidence_citations, reviewed_at "
                        "FROM review_audits WHERE clause_analysis_id=%s",
                        (ca_id,),
                    )
                    audit_row = cur.fetchone()
                    audit: ReviewAudit | None = None
                    if audit_row is not None:
                        (
                            final_status,
                            proposed_status,
                            proposed_confidence,
                            review_reason,
                            verifier_recommendation,
                            verifier_notes,
                            deterministic_status,
                            evidence_citations,
                            reviewed_at,
                        ) = audit_row
                        audit = ReviewAudit(
                            final_status=final_status or "",
                            proposed_status=proposed_status or "",
                            proposed_confidence=proposed_confidence or 0.0,
                            review_reason=review_reason or "",
                            verifier_recommendation=verifier_recommendation or "",
                            verifier_notes=verifier_notes or "",
                            deterministic_status=deterministic_status or "",
                            evidence_citations=evidence_citations or [],
                            reviewed_at=reviewed_at or "",
                        )
                    clauses.append(
                        ClauseReport(
                            clause_id=clause_id,
                            clause_title=clause_title,
                            status=status,
                            confidence=confidence,
                            reason=reason,
                            evidence=evidence,
                            verification_status=verification_status,
                            review_reason=review_reason,
                            review_audit=audit,
                            memory_participated=bool(memory_participated),
                        )
                    )
                return ReportRecord(
                    analysis_id=analysis_id,
                    contract_id=contract_id,
                    generated_at=generated_at or "",
                    total_clauses=total,
                    compliant=compliant,
                    non_compliant=non_compliant,
                    needs_review=needs_review,
                    clauses=clauses,
                )

    def list_analyses(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        reports_dir: str | Path | None = None,
    ) -> list[AnalysisSummary]:
        where, params = _analysis_where(status)
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT analysis_id, generated_at, contract_id, total_clauses, "
                    f"compliant, non_compliant, needs_review FROM analyses {where} "
                    f"ORDER BY generated_at DESC, analysis_id DESC LIMIT %s OFFSET %s",
                    (*params, limit, offset),
                )
                return [
                    AnalysisSummary(
                        analysis_id=r[0],
                        generated_at=r[1] or "",
                        contract_id=r[2],
                        total_clauses=r[3],
                        compliant=r[4],
                        non_compliant=r[5],
                        needs_review=r[6],
                    )
                    for r in cur.fetchall()
                ]

    def count_analyses(
        self,
        *,
        status: str | None = None,
        reports_dir: str | Path | None = None,
    ) -> int:
        where, params = _analysis_where(status)
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM analyses {where}", params).fetchone()
            return int(row[0])

    def summarize(
        self,
        *,
        reports_dir: str | Path | None = None,
    ) -> MetricsSummary:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), COALESCE(SUM(total_clauses),0), "
                    "COALESCE(SUM(compliant),0), COALESCE(SUM(non_compliant),0), "
                    "COALESCE(SUM(needs_review),0) FROM analyses"
                )
                row = cur.fetchone()
                cur.execute(
                    "SELECT COUNT(*) FROM review_decisions WHERE state='needs_review'"
                )
                pending = cur.fetchone()[0]
        total_analyses, total_clauses, compliant, non_compliant, needs_review = row
        return MetricsSummary(
            total_analyses=int(total_analyses),
            total_clauses=int(total_clauses),
            compliant=int(compliant),
            non_compliant=int(non_compliant),
            needs_review=int(needs_review),
            review_pending=int(pending),
        )

    # ------------------------------------------------------------------
    # Human review
    # ------------------------------------------------------------------

    def list_review_queue(
        self,
        *,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        where = ""
        params: list[object] = []
        if state and is_valid_state(state):
            where = "WHERE rd.state = %s"
            params.append(state)
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT rd.analysis_id, rd.clause_id, ca.clause_title,
                           rd.original_status, ca.verification_status,
                           rd.state, rd.version, rd.reviewer_identity, rd.decided_at
                    FROM review_decisions rd
                    LEFT JOIN clause_analyses ca
                      ON ca.analysis_id = rd.analysis_id AND ca.clause_id = rd.clause_id
                    {where}
                    ORDER BY rd.decided_at DESC NULLS LAST, rd.analysis_id, rd.clause_id
                    LIMIT %s OFFSET %s
                    """,
                    (*params, limit, offset),
                )
                items = [
                    ReviewItem(
                        analysis_id=r[0],
                        clause_id=r[1],
                        clause_title=r[2] or "",
                        original_status=r[3] or "",
                        verification_status=r[4] or "",
                        review_state=r[5],
                        version=r[6],
                        reviewer_identity=r[7] or "",
                        decided_at=r[8] or "",
                    )
                    for r in cur.fetchall()
                ]
        return items

    def count_review_queue(self, *, state: str | None = None) -> int:
        where = ""
        params: list[object] = []
        if state and is_valid_state(state):
            where = "WHERE rd.state = %s"
            params.append(state)
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM review_decisions rd {where}", params
            ).fetchone()
            return int(row[0])

    def get_review(self, analysis_id: str, clause_id: str) -> ReviewDetail:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                return _load_review(cur, analysis_id, clause_id)

    def list_review_events(self, analysis_id: str, clause_id: str) -> list[ReviewEvent]:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM review_decisions WHERE analysis_id=%s AND clause_id=%s",
                    (analysis_id, clause_id),
                )
                rd_id = cur.fetchone()
                if rd_id is None:
                    return []
                cur.execute(
                    "SELECT state, reason, reviewer_identity, created_at "
                    "FROM review_decision_events WHERE review_decision_id=%s "
                    "ORDER BY id",
                    (rd_id[0],),
                )
                return [
                    ReviewEvent(
                        state=r[0],
                        reason=r[1] or "",
                        reviewer_identity=r[2] or "",
                        created_at=r[3] or "",
                    )
                    for r in cur.fetchall()
                ]

    def transition_review(
        self,
        analysis_id: str,
        clause_id: str,
        *,
        target_state: str,
        reason: str,
        reviewer_identity: str,
        expected_version: int,
    ) -> ReviewDetail:
        if not is_valid_state(target_state):
            raise ReviewStateError(f"Unknown review state {target_state!r}")

        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                # Clause must exist.
                cur.execute(
                    "SELECT status, verification_status FROM clause_analyses "
                    "WHERE analysis_id=%s AND clause_id=%s",
                    (analysis_id, clause_id),
                )
                clause_row = cur.fetchone()
                if clause_row is None:
                    raise AnalysisNotFoundError(
                        f"Clause {clause_id!r} not found in analysis {analysis_id!r}"
                    )
                clause_status, verification_status = clause_row

                # Existing review record (version tracked).
                cur.execute(
                    "SELECT id, state, version FROM review_decisions "
                    "WHERE analysis_id=%s AND clause_id=%s FOR UPDATE",
                    (analysis_id, clause_id),
                )
                rd = cur.fetchone()
                if rd is None:
                    # Validly initialize from a NEEDS_REVIEW clause.
                    if verification_status != "needs_review" and clause_status != "Needs Review":
                        raise ReviewNotFoundError(
                            f"No review record for clause {clause_id!r}; the clause is not "
                            "in a review-required state."
                        )
                    current_state = "needs_review"
                    current_version = 0
                    rd_id = None
                else:
                    rd_id, current_state, current_version = rd

                if not validate_transition(current_state, target_state):
                    raise ReviewStateError(
                        f"Invalid review transition {current_state!r} -> {target_state!r}"
                    )
                if expected_version != current_version:
                    raise ConcurrencyError(
                        f"Review version conflict: expected {expected_version}, "
                        f"current {current_version}. Refresh and retry."
                    )

                new_version = current_version + 1
                decided_at = _now_iso()
                if rd_id is None:
                    cur.execute(
                        """
                        INSERT INTO review_decisions
                          (analysis_id, clause_id, state, version, original_status,
                           reviewer_identity, decision_reason, decided_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            analysis_id,
                            clause_id,
                            target_state,
                            new_version,
                            clause_status,
                            reviewer_identity,
                            reason,
                            decided_at,
                        ),
                    )
                    rd_id = cur.fetchone()[0]
                else:
                    cur.execute(
                        """
                        UPDATE review_decisions
                        SET state=%s, version=%s, reviewer_identity=%s,
                            decision_reason=%s, decided_at=%s
                        WHERE id=%s
                        """,
                        (target_state, new_version, reviewer_identity, reason, decided_at, rd_id),
                    )
                cur.execute(
                    """
                    INSERT INTO review_decision_events
                      (review_decision_id, state, reason, reviewer_identity, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (rd_id, target_state, reason, reviewer_identity, decided_at),
                )

                return _load_review(cur, analysis_id, clause_id)


def _analysis_where(status: str | None) -> tuple[str, list[object]]:
    col = {
        "compliant": "compliant",
        "non_compliant": "non_compliant",
        "needs_review": "needs_review",
    }.get(status or "")
    if not col:
        return "", []
    return f"WHERE {col} > 0", []


def _load_review(cur, analysis_id: str, clause_id: str) -> ReviewDetail:
    """Build a ReviewDetail from the current clause snapshot + review record."""
    cur.execute(
        "SELECT id, clause_title, status, confidence, reason, verification_status, "
        "review_reason, memory_participated FROM clause_analyses "
        "WHERE analysis_id=%s AND clause_id=%s",
        (analysis_id, clause_id),
    )
    clause_row = cur.fetchone()
    if clause_row is None:
        raise AnalysisNotFoundError(f"Clause {clause_id!r} not found in analysis {analysis_id!r}")
    (
        ca_id,
        clause_title,
        status,
        confidence,
        reason,
        verification_status,
        _review_reason,
        memory_participated,
    ) = clause_row

    cur.execute(
        "SELECT title, part, section, date, text_span, citation, source, "
        "retrieved_at, retrieval_method, version, confidence "
        "FROM evidence WHERE clause_analysis_id=%s ORDER BY id",
        (ca_id,),
    )
    evidence = [
        EvidencePassage(
            title=t,
            part=p,
            section=s,
            date=d,
            text_span=ts,
            citation=c,
            source=src,
            retrieved_at=ra,
            retrieval_method=rm,
            version=v,
            confidence=conf,
        )
        for (t, p, s, d, ts, c, src, ra, rm, v, conf) in cur.fetchall()
    ]

    audit: ReviewAudit | None = None
    cur.execute(
        "SELECT final_status, proposed_status, proposed_confidence, review_reason, "
        "verifier_recommendation, verifier_notes, deterministic_status, "
        "evidence_citations, reviewed_at FROM review_audits WHERE clause_analysis_id=%s",
        (ca_id,),
    )
    audit_row = cur.fetchone()
    if audit_row is not None:
        (
            final_status,
            proposed_status,
            proposed_confidence,
            review_reason,
            verifier_recommendation,
            verifier_notes,
            deterministic_status,
            evidence_citations,
            reviewed_at,
        ) = audit_row
        audit = ReviewAudit(
            final_status=final_status or "",
            proposed_status=proposed_status or "",
            proposed_confidence=proposed_confidence or 0.0,
            review_reason=review_reason or "",
            verifier_recommendation=verifier_recommendation or "",
            verifier_notes=verifier_notes or "",
            deterministic_status=deterministic_status or "",
            evidence_citations=evidence_citations or [],
            reviewed_at=reviewed_at or "",
        )

    cur.execute(
        "SELECT state, version, reviewer_identity, decision_reason, decided_at "
        "FROM review_decisions WHERE analysis_id=%s AND clause_id=%s",
        (analysis_id, clause_id),
    )
    rd = cur.fetchone()

    review_state: str | None = None
    version = 0
    reviewer_identity = ""
    decision_reason = ""
    decided_at = ""
    events: list[ReviewEvent] = []
    if rd is not None:
        (
            review_state,
            version,
            reviewer_identity,
            decision_reason,
            decided_at,
        ) = rd
        # Re-fetch the review decision id for the event query.
        cur.execute(
            "SELECT e.state, e.reason, e.reviewer_identity, e.created_at "
            "FROM review_decision_events e JOIN review_decisions d "
            "ON e.review_decision_id = d.id "
            "WHERE d.analysis_id=%s AND d.clause_id=%s ORDER BY e.id",
            (analysis_id, clause_id),
        )
        events = [
            ReviewEvent(
                state=r[0],
                reason=r[1] or "",
                reviewer_identity=r[2] or "",
                created_at=r[3] or "",
            )
            for r in cur.fetchall()
        ]

    return ReviewDetail(
        analysis_id=analysis_id,
        clause_id=clause_id,
        clause_title=clause_title,
        original_status=status,
        verification_status=verification_status,
        reason=reason,
        confidence=confidence,
        memory_participated=bool(memory_participated),
        evidence=evidence,
        review_audit=audit,
        review_state=review_state,
        version=version,
        reviewer_identity=reviewer_identity,
        decision_reason=decision_reason,
        decided_at=decided_at,
        events=events,
    )
