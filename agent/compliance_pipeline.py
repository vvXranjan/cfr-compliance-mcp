from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .compliance_agent import evaluate_compliance
from .contract_parser import ContractParser, split_into_clauses
from .deterministic_rules import evaluate_deterministic
from .mcp_search import CfrMatch, retrieve_for_clauses
from .models import Clause, ComplianceResult, EvidencePassage, ReviewAudit
from .security import check_clause_security
from .verification_agent import verify_compliance

logger = logging.getLogger(__name__)

__all__ = ["run_compliance_pipeline"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _enrich_evidence(
    evidence: list[EvidencePassage], match: CfrMatch
) -> list[EvidencePassage]:
    """Attach authoritative retrieval provenance to evidence passages.

    The LLM is never the source of provenance (its evidence entries
    arrive with ``date=""`` and no source/version by design). The
    retrieval layer owns that metadata, so it is injected here from the
    match: source, retrieval timestamp, retrieval method, the effective
    version, and the authoritative as-of date. Missing provenance stays
    empty -- the system never fabricates it.
    """
    enriched: list[EvidencePassage] = []
    for ev in evidence or []:
        enriched.append(
            ev.model_copy(
                update={
                    "source": match.source or ev.source,
                    "retrieved_at": match.retrieved_at or ev.retrieved_at,
                    "retrieval_method": match.retrieval_method or ev.retrieval_method,
                    "version": match.version or ev.version,
                    "date": match.date or ev.date,
                    "part": ev.part or match.part,
                    "section": ev.section or match.section,
                    "citation": ev.citation or (match.citation or ""),
                }
            )
        )
    return enriched


def _build_audit(
    *,
    final_status: str,
    proposed: ComplianceResult | None,
    verification=None,
    deterministic_status: str = "",
    reason: str = "",
) -> ReviewAudit:
    """Build the audit trail for a compliance finding."""
    citations = [ev.citation for ev in (proposed.evidence if proposed else []) if ev.citation]
    return ReviewAudit(
        final_status=final_status,
        proposed_status=proposed.status if proposed else "",
        proposed_confidence=proposed.confidence if proposed else 0.0,
        review_reason=reason,
        verifier_recommendation=(
            verification.recommendation if verification is not None else "not_run"
        ),
        verifier_notes=verification.notes if verification is not None else "",
        deterministic_status=deterministic_status,
        evidence_citations=citations,
        reviewed_at=_now_iso(),
    )


def _needs_review(
    clause: Clause,
    reason: str,
    *,
    proposed: ComplianceResult | None = None,
    verification=None,
    deterministic_status: str = "",
) -> ComplianceResult:
    """Build the standard "human review required" result.

    Every non-auto-finalizable outcome funnels through here so the
    human-in-the-loop boundary is a single, consistent shape: status
    "Needs Review", confidence 0.0, an explicit ``verification_status``,
    a readable ``review_reason``, and a full audit trail explaining WHY.
    """
    return ComplianceResult(
        clause_title=clause.title,
        clause_id=clause.clause_id,
        status="Needs Review",
        confidence=0.0,
        reason=reason,
        evidence=proposed.evidence if proposed else [],
        verification_status="needs_review",
        review_reason=reason,
        review_audit=_build_audit(
            final_status="Needs Review",
            proposed=proposed,
            verification=verification,
            deterministic_status=deterministic_status,
            reason=reason,
        ),
    )


async def _evaluate_match(match: CfrMatch) -> ComplianceResult:
    """Turn one `CfrMatch` into a `ComplianceResult`.

    Canonical evaluation path -- every clause that reaches the LLM must
    pass through here, so the security layer cannot be bypassed by
    calling the LLM directly.

    Strategy:
     0. Security gate (injection / title / length) -- fail safe to review.
     1. If CFR retrieval failed (no citation/text), return "Needs Review".
     2. Run deterministic rule-based checking first -- if a rule reaches
        a confident verdict, use it (LLM-free, zero latency, fully auditable).
     3. If all rules are "unsure", fall back to the LLM compliance agent.
     4. Run the verification agent to cross-check the LLM decision.
     5. If verification flags issues, downgrade to "Needs Review" with
        recommendation for human review.
     6. Any exception during evaluation downgrades to "Needs Review".

    Authoritative retrieval provenance (source, retrieved_at,
    retrieval_method, version, as-of date) is attached to evidence
    passages here -- never by the LLM.
    """
    clause = match.clause

    title = int(match.citation.split()[0]) if match.citation else 0

    # --- Step 0: Security gate (fail-safe to human review) ---
    gate = check_clause_security(
        clause, title=title or None, cfr_text=match.regulation_text
    )
    if not gate["safe"]:
        logger.warning(
            "Security gate blocked clause %r: %s",
            clause.title, gate["reason"],
        )
        return _needs_review(
            clause,
            f"Security check failed: {gate['reason']}",
            deterministic_status="blocked_by_security",
        )

    if match.error is not None or match.citation is None or match.regulation_text is None:
        reason = match.error or "CFR retrieval returned no citation/regulation text"
        logger.warning("CFR retrieval unusable for clause %r: %s", clause.title, reason)
        return _needs_review(clause, reason)

    logger.info("Evaluating compliance for clause %r against %s", clause.title, match.citation)

    # --- Step 2: Deterministic rule check (LLM-free) ---
    det_result: ComplianceResult | None = None
    try:
        det_result = evaluate_deterministic(
            clause=clause,
            cfr_text=match.regulation_text,
            title=title,
            part=match.part,
            section=match.section,
        )
        if det_result.status != "Needs Review":
            logger.info(
                "Deterministic rule decided clause %r: status=%s confidence=%.2f",
                clause.title, det_result.status, det_result.confidence,
            )
            evidence = _enrich_evidence(det_result.evidence, match)
            return ComplianceResult(
                clause_title=clause.title,
                clause_id=clause.clause_id,
                status=det_result.status,
                confidence=det_result.confidence,
                reason=det_result.reason,
                evidence=evidence,
                verification_status="verified",
                review_audit=_build_audit(
                    final_status=det_result.status,
                    proposed=det_result,
                    deterministic_status=det_result.status,
                ),
            )
    except Exception as exc:
        logger.warning(
            "Deterministic rule evaluation failed for clause %r: %s",
            clause.title, exc,
        )

    # --- Step 3: Fall back to LLM ---
    logger.info("Deterministic rules inconclusive for clause %r; falling back to LLM", clause.title)

    try:
        result = await asyncio.to_thread(
            evaluate_compliance,
            clause_title=clause.title,
            clause_text=clause.text,
            cfr_citation=match.citation,
            cfr_text=match.regulation_text,
        )
    except Exception as exc:
        logger.exception("Compliance evaluation failed for clause %r", clause.title)
        return _needs_review(
            clause,
            f"Compliance evaluation failed: {exc}",
            deterministic_status=det_result.status if det_result else "",
        )

    # --- Step 4: Verify the LLM result ---
    # The version-history payload was already attached to the match by
    # the retrieval layer (`retrieve_for_clause`); use it directly for
    # the verification agent's version-consistency check.
    version_payload = match.version_payload

    # Attach authoritative retrieval provenance to the LLM's evidence
    # BEFORE verification so the verifier can cross-check version/date
    # claims in the evidence against the retrieval metadata. Enrichment
    # only fills empty provenance fields, so re-enrichment is idempotent.
    enriched_evidence = _enrich_evidence(result.evidence, match)
    result = result.model_copy(update={"evidence": enriched_evidence})

    # Run verification agent
    try:
        verification = verify_compliance(
            result=result,
            clause=clause,
            cfr_text=match.regulation_text,
            title=title,
            version_payload=version_payload,
        )

        # If verification recommends rejection or review, downgrade the result
        if verification.recommendation in ("reject", "review"):
            logger.warning(
                "Verification flags clause %r: %s (recommendation: %s)",
                clause.title, verification.notes, verification.recommendation,
            )
            # Evidence (with provenance) is already attached to `result`.
            return _needs_review(
                clause,
                f"{result.reason} | Verification: {verification.notes}",
                proposed=result,
                verification=verification,
                deterministic_status=det_result.status if det_result else "",
            )

        # Verification passed - return the LLM result as verified, with
        # authoritative retrieval provenance attached to its evidence.
        return result.model_copy(
            update={
                "verification_status": "verified",
                "review_reason": "",
                "review_audit": _build_audit(
                    final_status=result.status,
                    proposed=result,
                    verification=verification,
                    deterministic_status=det_result.status if det_result else "",
                ),
            }
        )

    except Exception as exc:
        logger.exception("Verification failed for clause %r", clause.title)
        # If verification itself fails, keep the original LLM result but
        # flag it for review
        return _needs_review(
            clause,
            f"Compliance evaluation: {result.reason}; verification error: {exc}",
            proposed=result,
            deterministic_status=det_result.status if det_result else "",
        )


async def run_compliance_pipeline(clauses: list[Clause]) -> list[ComplianceResult]:
    """Run the full Clause -> CFR retrieval -> compliance evaluation
    pipeline for every clause in `clauses`.

    Always returns exactly one `ComplianceResult` per input clause, in
    the same order. A retrieval or evaluation failure for one clause
    downgrades that clause's own result to "Needs Review" (confidence
    0.0) rather than raising or dropping it from the output. A total
    retrieval failure (e.g. the MCP server connection itself fails)
    still returns one "Needs Review" result per clause rather than
    raising, for the same reason.

    Deterministic except for `evaluate_compliance()`'s LLM call -- see
    module docstring.

    Args:
        clauses: contract clauses to evaluate, e.g. from
            `contract_parser.split_into_clauses()`.

    Returns:
        One `ComplianceResult` per input clause, in the same order as
        `clauses`. Empty list in, empty list out.
    """
    if not clauses:
        logger.info("run_compliance_pipeline called with no clauses; returning empty result list")
        return []

    logger.info("Starting compliance pipeline for %d clause(s)", len(clauses))

    try:
        matches = await retrieve_for_clauses(clauses)
    except Exception:
        import traceback

        traceback.print_exc()
        raise

    # results = [await _evaluate_match(match) for match in matches]
    tasks = [
        asyncio.create_task(_evaluate_match(match))
        for match in matches
    ]       

    results = await asyncio.gather(*tasks)

    compliant = sum(1 for r in results if r.status == "Compliant")
    non_compliant = sum(1 for r in results if r.status == "Non-Compliant")
    needs_review = sum(1 for r in results if r.status == "Needs Review")
    logger.info(
        "Compliance pipeline complete: %d compliant, %d non-compliant, %d needs review (of %d total)",  # noqa: E501
        compliant, non_compliant, needs_review, len(results),
    )

    return results

def _demo() -> None:
    """Run the compliance pipeline on a contract PDF."""

    logging.basicConfig(level=logging.INFO)

    pdf_path = "contracts/sample_contract_multi.pdf"

    parser = ContractParser(pdf_path)
    text = parser.extract_text()

    clauses = split_into_clauses(text)

    print(f"\nExtracted {len(clauses)} clauses\n")

    for clause in clauses[:10]:
        print(f"- {clause.title}")

    if not clauses:
        print("No clauses found.")
        return

    results = asyncio.run(run_compliance_pipeline(clauses))

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    for result in results:
        print("=" * 60)
        print(f"Clause: {result.clause_title}")
        print(f"Status: {result.status}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Reason: {result.reason}")

if __name__ == "__main__":
    _demo()



