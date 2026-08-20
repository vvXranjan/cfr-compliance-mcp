"""P1 tests: evidence provenance and the LLM-fabrication guard.

The evidence model carries provenance (source, retrieved_at,
retrieval_method, version) plus an authoritative regulatory date. These
tests pin that provenance is populated ONLY from authoritative metadata
and that the LLM can never manufacture it.
"""

from __future__ import annotations

from agent.compliance_agent import ComplianceAgent
from agent.models import EvidencePassage


def test_evidence_provenance_defaults() -> None:
    ev = EvidencePassage(title=40, text_span="x", citation="40 CFR 261.10")
    assert ev.source == ""
    assert ev.retrieved_at == ""
    assert ev.retrieval_method == ""
    assert ev.version == ""
    assert ev.confidence is None
    assert ev.date == ""


def test_evidence_provenance_roundtrip() -> None:
    ev = EvidencePassage(
        title=40,
        part="261",
        section="10",
        date="2026-08-17",
        text_span="x",
        citation="40 CFR 261.10",
        source="eCFR",
        retrieved_at="2026-08-19T12:00:00+00:00",
        retrieval_method="ecfr_api",
        version="2026-08-01",
        confidence=0.95,
    )
    d = ev.model_dump()
    assert d["source"] == "eCFR"
    assert d["retrieved_at"] == "2026-08-19T12:00:00+00:00"
    assert d["retrieval_method"] == "ecfr_api"
    assert d["version"] == "2026-08-01"
    assert d["confidence"] == 0.95
    assert d["date"] == "2026-08-17"


def test_authoritative_date_is_preserved() -> None:
    ev = EvidencePassage(title=40, date="2026-08-17", text_span="x", citation="40 CFR 261.10")
    assert ev.date == "2026-08-17"


def test_missing_date_stays_empty() -> None:
    ev = EvidencePassage.model_validate(
        {"title": 40, "text_span": "x", "citation": "40 CFR 261.10"}
    )
    assert ev.date == ""


def test_llm_cannot_fabricate_evidence_metadata() -> None:
    """LLM-supplied provenance keys are dropped; date is reset to ''.

    The LLM may describe the passage it read but is never the authority
    for where it came from or which version/date it represents.
    """
    fabricated = {
        "title": 40,
        "part": "261",
        "section": "10",
        "date": "1999-12-31",  # fabricated -- must be discarded
        "text_span": "x",
        "citation": "40 CFR 261.10",
        "source": "made up",
        "retrieved_at": "2020-01-01T00:00:00+00:00",
        "retrieval_method": "hallucinated",
        "version": "v9.9.9",
        "confidence": 0.99,
    }
    coerced = ComplianceAgent._coerce_evidence(fabricated)

    assert coerced["date"] == ""
    for key in ("source", "retrieved_at", "retrieval_method", "version", "confidence"):
        assert key not in coerced, f"LLM-fabricated provenance key leaked: {key}"

    # Round-trips through a real EvidencePassage without provenance.
    ev = EvidencePassage(**coerced)
    assert ev.date == ""
    assert ev.source == ""
    assert ev.version == ""