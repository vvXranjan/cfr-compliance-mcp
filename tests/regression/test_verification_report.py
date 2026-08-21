"""
Regression test for VerificationReport bug.

Bug: VerificationReport is a plain Python class with type annotations but no
`__init__` method. The verify_compliance() function tries to construct it
with keyword arguments, which fails with "VerificationReport() takes no arguments".

After fix: VerificationReport is a Pydantic BaseModel and accepts keyword
arguments. original_result must be a ComplianceResult instance or a dict
with all required fields (clause_title, clause_id, status, confidence, reason, evidence).

This test documents the bug and serves as a regression test.
"""

from agent.models import ComplianceResult
from agent.verification_agent import VerificationReport


def make_test_clause_result():
    """Create a minimal ComplianceResult for testing."""
    return ComplianceResult(
        clause_title="40",
        clause_id="test-001",
        status="Compliant",
        confidence=0.9,
        reason="Test reason",
        evidence=[],
    )


def test_verification_report_construction():
    """Test that VerificationReport can be constructed with arguments.

    After fix: VerificationReport is a Pydantic BaseModel and accepts
    keyword arguments. original_result must be a ComplianceResult instance
    or a dict with all required fields (not None).
    """
    result = make_test_clause_result()
    # After fix, VerificationReport accepts keyword arguments
    report = VerificationReport(
        original_result=result,
        verified=False,
        checks={},
        recommendation="review",
        notes="Test note",
    )
    # If we get here, the bug is fixed
    assert report is not None
    assert report.verified is False
    assert report.recommendation == "review"
    assert report.notes == "Test note"
    print("PASS: VerificationReport accepts keyword arguments with proper original_result")


def test_verification_report_fields():
    """Test that VerificationReport has the expected fields."""
    result = make_test_clause_result()
    report = VerificationReport(
        original_result=result,
        verified=False,
        checks={},
        recommendation="review",
        notes="Test note",
    )
    assert hasattr(report, 'original_result')
    assert hasattr(report, 'verified')
    assert hasattr(report, 'checks')
    assert hasattr(report, 'recommendation')
    assert hasattr(report, 'notes')
    assert report.original_result == result
    assert report.verified is False
    assert report.recommendation == "review"
    assert report.notes == "Test note"
    print("PASS: VerificationReport has all expected fields and values")


def test_verification_report_with_dict():
    """Test that VerificationReport accepts a dict for original_result.

    After fix: VerificationReport accepts a dict for original_result, but
    the dict must contain all required ComplianceResult fields:
    clause_title, clause_id, status, confidence, reason, evidence.
    """
    report = VerificationReport(
        original_result={
            "clause_title": "40",
            "clause_id": "test-001",
            "status": "Compliant",
            "confidence": 0.9,
            "reason": "Test reason",
            "evidence": [],
        },
        verified=True,
        checks={"deterministic_consistency": True},
        recommendation="accept",
        notes="Dict test",
    )
    assert report is not None
    assert report.verified is True
    assert report.recommendation == "accept"
    print("PASS: VerificationReport accepts dict for original_result with full fields")
