"""agent/memory.py

Compliance Memory: a deterministic, durable, advisory layer over the
authoritative CFR compliance pipeline.

Purpose
-------
The core pipeline is authoritative-only: current/version-aware CFR
retrieval -> security gate -> deterministic rules -> LLM evaluation ->
verification. It has no cross-run knowledge; verified, audited outcomes
are written to reports and then discarded. Compliance Memory persists
*eligible* verified outcomes as an append-only JSONL store so repeated
contract boilerplate can be recognized on later runs.

Authority hierarchy (strict, never reordered)
---------------------------------------------
1. Current authoritative eCFR / version-aware retrieval
2. Current deterministic validation
3. Current compliance evaluation + verification
4. Historical Compliance Memory as contextual precedent ONLY

Safety invariants
-----------------
- Memory NEVER establishes or replaces a CFR requirement. It is context,
  not law, and never a source of regulatory truth.
- Exact-match reuse fires only after every compatibility check passes
  (identical clause fingerprint, eligible/verified record, no unresolved
  review, compatible citation/scope, compatible effective version) AND
  current authoritative retrieval is present and usable.
- Near-duplicate records are contextual only; they never reuse a verdict.
- Feedback-loop prevention (Option A): only CFR-only evaluations are
  automatically eligible for indexing. Any evaluation that memory
  participated in (exact reuse or supplied historical context) is marked
  ``memory_assisted`` and is never auto-indexed.
- Fail-open: any memory error, malformed record, disabled store or empty
  store falls back to the authoritative-only pipeline. A memory failure
  can never produce or invalidate a compliance verdict.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .memory_store import MEMORY_FORMAT_VERSION, MemoryStore
from .models import Clause, ComplianceResult

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryRecord",
    "MemoryLookup",
    "ComplianceMemory",
    "DEFAULT_MEMORY_DIR",
    "get_compliance_memory",
]

#: Fallback directory when CFR_MEMORY_DIR is unset (mirrors reporting's
#: DEFAULT_REPORTS_DIR convention: repo-root / memory).
DEFAULT_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"

#: Maximum number of near-duplicate records surfaced as context.
NEAR_MATCH_LIMIT = 3

#: Minimum token-set overlap for a clause to count as a near duplicate.
#: Conservative but realistic for contract boilerplate, which typically
#: varies by a few words. Near matches are contextual only, so a slightly
#: low threshold only surfaces extra labeled (non-authoritative) context.
NEAR_MATCH_THRESHOLD = 0.4

#: Tokenize on anything non-alphanumeric; ignore tiny tokens.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Stopwords stripped before similarity scoring (deterministic, tiny set).
_STOPWORDS = frozenset(
        {
            "the", "a", "an", "and", "or", "of", "to", "in", "for", "on",
            "with", "shall", "must", "may", "not", "any", "all", "its",
            "their", "this", "that", "from", "by", "as", "at", "be", "is",
            "are", "will", "has", "have", "been", "being", "than", "such",
            "each", "per",
        }
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_compliance_memory() -> ComplianceMemory | None:
    """Build the configured process-wide ComplianceMemory (fail-open).

    Returns None when memory is disabled (``CFR_MEMORY_ENABLED=false``),
    preserving the authoritative-only pipeline. Any configuration failure
    also degrades to None -- memory must never break evaluation.
    """
    try:
        from cfr_compliance_mcp.config import get_settings

        settings = get_settings()
    except Exception:
        logger.exception(
            "memory_disabled failed to load settings; authoritative-only pipeline"
        )
        return None
    if not settings.cfr_memory_enabled:
        return None
    return ComplianceMemory(enabled=True, memory_dir=settings.cfr_memory_dir or None)


def _record_id(clause_id: str, citation: str, effective_version: str) -> str:
    """Stable, content-addressed fingerprint for a memory record."""
    raw = f"{clause_id}|{citation}|{effective_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _tokens(text: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return {t for t in tokens if t not in _STOPWORDS and len(t) >= 3}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRecord:
    """A stored, verified historical compliance outcome.

    Provenance is exhaustive and honest: ``memory_assisted`` records
    whether historical memory influenced the stored evaluation. Under
    Option A it is always False for stored records (memory-assisted
    results are never auto-indexed), but the field is preserved so the
    schema can represent that state unambiguously.
    """

    record_id: str
    format_version: int
    clause_id: str
    clause_title: str
    clause_text: str
    status: str
    confidence: float
    reason: str
    citation: str
    title: int
    part: str
    section: str
    effective_version: str
    date: str
    version_specific: bool
    verification_status: str
    evidence: tuple[dict[str, Any], ...]
    source: str
    retrieval_method: str
    retrieved_at: str
    indexed_at: str
    memory_assisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "format_version": self.format_version,
            "clause_id": self.clause_id,
            "clause_title": self.clause_title,
            "clause_text": self.clause_text,
            "status": self.status,
            "confidence": self.confidence,
            "reason": self.reason,
            "citation": self.citation,
            "title": self.title,
            "part": self.part,
            "section": self.section,
            "effective_version": self.effective_version,
            "date": self.date,
            "version_specific": self.version_specific,
            "verification_status": self.verification_status,
            "evidence": list(self.evidence),
            "source": self.source,
            "retrieval_method": self.retrieval_method,
            "retrieved_at": self.retrieved_at,
            "indexed_at": self.indexed_at,
            "memory_assisted": self.memory_assisted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord | None:
        """Parse a stored dict into a record, or None when malformed.

        Fail-open by construction: a malformed line must never break
        retrieval. Only structurally valid records (with the current
        ``format_version``) are accepted.
        """
        if data.get("format_version") != MEMORY_FORMAT_VERSION:
            return None
        try:
            evidence = data.get("evidence") or []
            if not isinstance(evidence, list) or not all(
                isinstance(ev, dict) for ev in evidence
            ):
                return None
            return cls(
                record_id=str(data["record_id"]),
                format_version=int(data["format_version"]),
                clause_id=str(data["clause_id"]),
                clause_title=str(data.get("clause_title", "")),
                clause_text=str(data.get("clause_text", "")),
                status=str(data["status"]),
                confidence=float(data.get("confidence", 0.0)),
                reason=str(data.get("reason", "")),
                citation=str(data.get("citation", "")),
                title=int(data.get("title", 0)),
                part=str(data.get("part", "")),
                section=str(data.get("section", "")),
                effective_version=str(data.get("effective_version", "")),
                date=str(data.get("date", "")),
                version_specific=bool(data.get("version_specific", False)),
                verification_status=str(data.get("verification_status", "")),
                evidence=tuple(evidence),
                source=str(data.get("source", "")),
                retrieval_method=str(data.get("retrieval_method", "")),
                retrieved_at=str(data.get("retrieved_at", "")),
                indexed_at=str(data.get("indexed_at", "")),
                memory_assisted=bool(data.get("memory_assisted", False)),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("memory_load_skipped unparseable record")
            return None


# ---------------------------------------------------------------------------
# Lookup result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryLookup:
    """Result of a memory retrieval for one clause.

    ``match_type`` is one of ``exact``, ``near``, ``none``. ``exact``
    carries the single eligible record whose reuse compatibility checks
    all passed; ``near`` carries deterministic near-duplicates that are
    contextual only (never a verdict reuse). ``participated`` is True
    whenever any historical content was supplied to the evaluation.
    """

    match_type: str = "none"
    exact: MemoryRecord | None = None
    near: list[MemoryRecord] = field(default_factory=list)

    @property
    def participated(self) -> bool:
        return self.match_type != "none"


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def is_eligible(result: ComplianceResult) -> bool:
    """True when a result may be automatically indexed (Option A rules).

    Index only when ALL of the following hold:
      - verification_status == "verified"
      - no unresolved review state (empty review_reason, and audit not
        flagging a pending review)
      - not security-rejected (deterministic_status != blocked_by_security)
      - evidence present and complete (citation + text_span + provenance
        on every passage)
    NEEDS_REVIEW, security-rejected, failed and malformed results are
    never eligible.
    """
    if result.verification_status != "verified":
        return False

    if not result.evidence:
        return False

    audit = result.review_audit
    if audit is not None:
        if audit.deterministic_status == "blocked_by_security":
            return False
        if audit.review_reason:
            return False

    for ev in result.evidence:
        if not ev.citation or not ev.text_span:
            return False
        if not ev.source or not ev.retrieval_method:
            return False

    return True


# ---------------------------------------------------------------------------
# ComplianceMemory manager
# ---------------------------------------------------------------------------


class ComplianceMemory:
    """Deterministic, durable, advisory memory over the pipeline.

    All public entry points are fail-open: a disabled/empty store, an
    unreadable file, or any exception results in a benign ``none`` lookup
    / skipped index and a logged observability event -- never a changed
    or invalidated compliance verdict.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        memory_dir: str | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.store: MemoryStore | None = None
            return
        self.store = MemoryStore(memory_dir or DEFAULT_MEMORY_DIR)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        clause: Clause,
        *,
        citation: str,
        title: int,
        effective_version: str,
        date: str = "",
    ) -> MemoryLookup:
        """Look up historical context for a clause.

        Exact and near matches are computed deterministically over
        stored, eligible records. Any failure degrades to an empty
        ``none`` lookup.
        """
        if not self.enabled or self.store is None:
            return MemoryLookup(match_type="none")
        try:
            records = self._load_records()
        except Exception:
            logger.exception(
                "memory_retrieval_failure store unreadable; "
                "falling back to authoritative-only"
            )
            return MemoryLookup(match_type="none")
        if not records:
            logger.info("memory_retrieval_empty no eligible records")
            return MemoryLookup(match_type="none")

        clause_tokens = _tokens(clause.text)
        exact: MemoryRecord | None = None
        near: list[MemoryRecord] = []

        for rec in records:
            if not self._compatible_scope(rec, title=title, citation=citation):
                continue
            if rec.clause_id == clause.clause_id:
                if self._exact_reuse_allowed(
                    rec,
                    clause=clause,
                    title=title,
                    citation=citation,
                    effective_version=effective_version,
                ):
                    exact = rec
                    continue
            elif clause_tokens:
                overlap = _jaccard(clause_tokens, _tokens(rec.clause_text))
                if overlap >= NEAR_MATCH_THRESHOLD:
                    near.append(rec)

        if exact is not None:
            logger.info(
                "memory_exact_match record=%s clause=%r citation=%s",
                exact.record_id,
                clause.title,
                citation,
            )
            return MemoryLookup(match_type="exact", exact=exact, near=near)

        near = near[:NEAR_MATCH_LIMIT]
        if near:
            logger.info(
                "memory_near_match clause=%r citations=%s",
                clause.title,
                [r.citation for r in near],
            )
            return MemoryLookup(match_type="near", exact=None, near=near)

        return MemoryLookup(match_type="none")

    def _load_records(self) -> list[MemoryRecord]:
        """All stored records that parse cleanly (fail-open)."""
        records: list[MemoryRecord] = []
        for data in self.store.load():
            rec = MemoryRecord.from_dict(data)
            if rec is not None:
                records.append(rec)
        return records

    @staticmethod
    def _normalize_citation(citation: str) -> str:
        return " ".join(citation.strip().lower().split())

    def _compatible_scope(
        self, rec: MemoryRecord, *, title: int, citation: str
    ) -> bool:
        """Scope compatibility: same title (or exact normalized citation)."""
        if title and rec.title and rec.title == title:
            return True
        if citation and rec.citation:
            return self._normalize_citation(citation) == self._normalize_citation(rec.citation)
        return False

    @staticmethod
    def _exact_reuse_allowed(
        rec: MemoryRecord,
        *,
        clause: Clause,
        title: int,
        citation: str,
        effective_version: str,
    ) -> bool:
        """All Critical Change 2 gates for exact-match verdict reuse.

        A verdict is reused ONLY when the historical record is an
        eligible, verified, review-free record for the SAME clause
        fingerprint AND the current authoritative retrieval is present
        (caller already confirmed usable text) AND regulatory
        citation/scope AND effective-version metadata are compatible.
        Anything else => do not reuse (the record may still surface as
        near/context via the caller).
        """
        if rec.clause_id != clause.clause_id:
            return False
        if rec.verification_status != "verified":
            return False
        if rec.memory_assisted:
            return False
        if rec.status not in ("Compliant", "Non-Compliant"):
            return False
        if not citation:
            return False
        if title and rec.title and rec.title != title:
            return False
        if rec.citation and citation:
            if (
                ComplianceMemory._normalize_citation(rec.citation)
                != ComplianceMemory._normalize_citation(citation)
            ):
                return False
        # Compatible effective version: reuse only when the historical
        # record was evaluated against the same effective version as the
        # current retrieval. When either side lacks version metadata,
        # compatibility cannot be confirmed => no reuse (conservative).
        if effective_version and rec.effective_version:
            if effective_version != rec.effective_version:
                return False
        else:
            return False
        return True

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(
        self,
        result: ComplianceResult,
        *,
        clause: Clause,
        citation: str,
        title: int,
        effective_version: str,
        date: str,
        version_specific: bool,
        source: str,
        retrieval_method: str,
        retrieved_at: str,
        memory_assisted: bool,
    ) -> str:
        """Attempt to index a verified result; never blocks or fails the request.

        Returns a decision string: "indexed", "skipped", "duplicate", or
        "error". Option A: ``memory_assisted`` results are never
        automatically indexed (feedback-loop prevention).
        """
        if not self.enabled or self.store is None:
            return "skipped"
        logger.info("memory_index_attempt clause=%r citation=%s", clause.title, citation)

        if memory_assisted:
            logger.info(
                "memory_index_skipped memory-assisted result never auto-indexed clause=%r",
                clause.title,
            )
            return "skipped"

        if not is_eligible(result):
            logger.info("memory_index_skipped ineligible result clause=%r", clause.title)
            return "skipped"

        record = self._build_record(
            result,
            clause=clause,
            citation=citation,
            title=title,
            effective_version=effective_version,
            date=date,
            version_specific=version_specific,
            source=source,
            retrieval_method=retrieval_method,
            retrieved_at=retrieved_at,
        )
        try:
            if self.store.contains(record.record_id):
                logger.info(
                    "memory_index_skipped duplicate record=%s", record.record_id
                )
                return "duplicate"
            self.store.append(record.to_dict())
        except Exception:
            logger.exception(
                "memory_index_failure record=%s; compliance result remains valid",
                record.record_id,
            )
            return "error"

        logger.info(
            "memory_index_success record=%s clause=%r citation=%s",
            record.record_id,
            clause.title,
            citation,
        )
        return "indexed"

    def _build_record(
        self,
        result: ComplianceResult,
        *,
        clause: Clause,
        citation: str,
        title: int,
        effective_version: str,
        date: str,
        version_specific: bool,
        source: str,
        retrieval_method: str,
        retrieved_at: str,
    ) -> MemoryRecord:
        evidence = [
            {
                "title": ev.title,
                "part": ev.part,
                "section": ev.section,
                "date": ev.date,
                "text_span": ev.text_span,
                "citation": ev.citation,
                "source": ev.source,
                "retrieved_at": ev.retrieved_at,
                "retrieval_method": ev.retrieval_method,
                "version": ev.version,
            }
            for ev in result.evidence
        ]
        part = result.evidence[0].part or ""
        section = result.evidence[0].section or ""
        return MemoryRecord(
            record_id=_record_id(clause.clause_id, citation, effective_version),
            format_version=MEMORY_FORMAT_VERSION,
            clause_id=clause.clause_id,
            clause_title=clause.title,
            clause_text=clause.text,
            status=result.status,
            confidence=result.confidence,
            reason=result.reason,
            citation=citation,
            title=title,
            part=part,
            section=section,
            effective_version=effective_version,
            date=date,
            version_specific=version_specific,
            verification_status=result.verification_status,
            evidence=tuple(evidence),
            source=source,
            retrieval_method=retrieval_method,
            retrieved_at=retrieved_at,
            indexed_at=_now_iso(),
            memory_assisted=False,
        )


# ---------------------------------------------------------------------------
# Historical-context formatting (HISTORICAL_CONTEXT as DATA)
# ---------------------------------------------------------------------------


def format_historical_context(
    lookup: MemoryLookup,
    *,
    clause_title: str,
    citation: str,
) -> str:
    """Render historical records as a clearly labeled, non-authoritative
    DATA block for the LLM prompt.

    The block states explicitly that historical context is not law,
    cannot establish a CFR requirement, that the current authoritative
    CFR evidence takes precedence, and that final citations must cite
    only the authoritative CFR material.
    """
    lines: list[str] = []
    lines.append("=====================")
    lines.append("HISTORICAL CONTEXT (NOT AUTHORITATIVE)")
    lines.append("=====================")
    lines.append(
        "The following are prior VERIFIED compliance outcomes for this "
        "or a similar contract clause. They are historical records only."
    )
    lines.append("- They are NOT law and cannot establish or replace any CFR requirement.")
    lines.append("- The current CFR REGULATION above is the sole authority for this decision.")
    lines.append(
        "- If historical context conflicts with the current CFR regulation, "
        "the current CFR regulation wins."
    )
    lines.append(
        "- Every evidence entry in your response MUST cite ONLY the current "
        "CFR regulation, never historical context."
    )
    lines.append("")

    records: list[tuple[str, MemoryRecord]] = []
    if lookup.exact is not None:
        records.append(("exact", lookup.exact))
    for rec in lookup.near:
        records.append(("near", rec))

    for idx, (match_type, rec) in enumerate(records, start=1):
        citations = [ev.get("citation", "") for ev in rec.evidence if ev.get("citation")]
        lines.append(f"{idx}. [memory_id: {rec.record_id}] [match: {match_type}]")
        lines.append(f"   Prior outcome: {rec.status} (confidence {rec.confidence:.2f})")
        lines.append(f"   Prior citation: {rec.citation}")
        lines.append(f"   Prior reason: {rec.reason}")
        if citations:
            lines.append(f"   Prior evidence citations: {', '.join(citations)}")
        lines.append("")

    lines.append("(End of historical context.)")
    lines.append("")
    return "\n".join(lines)