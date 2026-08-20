"""Synthetic, reproducible benchmark harness for the compliance pipeline.

Compares the two evaluation paths over the same clause set, with the
same deterministic logic, the same mocked external dependencies, and a
deterministic LLM stub that returns valid structured results with
configurable simulated latency:

  * sequential  -- one clause evaluated at a time
                   (``[await _evaluate_match(m) for m in matches]``)
  * concurrent  -- the pipeline's optimized concurrency architecture
                   (``asyncio.gather`` over ``_evaluate_match`` tasks, where
                   each clause's LLM call runs via ``asyncio.to_thread``)

The harness exercises the real orchestration layer
(``agent.compliance_pipeline._evaluate_match`` + ``run_compliance_pipeline``
concurrency) -- only the external LLM (``evaluate_compliance``) and the
CFR retrieval layer are mocked.

IMPORTANT -- these are SYNTHETIC numbers:

  * No internet, ATM, OpenAI, Ollama, GPU, or real LLM is used.
  * The stub sleeps a fixed, configurable latency per clause so that the
    concurrency benefit is measurable.
  * Results measure the orchestration/concurrency layer only, with a stub
    LLM. They are NOT production latency and must not be labeled as such.

The historical LIVE measurement (~12 min sequential -> ~4 min 10 s
optimized, ~65% reduction) is documented as a historical/unverifiable
claim. This harness does NOT reproduce it and does not claim to.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import agent.compliance_pipeline as pipeline
from agent.deterministic_rules import _extract_keywords
from agent.mcp_search import CfrMatch
from agent.models import Clause, ComplianceResult, EvidencePassage

# The 24-clause contract used by the existing workflow.
DEFAULT_CONTRACT = Path("contracts/sample_contract_multi.pdf")

DEFAULT_NUM_CLAUSES = 24
DEFAULT_LATENCY_S = 0.05  # simulated per-clause LLM latency


# ---------------------------------------------------------------------------
# Deterministic LLM stub
# ---------------------------------------------------------------------------


class DeterministicLLMStub:
    """Stand-in for ``evaluate_compliance``.

    Behaves like the real agent's contract (returns a valid
    ``ComplianceResult``, evidence without provenance -- the retrieval
    layer owns provenance downstream) but is fully deterministic and
    offline. ``latency`` simulates per-call model latency so the
    concurrency layer has something real to overlap.
    """

    def __init__(self, latency: float = DEFAULT_LATENCY_S) -> None:
        self.latency = latency
        self.calls = 0

    def evaluate(
        self,
        *,
        clause_title: str,
        clause_text: str,
        cfr_citation: str,
        cfr_text: str,
    ) -> ComplianceResult:
        self.calls += 1
        time.sleep(self.latency)

        digest = hashlib.sha256(f"{clause_title}|{clause_text}".encode()).hexdigest()
        status = "Compliant" if int(digest[0], 16) % 2 == 0 else "Non-Compliant"

        return ComplianceResult(
            clause_title=clause_title,
            clause_id=hashlib.sha256(f"{clause_title}|{clause_text}".encode()).hexdigest()[:8],
            status=status,
            confidence=0.90,
            reason=f"[stub] deterministic verdict for {clause_title!r}",
            evidence=[
                EvidencePassage(
                    title=40,
                    part="261",
                    section="10",
                    text_span=cfr_text[:120],
                    citation=cfr_citation,
                )
            ],
        )

    def __call__(self, **kwargs: Any) -> ComplianceResult:
        return self.evaluate(**kwargs)


# ---------------------------------------------------------------------------
# Clauses + mocked matches
# ---------------------------------------------------------------------------


def clauses_from_contract(pdf_path: Path | str = DEFAULT_CONTRACT) -> list[Clause]:
    """Extract clauses from the existing contract workflow (deterministic)."""
    from agent.contract_parser import ContractParser, split_into_clauses

    text = ContractParser(pdf_path).extract_text()
    return split_into_clauses(text)


def synthetic_clauses(n: int = DEFAULT_NUM_CLAUSES) -> list[Clause]:
    """Generate n distinct clauses for hermetic tests / offline runs.

    Each clause is a realistic environmental contract clause with
    distinctive keywords so results are deterministic and distinguishable.
    """
    topics = [
        "hazardous waste disposal",
        "air emissions control",
        "stormwater runoff management",
        "groundwater monitoring",
        "chemical storage containment",
        "wastewater discharge permitting",
        "hazardous materials labeling",
        "spill response preparedness",
        "used oil management",
        "universal waste handling",
        "tank integrity testing",
        "emissions reporting",
        "soil remediation",
        "asbestos abatement",
        "lead paint stabilization",
        "dust control measures",
        "noise emission limits",
        "wetland disturbance minimization",
        "endangered species protection",
        "waste minimization program",
        "recycling program compliance",
        "landfill disposal restrictions",
        "incineration controls",
        "toxic substance reduction",
    ]
    clauses: list[Clause] = []
    for i in range(n):
        topic = topics[i % len(topics)]
        title = f"SECTION {i + 1}. {topic.upper()}"
        text = (
            f"Contractor shall implement {topic} procedures and comply with "
            f"all applicable environmental protection requirements during the course of the Work."
        )
        clauses.append(Clause(title=title, text=text))
    return clauses


def _cfr_text_for(clause: Clause) -> str:
    """Deterministic synthetic regulation text for a clause.

    Engineered so the deterministic rules are inconclusive (topic overlap
    + mandatory "must" language, no section/part numbers), which forces the
    clause through the LLM-stub path -- the path the benchmark measures.
    """
    keywords = _extract_keywords(clause.text)
    if not keywords:
        keywords = ["waste"]
    return "Standards for " + " ".join(keywords[:8]) + " must be followed per EPA requirements."


def build_matches(clauses: list[Clause]) -> list[CfrMatch]:
    """Build CfrMatch objects with mocked retrieval.

    Retrieval is stubbed: citation + synthetic regulation text + a
    deterministic version-history payload, mirroring the real
    ``retrieve_for_clause`` output shape (which the pipeline consumes).
    """
    version_payload = {
        "title": 40,
        "versions": [
            {"issue_date": "2024-05-15"},
            {"issue_date": "2026-08-01"},
        ],
    }
    # Model version-specific retrieval: the effective version (2026-08-01)
    # is both the as-of date and the version label of the fetched text.
    effective = "2026-08-01"
    return [
        CfrMatch(
            clause=clause,
            citation="40 CFR 261.10",
            regulation_text=_cfr_text_for(clause),
            part="261",
            section="10",
            date=effective,
            version=effective,
            effective_version=effective,
            version_specific=True,
            version_payload=version_payload,
        )
        for clause in clauses
    ]


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


async def _run_sequential(matches: list[CfrMatch]) -> list[ComplianceResult]:
    results: list[ComplianceResult] = []
    for match in matches:
        results.append(await pipeline._evaluate_match(match))
    return results


async def _run_concurrent(matches: list[CfrMatch]) -> list[ComplianceResult]:
    tasks = [asyncio.create_task(pipeline._evaluate_match(match)) for match in matches]
    return await asyncio.gather(*tasks)


def install_stub(stub: Any) -> None:
    """Route the pipeline's LLM step to the deterministic stub.

    ``_evaluate_match`` resolves ``evaluate_compliance`` from the module
    global at call time (via ``asyncio.to_thread``), so reassigning the
    module attribute exercises the real orchestration/concurrency layer
    with a stub LLM. Accepts a ``DeterministicLLMStub`` or any callable
    with the ``evaluate_compliance(**kwargs)`` contract.
    """
    if hasattr(stub, "evaluate") and callable(stub.evaluate):
        pipeline.evaluate_compliance = stub.evaluate
    else:
        pipeline.evaluate_compliance = stub


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkReport:
    num_clauses: int
    sequential_s: float
    concurrent_s: float
    improvement_s: float
    improvement_pct: float
    concurrency: int
    cache_state: str
    model_mode: str
    network: str
    latency_s: float
    timestamp: str
    environment: str
    sequential_statuses: list[str]
    concurrent_statuses: list[str]


def _asyncio_executor_max_workers() -> int:
    """Report the asyncio default thread-pool bound the concurrent path uses."""
    return min(32, (os.cpu_count() or 1) + 4)


def run_benchmark(
    num_clauses: int = DEFAULT_NUM_CLAUSES,
    latency_s: float = DEFAULT_LATENCY_S,
    clauses: list[Clause] | None = None,
) -> BenchmarkReport:
    """Run sequential vs. concurrent evaluation and return a report."""
    if clauses is None:
        clauses = synthetic_clauses(num_clauses)
    clauses = clauses[:num_clauses]

    matches = build_matches(clauses)
    stub = DeterministicLLMStub(latency=latency_s)
    install_stub(stub)

    try:
        t0 = time.perf_counter()
        sequential = asyncio.run(_run_sequential(matches))
        sequential_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        concurrent = asyncio.run(_run_concurrent(matches))
        concurrent_s = time.perf_counter() - t0
    finally:
        # Restore the real agent function for subsequent runs.
        from agent.compliance_agent import evaluate_compliance

        pipeline.evaluate_compliance = evaluate_compliance

    improvement_s = sequential_s - concurrent_s
    improvement_pct = (improvement_s / sequential_s * 100) if sequential_s > 0 else 0.0

    return BenchmarkReport(
        num_clauses=len(clauses),
        sequential_s=sequential_s,
        concurrent_s=concurrent_s,
        improvement_s=improvement_s,
        improvement_pct=improvement_pct,
        concurrency=_asyncio_executor_max_workers(),
        cache_state="disabled (retrieval mocked; eCFR cache not exercised)",
        model_mode="deterministic stub",
        network="disabled",
        latency_s=latency_s,
        timestamp=datetime.now(UTC).isoformat(),
        environment=f"{platform.system()} {platform.release()} / Python {sys.version.split()[0]}",
        sequential_statuses=[r.status for r in sequential],
        concurrent_statuses=[r.status for r in concurrent],
    )


def format_report(report: BenchmarkReport) -> str:
    lines = [
        "=" * 72,
        "SYNTHETIC COMPLIANCE PIPELINE BENCHMARK",
        "=" * 72,
        f"  {report.num_clauses} clauses",
        f"  Sequential : {report.sequential_s:.3f}s",
        f"  Concurrent : {report.concurrent_s:.3f}s",
        f"  Improvement: {report.improvement_s:.3f}s ({report.improvement_pct:.1f}%)",
        f"  Concurrency: {report.concurrency} worker thread(s) (asyncio default executor)",
        f"  Latency    : {report.latency_s * 1000:.0f} ms simulated per-clause LLM latency",
        f"  Cache      : {report.cache_state}",
        f"  Model      : {report.model_mode}",
        f"  Network    : {report.network}",
        f"  Timestamp  : {report.timestamp}",
        f"  Environment: {report.environment}",
        "-" * 72,
        "  NOTE: SYNTHETIC numbers from a deterministic stub LLM. NOT real",
        "  production latency. The historical live measurement (~12 min ->",
        "  ~4 min 10 s, ~65% reduction) is documented separately and is not",
        "  reproduced by this harness.",
        "=" * 72,
    ]
    return "\n".join(lines)


def main() -> None:
    report = run_benchmark()
    print(format_report(report))


if __name__ == "__main__":
    main()