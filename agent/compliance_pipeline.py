# """agent/compliance_pipeline.py

# Orchestration layer: Clause -> CFR retrieval -> compliance evaluation ->
# ComplianceResult, for every clause in a contract.

# This module adds no new retrieval or optimization logic of its own --
# it is a thin orchestrator over two already-complete stages:

#     1. `agent.mcp_search.retrieve_for_clauses()` -- runs the full
#        `build_search_query()` -> `optimize_clause()` ->
#        `search_regulations()` -> `retrieve_section()`/`retrieve_part()`
#        pipeline for every clause against one shared MCP session, and
#        returns a `CfrMatch` per clause. See that module for the
#        retrieval pipeline itself.
#     2. `agent.compliance_agent.evaluate_compliance()` -- runs the Agno/
#        Ollama compliance judgment for one clause + retrieved CFR text,
#        returning a `ComplianceResult`.

# Deliberately calls `retrieve_for_clauses()` rather than re-invoking
# `build_search_query()` / `optimize_clause()` / the individual MCP tool
# calls directly: those steps are already fully composed inside
# `retrieve_for_clause()`, and reimplementing them here would duplicate
# that logic instead of reusing it.

# Deterministic except for one step: `evaluate_compliance()`'s LLM call.
# Retrieval (`retrieve_for_clauses`) and the query optimization it runs
# internally (`build_search_query`, `optimize_clause`) are both fully
# deterministic given the same contract text -- see their own docstrings.
# """

# from __future__ import annotations

# import asyncio
# import logging

# from .compliance_agent import evaluate_compliance
# from .mcp_search import CfrMatch, retrieve_for_clauses
# from .models import Clause, ComplianceResult

# logger = logging.getLogger(__name__)

# __all__ = ["run_compliance_pipeline"]


# def _needs_review_result(clause_title: str, reason: str) -> ComplianceResult:
#     """Build the standard fallback result for a clause that could not be
#     evaluated -- confidence 0.0, status "Needs Review". Used whenever
#     CFR retrieval or compliance evaluation fails for a clause, so a
#     single failure downgrades that clause's own result instead of
#     crashing the run or dropping the clause from the final report.
#     """
#     return ComplianceResult(
#         clause_title=clause_title,
#         status="Needs Review",
#         confidence=0.0,
#         reason=reason,
#     )


# async def _evaluate_match(match: CfrMatch) -> ComplianceResult:
#     """Turn one `CfrMatch` into a `ComplianceResult`.

#     If retrieval already failed for this clause (`match.error` set, or
#     `citation`/`regulation_text` missing), skips the LLM call entirely
#     and returns a "Needs Review" result explaining the retrieval
#     failure -- there is nothing for the compliance agent to evaluate
#     without retrieved CFR text.

#     Otherwise calls `evaluate_compliance()` -- a synchronous, blocking
#     Agno/Ollama call -- via `asyncio.to_thread` so it doesn't block the
#     event loop the rest of this pipeline runs on. Any exception raised
#     during evaluation is caught here and downgraded to a "Needs Review"
#     result rather than propagating and stopping the rest of the batch.
#     """
#     clause = match.clause

#     if match.error is not None or match.citation is None or match.regulation_text is None:
#         reason = match.error or "CFR retrieval returned no citation/regulation text"
#         logger.warning("CFR retrieval unusable for clause %r: %s", clause.title, reason)
#         return _needs_review_result(
#             clause.title,
#             reason=f"CFR retrieval failed, could not evaluate compliance: {reason}",
#         )

#     logger.info("Evaluating compliance for clause %r against %s", clause.title, match.citation)

#     try:
#         result = await asyncio.to_thread(
#             evaluate_compliance,
#             clause_title=clause.title,
#             clause_text=clause.text,
#             cfr_citation=match.citation,
#             cfr_text=match.regulation_text,
#         )
#     except Exception as exc:  # noqa: BLE001 -- one clause's LLM failure must not kill the run
#         logger.exception("Compliance evaluation failed for clause %r", clause.title)
#         return _needs_review_result(
#             clause.title, reason=f"Compliance evaluation failed: {exc}"
#         )

#     logger.info(
#         "Clause %r evaluated: status=%s confidence=%.2f",
#         clause.title, result.status, result.confidence,
#     )
#     return result


# async def run_compliance_pipeline(clauses: list[Clause]) -> list[ComplianceResult]:
#     """Run the full Clause -> CFR retrieval -> compliance evaluation
#     pipeline for every clause in `clauses`.

#     Always returns exactly one `ComplianceResult` per input clause, in
#     the same order. A retrieval or evaluation failure for one clause
#     downgrades that clause's own result to "Needs Review" (confidence
#     0.0) rather than raising or dropping it from the output. A total
#     retrieval failure (e.g. the MCP server connection itself fails)
#     still returns one "Needs Review" result per clause rather than
#     raising, for the same reason.

#     Deterministic except for `evaluate_compliance()`'s LLM call -- see
#     module docstring.

#     Args:
#         clauses: contract clauses to evaluate, e.g. from
#             `contract_parser.split_into_clauses()`.

#     Returns:
#         One `ComplianceResult` per input clause, in the same order as
#         `clauses`. Empty list in, empty list out.
#     """
#     if not clauses:
#         logger.info("run_compliance_pipeline called with no clauses; returning empty result list")
#         return []

#     logger.info("Starting compliance pipeline for %d clause(s)", len(clauses))

#     try:
#         matches = await retrieve_for_clauses(clauses)
#     except Exception as exc:  # noqa: BLE001 -- a connection-level failure must not crash the whole run
#         logger.exception("CFR retrieval failed for the entire batch (MCP connection error)")
#         return [
#             _needs_review_result(
#                 clause.title, reason=f"CFR retrieval failed for the entire batch: {exc}"
#             )
#             for clause in clauses
#         ]

#     results = [await _evaluate_match(match) for match in matches]

#     compliant = sum(1 for r in results if r.status == "Compliant")
#     non_compliant = sum(1 for r in results if r.status == "Non-Compliant")
#     needs_review = sum(1 for r in results if r.status == "Needs Review")
#     logger.info(
#         "Compliance pipeline complete: %d compliant, %d non-compliant, %d needs review (of %d total)",
#         compliant, non_compliant, needs_review, len(results),
#     )

#     return results


# def _demo() -> None:
#     """Run the pipeline on one sample Clause and print the result.

#     Uses a synthetic clause so this demo has no dependency on a real
#     contract PDF being present on disk -- exercises the full
#     retrieval + evaluation pipeline end to end against the live MCP
#     server and Ollama.
#     """
#     logging.basicConfig(level=logging.INFO)

#     sample_clause = Clause(
#         title="SECTION 2. HAZARDOUS MATERIALS HANDLING",
#         text=(
#             "Contractor shall identify, characterize, and segregate any hazardous "
#             "waste encountered or generated during the Work. Prior to offsite "
#             "transport, Contractor shall determine whether such waste meets the "
#             "characteristics of ignitability, corrosivity, reactivity, or toxicity, "
#             "and shall obtain an EPA identification number before any hazardous "
#             "waste is shipped from the site."
#         ),
#     )

#     results = asyncio.run(run_compliance_pipeline([sample_clause]))

#     for result in results:
#         print("=" * 60)
#         print(f"Clause: {result.clause_title}")
#         print(f"Status: {result.status}")
#         print(f"Confidence: {result.confidence:.2f}")
#         print(f"Reason: {result.reason}")


# if __name__ == "__main__":
#     _demo()


"""agent/compliance_pipeline.py

Orchestration layer: Clause -> CFR retrieval -> compliance evaluation ->
ComplianceResult, for every clause in a contract.

This module adds no new retrieval or optimization logic of its own --
it is a thin orchestrator over two already-complete stages:

    1. `agent.mcp_search.retrieve_for_clauses()` -- runs the full
       `build_search_query()` -> `optimize_clause()` ->
       `search_regulations()` -> `retrieve_section()`/`retrieve_part()`
       pipeline for every clause against one shared MCP session, and
       returns a `CfrMatch` per clause. See that module for the
       retrieval pipeline itself.
    2. `agent.compliance_agent.evaluate_compliance()` -- runs the Agno/
       Ollama compliance judgment for one clause + retrieved CFR text,
       returning a `ComplianceResult`.

Deliberately calls `retrieve_for_clauses()` rather than re-invoking
`build_search_query()` / `optimize_clause()` / the individual MCP tool
calls directly: those steps are already fully composed inside
`retrieve_for_clause()`, and reimplementing them here would duplicate
that logic instead of reusing it.

Deterministic except for one step: `evaluate_compliance()`'s LLM call.
Retrieval (`retrieve_for_clauses`) and the query optimization it runs
internally (`build_search_query`, `optimize_clause`) are both fully
deterministic given the same contract text -- see their own docstrings.
"""

from __future__ import annotations

import asyncio
import logging

from .compliance_agent import evaluate_compliance
from .mcp_search import CfrMatch, retrieve_for_clauses
from .models import Clause, ComplianceResult

logger = logging.getLogger(__name__)

__all__ = ["run_compliance_pipeline"]


def _needs_review_result(clause_title: str, reason: str) -> ComplianceResult:
    """Build the standard fallback result for a clause that could not be
    evaluated -- confidence 0.0, status "Needs Review". Used whenever
    CFR retrieval or compliance evaluation fails for a clause, so a
    single failure downgrades that clause's own result instead of
    crashing the run or dropping the clause from the final report.
    """
    return ComplianceResult(
        clause_title=clause_title,
        status="Needs Review",
        confidence=0.0,
        reason=reason,
    )


async def _evaluate_match(match: CfrMatch) -> ComplianceResult:
    """Turn one `CfrMatch` into a `ComplianceResult`.

    If retrieval already failed for this clause (`match.error` set, or
    `citation`/`regulation_text` missing), skips the LLM call entirely
    and returns a "Needs Review" result explaining the retrieval
    failure -- there is nothing for the compliance agent to evaluate
    without retrieved CFR text.

    Otherwise calls `evaluate_compliance()` -- a synchronous, blocking
    Agno/Ollama call -- via `asyncio.to_thread` so it doesn't block the
    event loop the rest of this pipeline runs on. Any exception raised
    during evaluation is caught here and downgraded to a "Needs Review"
    result rather than propagating and stopping the rest of the batch.
    """
    clause = match.clause

    if match.error is not None or match.citation is None or match.regulation_text is None:
        reason = match.error or "CFR retrieval returned no citation/regulation text"
        logger.warning("CFR retrieval unusable for clause %r: %s", clause.title, reason)
        return _needs_review_result(
            clause.title,
            reason=f"CFR retrieval failed, could not evaluate compliance: {reason}",
        )

    logger.info("Evaluating compliance for clause %r against %s", clause.title, match.citation)

    try:
        result = await asyncio.to_thread(
            evaluate_compliance,
            clause_title=clause.title,
            clause_text=clause.text,
            cfr_citation=match.citation,
            cfr_text=match.regulation_text,
        )
    except Exception as exc:  # noqa: BLE001 -- one clause's LLM failure must not kill the run
        logger.exception("Compliance evaluation failed for clause %r", clause.title)
        return _needs_review_result(
            clause.title, reason=f"Compliance evaluation failed: {exc}"
        )

    logger.info(
        "Clause %r evaluated: status=%s confidence=%.2f",
        clause.title, result.status, result.confidence,
    )
    return result


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
    except Exception as exc:  # noqa: BLE001 -- a connection-level failure must not crash the whole run
        logger.exception("CFR retrieval failed for the entire batch (MCP connection error)")
        return [
            _needs_review_result(
                clause.title, reason=f"CFR retrieval failed for the entire batch: {exc}"
            )
            for clause in clauses
        ]

    results = [await _evaluate_match(match) for match in matches]

    compliant = sum(1 for r in results if r.status == "Compliant")
    non_compliant = sum(1 for r in results if r.status == "Non-Compliant")
    needs_review = sum(1 for r in results if r.status == "Needs Review")
    logger.info(
        "Compliance pipeline complete: %d compliant, %d non-compliant, %d needs review (of %d total)",
        compliant, non_compliant, needs_review, len(results),
    )

    return results


def _demo() -> None:
    """Run the pipeline on one sample Clause and print the result.

    Uses a synthetic clause so this demo has no dependency on a real
    contract PDF being present on disk -- exercises the full
    retrieval + evaluation pipeline end to end against the live MCP
    server and Ollama.
    """
    logging.basicConfig(level=logging.INFO)

    sample_clause = Clause(
        title="SECTION 2. HAZARDOUS MATERIALS HANDLING",
        text=(
            "Contractor shall identify, characterize, and segregate any hazardous "
            "waste encountered or generated during the Work. Prior to offsite "
            "transport, Contractor shall determine whether such waste meets the "
            "characteristics of ignitability, corrosivity, reactivity, or toxicity, "
            "and shall obtain an EPA identification number before any hazardous "
            "waste is shipped from the site."
        ),
    )

    results = asyncio.run(run_compliance_pipeline([sample_clause]))

    for result in results:
        print("=" * 60)
        print(f"Clause: {result.clause_title}")
        print(f"Status: {result.status}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Reason: {result.reason}")


if __name__ == "__main__":
    _demo()