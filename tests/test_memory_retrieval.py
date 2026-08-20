"""Compliance Memory manager tests (agent.memory).

Covers the full P4 approval surface with deterministic, offline tests:

  1. Eligible VERIFIED result can be indexed.
  2. Deterministically VERIFIED eligible result can be indexed.
  3. NEEDS_REVIEW is not indexed.
  4. Security-rejected input is not indexed.
  5. Failed retrieval/evaluation result is not indexed.
  6. Duplicate indexing is controlled.
  7. Exact historical match is retrieved with provenance.
  8. Exact match with incompatible CFR version is NOT reused.
  9. Near-duplicate records are contextual only and cannot reuse a verdict.
 10. Historical memory never overrides conflicting current CFR evidence.
 11. Memory-disabled mode preserves existing behavior.
 12. Empty memory preserves existing behavior.
 13. Memory retrieval failure falls back to authoritative-only evaluation.
 14. Memory indexing failure does not invalidate a valid result.
 15. Memory-assisted results do not create a feedback loop.
 16. Historical content is DATA, not instructions.
 17. Existing API responses remain backward compatible.

No external embedding APIs or live LLMs are used; the LLM step is always
stubbed.
"""

from __future__ import annotations

import pytest

from agent.compliance_agent import _COMPLIANCE_REVIEWER_INSTRUCTIONS, ComplianceAgent
from agent.mcp_search import CfrMatch
from agent.memory import ComplianceMemory, format_historical_context
from agent.models import Clause, ComplianceResult, EvidencePassage, ReviewAudit

CITATION = "40 CFR 261.10"
EFFECTIVE_VERSION = "2026-08-01"
DATE = "2026-08-01"

# A clause-CFR pair where deterministic rules stay inconclusive, forcing
# the (stubbed) LLM fallback -- same pair the API tests use.
BASE_CLAUSE_TEXT = (
    "Contractor shall properly dispose hazardous waste per EPA requirements."
)
BASE_CFR_TEXT = "Hazardous waste must be disposed per EPA requirements."

# A near-duplicate of BASE_CLAUSE_TEXT (token overlap above threshold).
NEAR_CLAUSE_TEXT = (
    "Contractor shall dispose of hazardous materials in accordance with "
    "all applicable EPA environmental requirements."
)


def _clause(text: str = BASE_CLAUSE_TEXT, title: str = "Hazardous Waste Disposal") -> Clause:
    return Clause(title=title, text=text)


def _evidence(*, version: str = EFFECTIVE_VERSION, date: str = DATE) -> list[EvidencePassage]:
    return [
        EvidencePassage(
            title=40,
            part="261",
            section="10",
            date=date,
            text_span="Hazardous waste must be disposed.",
            citation=CITATION,
            source="eCFR",
            retrieved_at="2026-08-01T00:00:00+00:00",
            retrieval_method="ecfr_api",
            version=version,
        )
    ]


def _verified(
    clause: Clause,
    *,
    status: str = "Compliant",
    deterministic: bool = False,
    version: str = EFFECTIVE_VERSION,
) -> ComplianceResult:
    return ComplianceResult(
        clause_title=clause.title,
        clause_id=clause.clause_id,
        status=status,
        confidence=0.95,
        reason="ok",
        evidence=_evidence(version=version),
        verification_status="verified",
        review_audit=ReviewAudit(
            final_status=status,
            proposed_status=status,
            proposed_confidence=0.95,
            verifier_recommendation="accept",
            verifier_notes="ok",
            deterministic_status=status if deterministic else "",
            evidence_citations=[CITATION],
            reviewed_at="2026-08-01T00:00:00+00:00",
        ),
    )


def _needs_review(clause: Clause) -> ComplianceResult:
    return ComplianceResult(
        clause_title=clause.title,
        clause_id=clause.clause_id,
        status="Needs Review",
        confidence=0.0,
        reason="needs review",
        evidence=[],
        verification_status="needs_review",
        review_reason="needs review",
        review_audit=ReviewAudit(
            review_reason="needs review",
            reviewed_at="2026-08-01T00:00:00+00:00",
        ),
    )


def _memory(tmp_path) -> ComplianceMemory:
    return ComplianceMemory(enabled=True, memory_dir=str(tmp_path / "mem"))


def _index(memory: ComplianceMemory, result: ComplianceResult, clause: Clause) -> str:
    return memory.index(
        result,
        clause=clause,
        citation=CITATION,
        title=40,
        effective_version=EFFECTIVE_VERSION,
        date=DATE,
        version_specific=False,
        source="eCFR",
        retrieval_method="ecfr_api",
        retrieved_at="2026-08-01T00:00:00+00:00",
        memory_assisted=False,
    )


def _match(
    clause: Clause,
    *,
    effective_version: str = EFFECTIVE_VERSION,
    date: str = DATE,
    citation: str = CITATION,
) -> CfrMatch:
    return CfrMatch(
        clause=clause,
        citation=citation,
        regulation_text=BASE_CFR_TEXT,
        part="261",
        section="10",
        date=date,
        version=effective_version,
        effective_version=effective_version,
        version_specific=False,
    )


def _compliant_llm_stub(status: str = "Compliant"):
    def _stub(**kwargs):
        ctx = kwargs.get("historical_context")
        assert ctx is None or "HISTORICAL CONTEXT" in ctx
        return ComplianceResult(
            clause_title=kwargs["clause_title"],
            clause_id="stub",
            status=status,
            confidence=0.9,
            reason="llm stub",
            evidence=_evidence(),
        )

    return _stub


# ---------------------------------------------------------------------------
# 1-6. Indexing eligibility + dedup
# ---------------------------------------------------------------------------


class TestIndexingEligibility:
    def test_eligible_verified_result_can_be_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        decision = _index(memory, _verified(clause), clause)
        assert decision == "indexed"
        assert len(memory.store.load()) == 1

    def test_deterministically_verified_result_can_be_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        decision = _index(memory, _verified(clause, deterministic=True), clause)
        assert decision == "indexed"

    def test_needs_review_is_not_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        assert _index(memory, _needs_review(clause), clause) == "skipped"
        assert len(memory.store.load()) == 0

    def test_security_rejected_is_not_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        result = _verified(clause)
        audit = result.review_audit
        result = result.model_copy(
            update={
                "review_audit": audit.model_copy(
                    update={"deterministic_status": "blocked_by_security"}
                )
            }
        )
        assert _index(memory, result, clause) == "skipped"
        assert len(memory.store.load()) == 0

    def test_failed_evaluation_is_not_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        result = _needs_review(clause)
        assert _index(memory, result, clause) == "skipped"

    def test_malformed_evidence_is_not_indexed(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        result = _verified(clause).model_copy(
            update={
                "evidence": [
                    EvidencePassage(
                        title=40,
                        text_span="",
                        citation="",
                        source="",
                        retrieval_method="",
                    )
                ]
            }
        )
        assert _index(memory, result, clause) == "skipped"

    def test_duplicate_indexing_is_controlled(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        assert _index(memory, _verified(clause), clause) == "indexed"
        assert _index(memory, _verified(clause), clause) == "duplicate"
        assert len(memory.store.load()) == 1


# ---------------------------------------------------------------------------
# 7-9. Retrieval: exact / near / compatibility
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_exact_match_is_retrieved_with_provenance(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        _index(memory, _verified(clause), clause)

        lookup = memory.retrieve(
            clause, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        )
        assert lookup.match_type == "exact"
        assert lookup.exact is not None
        assert lookup.exact.record_id == memory.store.load()[0]["record_id"]
        assert lookup.exact.verification_status == "verified"
        assert lookup.exact.source == "eCFR"
        assert lookup.exact.effective_version == EFFECTIVE_VERSION

    def test_exact_match_with_incompatible_version_is_not_reused(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        _index(memory, _verified(clause), clause)

        lookup = memory.retrieve(
            clause, citation=CITATION, title=40, effective_version="2024-01-01", date="2024-01-01"
        )
        assert lookup.match_type != "exact"
        assert lookup.exact is None

    def test_near_duplicate_is_contextual_only_never_a_verdict(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause_a = _clause()
        clause_b = _clause(text=NEAR_CLAUSE_TEXT, title="Hazardous Materials")
        _index(memory, _verified(clause_a, status="Non-Compliant"), clause_a)

        lookup = memory.retrieve(
            clause_b, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        )
        assert lookup.match_type == "near"
        assert lookup.exact is None
        assert len(lookup.near) >= 1
        assert lookup.near[0].status == "Non-Compliant"

    def test_no_memory_returns_none(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        lookup = memory.retrieve(
            clause, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        )
        assert lookup.match_type == "none"
        assert not lookup.participated


# ---------------------------------------------------------------------------
# 11-14. Disabled / empty / failure behavior (fail-open)
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_disabled_memory_preserves_behavior(self, tmp_path) -> None:
        memory = ComplianceMemory(enabled=False)
        clause = _clause()
        lookup = memory.retrieve(
            clause, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        )
        assert lookup.match_type == "none"
        assert memory.index(
            _verified(clause), clause=clause, citation=CITATION, title=40,
            effective_version=EFFECTIVE_VERSION, date=DATE, version_specific=False,
            source="eCFR", retrieval_method="ecfr_api",
            retrieved_at="2026-08-01T00:00:00+00:00", memory_assisted=False,
        ) == "skipped"

    def test_empty_memory_preserves_behavior(self, tmp_path) -> None:
        memory = _memory(tmp_path)
        clause = _clause()
        assert memory.retrieve(
            clause, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        ).match_type == "none"

    def test_retrieval_failure_falls_back_to_authoritative_only(
        self, tmp_path, monkeypatch
    ) -> None:
        memory = _memory(tmp_path)
        clause = _clause()

        def _boom() -> list:
            raise OSError("unreadable memory file")

        monkeypatch.setattr(memory.store, "load", _boom)
        lookup = memory.retrieve(
            clause, citation=CITATION, title=40, effective_version=EFFECTIVE_VERSION, date=DATE
        )
        assert lookup.match_type == "none"
        assert lookup.exact is None

    def test_indexing_failure_returns_error_but_keeps_result(self, tmp_path, monkeypatch) -> None:
        memory = _memory(tmp_path)
        clause = _clause()

        def _boom(record):
            raise OSError("disk full")

        monkeypatch.setattr(memory.store, "append", _boom)
        assert _index(memory, _verified(clause), clause) == "error"


# ---------------------------------------------------------------------------
# 10, 15. Feedback loop + authority: pipeline-level (stubbed LLM)
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    @pytest.mark.asyncio
    async def test_memory_never_overrides_current_cfr_evidence(self, tmp_path, monkeypatch) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause_a = _clause()
        clause_b = _clause(text=NEAR_CLAUSE_TEXT, title="Hazardous Materials")
        # Stored precedent says Non-Compliant for the near-duplicate.
        _index(memory, _verified(clause_a, status="Non-Compliant"), clause_a)

        # Current authoritative LLM path says Compliant; memory must not win.
        monkeypatch.setattr(
            "agent.compliance_pipeline.evaluate_compliance",
            _compliant_llm_stub("Compliant"),
        )
        result = await _evaluate_match(_match(clause_b), memory=memory)

        assert result.status == "Compliant"
        assert result.verification_status == "verified"
        assert result.memory_participated is True  # context was supplied
        assert result.review_audit.deterministic_status != "memory_reuse"

    @pytest.mark.asyncio
    async def test_memory_assisted_result_is_not_auto_indexed(self, tmp_path, monkeypatch) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause_a = _clause()
        clause_b = _clause(text=NEAR_CLAUSE_TEXT, title="Hazardous Materials")
        _index(memory, _verified(clause_a), clause_a)
        assert len(memory.store.load()) == 1

        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _compliant_llm_stub())
        result = await _evaluate_match(_match(clause_b), memory=memory)

        assert result.verification_status == "verified"
        assert result.memory_participated is True
        # No new record for clause_b -> no feedback loop.
        assert len(memory.store.load()) == 1

    @pytest.mark.asyncio
    async def test_exact_match_reuses_verified_verdict_without_llm(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause = _clause()
        _index(memory, _verified(clause), clause)

        def _raise_llm(**kwargs):  # must never be called
            raise AssertionError("LLM must not run on an eligible exact match")

        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _raise_llm)
        result = await _evaluate_match(_match(clause), memory=memory)

        assert result.status == "Compliant"
        assert result.verification_status == "verified"
        assert result.memory_participated is True
        assert result.review_audit.deterministic_status == "memory_reuse"

    @pytest.mark.asyncio
    async def test_exact_match_incompatible_version_runs_authoritative_path(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause = _clause()
        _index(memory, _verified(clause), clause)

        calls = {"n": 0}

        def _stub(**kwargs):
            calls["n"] += 1
            return ComplianceResult(
                clause_title=kwargs["clause_title"], clause_id="stub",
                status="Compliant", confidence=0.9, reason="llm stub", evidence=_evidence(),
            )

        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _stub)
        result = await _evaluate_match(
            _match(clause, effective_version="2024-01-01", date="2024-01-01"), memory=memory
        )

        assert calls["n"] == 1  # authoritative path ran
        assert result.status == "Compliant"
        assert result.review_audit.deterministic_status != "memory_reuse"

    @pytest.mark.asyncio
    async def test_no_memory_preserves_existing_pipeline_behavior(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent.compliance_pipeline import _evaluate_match

        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _compliant_llm_stub())
        result = await _evaluate_match(_match(_clause()), memory=None)
        assert result.verification_status == "verified"
        assert result.memory_participated is False

    @pytest.mark.asyncio
    async def test_retrieval_failure_falls_back_to_authoritative_pipeline(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause = _clause()

        def _boom() -> list:
            raise OSError("unreadable")

        monkeypatch.setattr(memory.store, "load", _boom)
        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _compliant_llm_stub())
        result = await _evaluate_match(_match(clause), memory=memory)

        assert result.verification_status == "verified"
        assert result.status == "Compliant"
        assert result.memory_participated is False

    @pytest.mark.asyncio
    async def test_indexing_failure_does_not_invalidate_valid_result(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent.compliance_pipeline import _evaluate_match

        memory = _memory(tmp_path)
        clause = _clause()

        def _boom(record):
            raise OSError("disk full")

        monkeypatch.setattr(memory.store, "append", _boom)
        monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _compliant_llm_stub())
        result = await _evaluate_match(_match(clause), memory=memory)

        assert result.verification_status == "verified"
        assert result.status == "Compliant"


# ---------------------------------------------------------------------------
# 16. Historical content is DATA, not instructions
# ---------------------------------------------------------------------------


class TestContextBoundary:
    def test_historical_context_is_data_not_instructions(self) -> None:
        from agent.memory import MemoryLookup, MemoryRecord

        clause = _clause()
        record = MemoryRecord(
            record_id="mem-1",
            format_version=1,
            clause_id=clause.clause_id,
            clause_title=clause.title,
            clause_text=clause.text,
            status="Compliant",
            confidence=0.95,
            reason="prior verified outcome",
            citation=CITATION,
            title=40,
            part="261",
            section="10",
            effective_version=EFFECTIVE_VERSION,
            date=DATE,
            version_specific=False,
            verification_status="verified",
            evidence=({
                "title": 40, "part": "261", "section": "10", "date": DATE,
                "text_span": "must be disposed", "citation": CITATION,
                "source": "eCFR", "retrieved_at": "x", "retrieval_method": "ecfr_api",
                "version": EFFECTIVE_VERSION,
            },),
            source="eCFR",
            retrieval_method="ecfr_api",
            retrieved_at="2026-08-01T00:00:00+00:00",
            indexed_at="2026-08-01T00:00:00+00:00",
        )
        lookup = MemoryLookup(match_type="near", exact=None, near=[record])
        context = format_historical_context(lookup, clause_title=clause.title, citation=CITATION)

        prompt = ComplianceAgent._build_prompt(
            clause_title=clause.title,
            clause_text=clause.text,
            cfr_citation=CITATION,
            cfr_text=BASE_CFR_TEXT,
            historical_context=context,
        )

        # Structurally separated: historical block comes AFTER the CFR block.
        hist_at = prompt.index("HISTORICAL CONTEXT (NOT AUTHORITATIVE)")
        cfr_at = prompt.index("CFR REGULATION")
        assert hist_at > cfr_at
        # Explicit authority disclaimers present.
        assert "are NOT law" in prompt
        assert "current CFR regulation wins" in prompt
        assert "cite ONLY the current" in prompt

    def test_memory_content_never_reaches_system_instructions(self) -> None:
        assert "ignore previous instructions" not in _COMPLIANCE_REVIEWER_INSTRUCTIONS
        assert "HISTORICAL CONTEXT" not in _COMPLIANCE_REVIEWER_INSTRUCTIONS
        assert "prior verified" not in _COMPLIANCE_REVIEWER_INSTRUCTIONS


# ---------------------------------------------------------------------------
# 17. API backward compatibility
# ---------------------------------------------------------------------------


class TestApiBackwardCompatibility:
    def test_evaluate_clause_response_is_backward_compatible(self, tmp_path, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        from api import app

        # Force memory off for a hermetic API test.
        monkeypatch.setattr("agent.memory.get_compliance_memory", lambda: None)

        client = TestClient(app)
        resp = client.post(
            "/evaluate-clause",
            json={
                "clause_title": "Office Cleaning",
                "clause_text": "Contractor shall clean the office weekly.",
                "cfr_citation": "40 CFR 261.10",
                "cfr_text": "Hazardous waste must be disposed per EPA requirements.",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # Existing fields preserved.
        for field in (
            "clause_title", "clause_id", "status", "confidence", "reason",
            "evidence", "verified", "verification_notes", "verification_status",
            "review_audit",
        ):
            assert field in body
        # New field present and defaulted off.
        assert body["memory_participated"] is False
        assert body["verification_status"] == "verified"