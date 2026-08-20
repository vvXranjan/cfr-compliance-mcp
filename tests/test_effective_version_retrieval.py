"""P2.6 tests: effective-version section retrieval + verifier version consistency.

Covers:
  * version-specific section fetch (dated retrieve_section) wiring
  * graceful fallback to current text when the dated fetch fails
    (current text is never labeled historical)
  * verifier detection of evidence version/date mismatches vs retrieval
    metadata
  * no false positives for current (non-version-specific) evidence
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.mcp_search import CfrMatch, retrieve_for_clause
from agent.models import Clause, ComplianceResult, EvidencePassage
from agent.verification_agent import _check_version_consistency
from tests.test_version_aware_retrieval import _FakeClient, _FakeResult


class _VersionAwareFakeClient(_FakeClient):
    """Returns dated section text when retrieve_section carries a date."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.current_payload = {
            "text": "CURRENT 261.10 text.",
            "citation": {
                "title": 40, "part": "261", "section": "10",
                "date": "2026-08-17", "heading": "261.10",
                "url": "u",
            },
        }
        self.version_payload = {
            "text": "VERSION 2026-08-01 text.",
            "citation": {
                "title": 40, "part": "261", "section": "10",
                "date": "2026-08-01", "heading": "261.10",
                "url": "u",
            },
        }
        self.version_history = {
            "title": 40,
            "versions": [{"issue_date": "2026-07-01"}, {"issue_date": "2026-08-01"}],
        }

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((name, arguments))
        if name == "retrieve_section" and arguments.get("date"):
            return _FakeResult(self.version_payload)
        if name == "retrieve_section":
            return _FakeResult(self.current_payload)
        if name == "get_version_history":
            return _FakeResult(self.version_history)
        return _FakeResult(
            {
                "results": [{"hierarchy": {"title": 40, "part": "261", "section": "10"}}],
                "total_count": 1, "current_page": 1, "total_pages": 1,
            }
        )


class TestEffectiveVersionRetrieval:
    async def test_retrieve_for_clause_fetches_version_specific_text(self) -> None:
        clause = Clause(title="Environmental", text="Contractor shall dispose hazardous waste")
        client = _VersionAwareFakeClient()

        match = await retrieve_for_clause(client, clause)

        assert match.error is None
        assert match.version_specific is True
        assert match.effective_version == "2026-08-01"
        assert match.version == "2026-08-01"
        assert match.date == "2026-08-01"
        assert match.regulation_text == "VERSION 2026-08-01 text."

        # A dated retrieve_section call was made with the effective version.
        dated_calls = [
            args for name, args in client.calls
            if name == "retrieve_section" and args.get("date")
        ]
        assert len(dated_calls) == 1
        assert dated_calls[0]["date"] == "2026-08-01"

    async def test_version_fetch_failure_falls_back_to_current_text(self) -> None:
        clause = Clause(title="Environmental", text="Contractor shall dispose hazardous waste")
        client = _VersionAwareFakeClient()
        client.version_history = {
            "error": True, "error_type": "EcfrServerError",
            "message": "boom", "retryable": True,
        }

        match = await retrieve_for_clause(client, clause)

        # Version history failed entirely -> no version metadata.
        assert match.error is None
        assert match.version_payload is None
        assert match.version == ""
        assert match.version_specific is False

    async def test_version_specific_fetch_failure_falls_back_to_current(self) -> None:
        clause = Clause(title="Environmental", text="Contractor shall dispose hazardous waste")
        client = _VersionAwareFakeClient()
        client.version_payload = {
            "error": True, "error_type": "EcfrServerError",
            "message": "no content for that date", "retryable": False,
        }

        match = await retrieve_for_clause(client, clause)

        # The effective version is still derived and preserved...
        assert match.effective_version == "2026-08-01"
        assert match.version_payload is not None
        # ...but the text is the CURRENT one, explicitly not version-specific.
        assert match.version_specific is False
        assert match.version == ""  # never label current text as historical
        assert match.date == "2026-08-17"
        assert match.regulation_text == "CURRENT 261.10 text."

    async def test_invalid_effective_version_skips_dated_fetch(self) -> None:
        clause = Clause(title="Environmental", text="Contractor shall dispose hazardous waste")
        client = _VersionAwareFakeClient()
        client.version_history = {"title": 40, "versions": [{"issue_date": "not-a-date"}]}

        match = await retrieve_for_clause(client, clause)
        assert match.version_specific is False
        assert match.version == ""
        dated = [a for n, a in client.calls if n == "retrieve_section" and a.get("date")]
        assert dated == []


class TestVersionConsistencyCheck:
    def _payload(self) -> dict[str, Any]:
        return {
            "title": 40,
            "versions": [{"issue_date": "2026-07-01"}, {"issue_date": "2026-08-01"}],
        }

    def _result(self, evidence: list[EvidencePassage]) -> ComplianceResult:
        return ComplianceResult(
            clause_title="T", clause_id="x",
            status="Compliant", confidence=0.9, reason="r", evidence=evidence,
        )

    def _ev(self, *, version: str = "", date: str = "") -> EvidencePassage:
        return EvidencePassage(
            title=40, version=version, date=date,
            text_span="x", citation="40 CFR 261.10",
        )

    def test_matching_version_consistent(self) -> None:
        ev = self._ev(version="2026-08-01", date="2026-08-01")
        check = _check_version_consistency(self._result([ev]), self._payload(), 40)
        assert check["consistent"] is True

    def test_version_mismatch_detected(self) -> None:
        ev = self._ev(version="2024-05-15", date="2024-05-15")
        check = _check_version_consistency(self._result([ev]), self._payload(), 40)
        assert check["consistent"] is False
        assert "2026-08-01" in check["details"]
        assert "2024-05-15" in check["details"]

    def test_date_mismatch_with_version_claim_detected(self) -> None:
        ev = self._ev(version="2026-08-01", date="2026-08-17")
        check = _check_version_consistency(self._result([ev]), self._payload(), 40)
        assert check["consistent"] is False

    def test_current_evidence_no_version_not_flagged(self) -> None:
        """Empty version = current text; its retrieval date is not a claim."""
        ev = self._ev(version="", date="2026-08-17")
        check = _check_version_consistency(self._result([ev]), self._payload(), 40)
        assert check["consistent"] is True

    def test_no_payload_not_checkable(self) -> None:
        ev = self._ev(version="2026-08-01")
        check = _check_version_consistency(self._result([ev]), None, 40)
        assert check["checkable"] is False

    def test_empty_evidence_consistent(self) -> None:
        check = _check_version_consistency(self._result([]), self._payload(), 40)
        assert check["consistent"] is True


@pytest.mark.asyncio
async def test_pipeline_routes_version_mismatch_to_review(monkeypatch) -> None:
    """A clause whose evidence version disagrees with retrieval metadata
    must be routed to human review by the real verifier."""
    from agent.compliance_pipeline import _evaluate_match

    clause = Clause(
        title="Environmental",
        text="Contractor shall properly dispose hazardous waste according to EPA requirements",
    )
    match = CfrMatch(
        clause=clause,
        citation="40 CFR 261.10",
        regulation_text="Hazardous waste must be disposed per EPA requirements.",
        part="261", section="10",
        date="2024-05-15", version="2024-05-15", effective_version="2026-08-01",
        version_specific=False,
        version_payload={
            "title": 40,
            "versions": [{"issue_date": "2026-07-01"}, {"issue_date": "2026-08-01"}],
        },
    )
    # Simulate an LLM result whose evidence wrongly claims an old version.
    def _llm(**kwargs):
        return ComplianceResult(
            clause_title="Environmental", clause_id="x",
            status="Compliant", confidence=0.9, reason="llm",
            evidence=[
                EvidencePassage(
                    title=40, part="261", section="10", date="2024-05-15",
                    version="2024-05-15",
                    text_span="Hazardous waste must be disposed.",
                    citation="40 CFR 261.10",
                )
            ],
        )

    monkeypatch.setattr("agent.compliance_pipeline.evaluate_compliance", _llm)

    result = await _evaluate_match(match)

    # Enrichment preserves the claimed version; the verifier flags the
    # mismatch against the effective version -> Needs Review.
    assert result.verification_status == "needs_review"
    assert "Verification" in result.review_reason
    assert any(ev.version == "2024-05-15" for ev in result.evidence)