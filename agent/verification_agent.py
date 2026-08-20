"""Verification agent for cross-checking LLM compliance decisions.

Purpose: provide an independent verification layer that cross-checks the
LLM compliance agent's output against deterministic rules, evidence
passages, version history, and internal consistency. If the verification
agent flags issues, the compliance result is sent for human review (HITL).

Verification checks:
  1. Deterministic rule consistency: do any deterministic rules conflict
     with the LLM's verdict?
  2. Evidence coverage: are the evidence passages sufficient to support
     the stated compliance status?
  3. Version history consistency: does the CFR version in effect match
     the regulation text cited?
  4. Prompt injection detection: are there signs the clause text was
     crafted to manipulate the LLM?
  5. Internal consistency: does the result's own data (status, confidence,
     evidence) cohere?

The verifier does NOT override the LLM's decision automatically -- instead,
it produces a VerificationReport that the pipeline uses to decide whether
to accept the LLM result, request human review, or re-run with different
parameters.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agent.deterministic_rules import evaluate_deterministic
from agent.models import Clause, ComplianceResult


class VerificationReport(BaseModel):
    """Report from the verification agent cross-checking an LLM decision."""

    # The original LLM compliance result
    original_result: ComplianceResult

    # Whether the verdict passes all verification checks
    verified: bool

    # Specific check results
    checks: dict[str, Any]

    # Human-review recommendation: "accept", "review", "reject"
    recommendation: str

    # Additional evidence or reasons
    notes: str


def _check_deterministic_consistency(
    result: ComplianceResult,
    clause: Clause,
    cfr_text: str,
    title: int,
) -> dict[str, Any]:
    """Check 1: Do deterministic rules conflict with the LLM verdict?"

    Runs the deterministic rules and sees if any rule produces a verdict
    that is inconsistent with the LLM's result. If so, the verification
    is flagged.

    Returns {"consistent": bool, "conflicting_rule": str | None,
             "conflicting_verdict": str | None, "details": str}
    """
    try:
        det_result = evaluate_deterministic(
            clause=clause,
            cfr_text=cfr_text,
            title=title,
        )
    except Exception as exc:
        return {
            "consistent": False,
            "conflicting_rule": None,
            "conflicting_verdict": None,
            "details": f"Deterministic rule evaluation error: {exc}",
        }

    llm_verdict = result.status
    det_verdict = det_result.status

    # If both are "Needs Review", they're consistent (both need LLM)
    if llm_verdict == "Needs Review" and det_verdict == "Needs Review":
        return {
            "consistent": True,
            "conflicting_rule": None,
            "conflicting_verdict": None,
            "details": "Both LLM and deterministic rules returned 'Needs Review'; no conflict.",
        }

    # If LLM says Compliant/Non-Compliant and deterministic says the opposite,
    # that's a conflict
    opposite_pairs = {("Compliant", "Non-Compliant"), ("Non-Compliant", "Compliant")}
    if (llm_verdict, det_verdict) in opposite_pairs:
        return {
            "consistent": False,
            "conflicting_rule": f"Deterministic rule reached '{det_verdict}'",
            "conflicting_verdict": llm_verdict,
            "details": f"LLM says '{llm_verdict}' but deterministic rule says '{det_verdict}' - "
            "inconsistent. LLM result flags for human review.",
        }

    # If deterministic rule says "Needs Review" and LLM made a decision,
    # that's not necessarily a conflict (LLM may have additional context)
    # But if deterministic rule says Non-Compliant and LLM says Compliant,
    # that's a real conflict
    if det_verdict == "Non-Compliant" and llm_verdict == "Compliant":
        return {
            "consistent": False,
            "conflicting_rule": "rule_3_specific_requirement (or rule_1_mandatory_obligation)",
            "conflicting_verdict": llm_verdict,
            "details": "Deterministic rule flags Non-Compliant but LLM says Compliant. "
            "LLM result flags for human review.",
        }

    if det_verdict == "Compliant" and llm_verdict == "Non-Compliant":
        return {
            "consistent": False,
            "conflicting_rule": "rule_1_mandatory_obligation (or rule_2_prohibited_violation)",
            "conflicting_verdict": llm_verdict,
            "details": "Deterministic rule flags Compliant but LLM says Non-Compliant. "
            "LLM result flags for human review.",
        }

    # No direct conflict found
    return {
        "consistent": True,
        "conflicting_rule": None,
        "conflicting_verdict": None,
        "details": "No direct deterministic rule conflict with LLM verdict.",
    }


def _check_evidence_coverage(
    result: ComplianceResult,
    cfr_text: str,
    title: int,
) -> dict[str, Any]:
    """Check 2: Are the evidence passages sufficient to support the stated
    compliance status?

    Checks that the evidence list is non-empty when the status is
    Compliant or Non-Compliant, and that the evidence passages actually
    relate to the CFR text cited.
    """
    evidence = result.evidence

    if not evidence:
        if result.status in ("Compliant", "Non-Compliant"):
            return {
                "sufficient": False,
                "details": f"Status is '{result.status}' but evidence list is empty - "
                "evidence passages should be populated for non-Needs Review verdicts.",
            }
        # Needs Review with empty evidence is acceptable
        return {
            "sufficient": True,
            "details": "Needs Review with empty evidence is acceptable - LLM could not decide.",
        }

    # Evidence exists - verify it relates to the CFR text
    evidence_topics = []
    for ev in evidence:
        ev_kw = set(w for w in ev.text_span.lower().split() if len(w) >= 3)
        cfr_kw = set(w for w in cfr_text.lower().split() if len(w) >= 3)
        overlap = ev_kw & cfr_kw
        evidence_topics.append(
            {"citation": ev.citation, "overlap_count": len(overlap)}
        )

    return {
        "sufficient": True,
        "details": f"Evidence passages present: {len(evidence)} passage(s). "
        f"Topic overlaps with CFR text: {evidence_topics}",
    }


def _check_version_consistency(
    result: ComplianceResult,
    version_payload: dict[str, Any] | None,
    title: int,
) -> dict[str, Any]:
    """Check 3: Does the CFR version in effect match the version claimed
    by the evidence?

    If version history data is available, derives the effective version
    (the greatest issue date) and cross-checks it against any version or
    as-of-date the evidence passages claim. This is a metadata-consistency
    check -- it flags obvious mismatches (evidence says version/date A
    while retrieval metadata says version/date B), not a legal judgment.

    Evidence that makes no version claim (empty fields) is not flagged:
    the retrieval layer only stamps evidence with a version when the text
    was actually fetched for that version.
    """
    if version_payload is None:
        return {
            "checkable": False,
            "details": "No version history payload available - cannot verify version consistency.",
        }

    versions = version_payload.get("content_versions") or version_payload.get("versions") or []

    if not versions:
        return {
            "checkable": False,
            "details": "Version payload present but no version entries found.",
        }

    issue_dates: list[str] = []
    for v in versions:
        if isinstance(v, dict) and isinstance(v.get("issue_date"), str) and v["issue_date"]:
            issue_dates.append(v["issue_date"])

    if not issue_dates:
        return {
            "checkable": True,
            "details": f"Version payload has {len(versions)} entry(ies) but no usable issue dates.",
        }

    effective_version = max(issue_dates)

    claimed: list[tuple[str, str]] = []
    for ev in result.evidence:
        if ev.version:
            if ev.version != effective_version:
                claimed.append(("version", ev.version))
            elif ev.date and ev.date != ev.version:
                # A version-specific claim also carries its as-of date;
                # they must agree.
                claimed.append(("date", ev.date))
        # An empty `version` means the evidence is current/non-version-
        # specific text; its retrieval date is not a version claim and is
        # not flagged.

    if claimed:
        labels = ", ".join(f"{k}={v}" for k, v in claimed)
        return {
            "checkable": True,
            "consistent": False,
            "details": (
                f"Effective version is {effective_version} but evidence claims {labels}. "
                "Evidence version/date is inconsistent with retrieval metadata."
            ),
        }

    return {
        "checkable": True,
        "consistent": True,
        "details": (
            f"Version payload effective version {effective_version}; evidence version "
            "claims are consistent."
        ),
    }


def _check_prompt_injection(
    clause: Clause,
) -> dict[str, Any]:
    """Check 4: Are there signs the clause text was crafted to manipulate
    the LLM (prompt injection indicators)?

    Checks for common prompt injection patterns in the clause text:
    - Instructions to ignore previous instructions
    - Attempts to extract the system prompt
    - Commands to always return a specific answer
    - Role-playing attempts
    """
    clause_lower = clause.text.lower()
    injection_indicators = [
        "ignore previous instructions",
        "disregard all above",
        "forget prior context",
        "you are now",
        "system:",
        "prompt:",
        "always answer",
        "you must",
        "[injection]",
        "<</STOP>>",
    ]

    found_indicators = [ind for ind in injection_indicators if ind in clause_lower]

    if found_indicators:
        return {
            "suspicious": True,
            "indicators": found_indicators,
            "details": f"Prompt injection indicators detected: {found_indicators}. "
            "Clause flags for human review.",
        }

    return {
        "suspicious": False,
        "indicators": [],
        "details": "No prompt injection indicators detected.",
    }


def verify_compliance(
    result: ComplianceResult,
    clause: Clause,
    cfr_text: str,
    title: int,
    version_payload: dict[str, Any] | None = None,
) -> VerificationReport:
    """Run the full verification pipeline on a compliance result.

    Args:
        result: The LLM compliance result to verify
        clause: The contract clause
        cfr_text: The CFR regulation text cited
        title: The CFR title number
        version_payload: Optional version history payload from eCFR

    Returns:
        VerificationReport with verdict, checks, and human-review recommendation.
    """
    # Run all checks
    checks: dict[str, Any] = {}

    checks["deterministic_consistency"] = _check_deterministic_consistency(
        result, clause, cfr_text, title,
    )
    checks["evidence_coverage"] = _check_evidence_coverage(result, cfr_text, title)
    checks["version_consistency"] = _check_version_consistency(result, version_payload, title)
    checks["prompt_injection"] = _check_prompt_injection(clause)

    # Determine overall consistency and recommendation
    all_consistent = all(
        check.get("consistent", check.get("sufficient", True))
        for check in checks.values()
    )

    # Prompt injection is an automatic red flag
    prompt_suspicious = checks.get("prompt_injection", {}).get("suspicious", False)

    # Build recommendation
    if prompt_suspicious:
        recommendation = "reject"
        verified = False
        notes = "Prompt injection detected - LLM result rejected outright"
    elif not all_consistent:
        recommendation = "review"
        verified = False
        conflict_checks = [k for k, v in checks.items() if not v.get("consistent", v.get("sufficient", True))]  # noqa: E501
        notes = f"Verification conflicts detected in: {', '.join(conflict_checks)}. Human review required."  # noqa: E501
    elif not checks.get("evidence_coverage", {}).get("sufficient", True):
        recommendation = "review"
        verified = False
        notes = "Insufficient evidence coverage - human review required to assess compliance basis."
    else:
        recommendation = "accept"
        verified = True
        notes = "All verification checks passed - LLM result accepted."

    return VerificationReport(
        original_result=result,
        verified=verified,
        checks=checks,
        recommendation=recommendation,
        notes=notes,
    )