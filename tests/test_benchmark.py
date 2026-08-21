"""P2.2 benchmark regression tests.

Verify that the sequential and concurrent evaluation paths:
  * produce logically equivalent results for the same clauses,
  * do not lose clauses,
  * preserve deterministic ordering,
  * propagate per-clause failures without killing the run,
  * are repeatable (deterministic results across runs),
  * actually overlap the stubbed LLM latency (concurrent faster than
    sequential -- a relative comparison within one run, NOT an absolute
    machine-dependent threshold).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import agent.compliance_pipeline as pipeline
from agent.models import ComplianceResult
from benchmark.compliance_benchmark import (
    DeterministicLLMStub,
    build_matches,
    install_stub,
    synthetic_clauses,
)

N = 12  # smaller set keeps the test fast; scale is irrelevant to correctness
LATENCY = 0.02


def _statuses(results: list[ComplianceResult]) -> list[str]:
    return [r.status for r in results]


class TestBenchmarkEquivalence:
    @pytest.mark.asyncio
    async def test_sequential_and_concurrent_are_equivalent(self) -> None:
        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        install_stub(DeterministicLLMStub(latency=LATENCY))
        try:
            seq = await _run_sequential(matches)
            conc = await _run_concurrent(matches)
        finally:
            _restore_real_agent()

        assert len(seq) == N
        assert len(conc) == N
        assert _statuses(seq) == _statuses(conc)
        assert [r.clause_id for r in seq] == [r.clause_id for r in conc]
        assert all(r.verification_status == "verified" for r in conc)

    @pytest.mark.asyncio
    async def test_no_clauses_lost_and_ordering_deterministic(self) -> None:
        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        install_stub(DeterministicLLMStub(latency=LATENCY))
        try:
            seq = await _run_sequential(matches)
            conc = await _run_concurrent(matches)
        finally:
            _restore_real_agent()

        # One result per clause, in input order, in BOTH paths.
        expected_ids = [m.clause.clause_id for m in matches]
        assert [r.clause_id for r in seq] == expected_ids
        assert [r.clause_id for r in conc] == expected_ids

    @pytest.mark.asyncio
    async def test_results_are_repeatable(self) -> None:
        """Deterministic stub -> identical statuses across runs (cache/consistency)."""
        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        install_stub(DeterministicLLMStub(latency=LATENCY))
        try:
            first = await _run_concurrent(matches)
            second = await _run_concurrent(matches)
        finally:
            _restore_real_agent()

        assert _statuses(first) == _statuses(second)


class TestBenchmarkFailurePropagation:
    @pytest.mark.asyncio
    async def test_retrieval_failure_becomes_needs_review_only_for_that_clause(self) -> None:
        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        matches[3].error = "No CFR search results found"
        matches[3].citation = None
        matches[3].regulation_text = None

        install_stub(DeterministicLLMStub(latency=LATENCY))
        try:
            seq = await _run_sequential(matches)
            conc = await _run_concurrent(matches)
        finally:
            _restore_real_agent()

        for results in (seq, conc):
            assert len(results) == N  # nothing dropped
            assert results[3].status == "Needs Review"
            assert "No CFR search results" in results[3].review_reason
            # Every other clause still went through the LLM-stub path.
            assert all(r.verification_status == "verified" for r in results[:3])
            assert all(r.verification_status == "verified" for r in results[4:])

    @pytest.mark.asyncio
    async def test_llm_stub_failure_is_isolated_per_clause(self) -> None:
        """A failing LLM call for one clause must not kill the others."""

        def _flaky(**kwargs: Any) -> ComplianceResult:
            if "SECTION 5" in kwargs["clause_title"]:
                raise RuntimeError("simulated stub outage")
            return DeterministicLLMStub(latency=LATENCY).evaluate(**kwargs)

        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        install_stub(_flaky)
        try:
            conc = await _run_concurrent(matches)
        finally:
            _restore_real_agent()

        assert len(conc) == N
        failed = [r for r in conc if r.status == "Needs Review"]
        assert len(failed) == 1
        assert "SECTION 5" in failed[0].clause_title
        assert "Compliance evaluation failed" in failed[0].review_reason
        assert sum(1 for r in conc if r.verification_status == "verified") == N - 1


class TestBenchmarkConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_overlaps_stub_latency(self) -> None:
        """Relative within-run comparison: concurrent must beat sequential.

        With N clauses each sleeping LATENCY in the stub, sequential runs in
        roughly N * LATENCY while concurrent overlaps them across worker
        threads -- a large, stable gap. No absolute runtime thresholds.
        """
        clauses = synthetic_clauses(N)
        matches = build_matches(clauses)
        install_stub(DeterministicLLMStub(latency=LATENCY))
        try:
            t0 = __import__("time").perf_counter()
            await _run_sequential(matches)
            sequential_s = __import__("time").perf_counter() - t0

            t0 = __import__("time").perf_counter()
            await _run_concurrent(matches)
            concurrent_s = __import__("time").perf_counter() - t0
        finally:
            _restore_real_agent()

        # Sequential approximates N * LATENCY (0.24s); concurrent is far less.
        assert sequential_s >= N * LATENCY * 0.7
        assert concurrent_s < sequential_s


class TestStub:
    def test_stub_is_deterministic(self) -> None:
        stub = DeterministicLLMStub(latency=0)
        a = stub.evaluate(
            clause_title="T", clause_text="body", cfr_citation="40 CFR 261.10", cfr_text="x"
        )
        b = stub.evaluate(
            clause_title="T", clause_text="body", cfr_citation="40 CFR 261.10", cfr_text="x"
        )
        assert a.status == b.status
        assert a.confidence == b.confidence
        assert a.clause_id == b.clause_id
        assert isinstance(a, ComplianceResult)
        assert a.evidence and a.evidence[0].citation == "40 CFR 261.10"

    def test_stub_returns_no_provenance(self) -> None:
        """The stub stands in for the LLM, so it must not fabricate provenance."""
        stub = DeterministicLLMStub(latency=0)
        r = stub.evaluate(
            clause_title="T", clause_text="body", cfr_citation="40 CFR 261.10", cfr_text="x"
        )
        assert r.evidence[0].date == ""
        assert r.evidence[0].source == ""

    def test_real_contract_extracts_24_clauses(self) -> None:
        from benchmark.compliance_benchmark import clauses_from_contract

        clauses = clauses_from_contract()
        assert len(clauses) == 24, f"expected 24 clauses, got {len(clauses)}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_sequential(matches) -> list[ComplianceResult]:
    results: list[ComplianceResult] = []
    for match in matches:
        results.append(await pipeline._evaluate_match(match))
    return results


async def _run_concurrent(matches) -> list[ComplianceResult]:
    tasks = [asyncio.create_task(pipeline._evaluate_match(match)) for match in matches]
    return await asyncio.gather(*tasks)


def _restore_real_agent() -> None:
    from agent.compliance_agent import evaluate_compliance

    pipeline.evaluate_compliance = evaluate_compliance