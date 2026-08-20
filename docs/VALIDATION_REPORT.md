# Validation Report

Current validation state of the cfr-compliance-mcp repository after P0–P2
completion and P3 production polish.

## Summary

- **Tests:** 139 passed, 0 failed, 0 skipped
  - 130 offline tests (run in seconds, no network required)
  - 9 live LLM tests (`tests/test_live_llm_integration.py`) — require
    `ATM_API_KEY` and network access to the ATM endpoint; they pass when
    credentials/network are available and **skip** otherwise
- **Ruff:** `ruff check .` — All checks passed (0 errors)
- **Lint/whitespace:** `git diff --check` clean
- **Docker:** build and runtime verified (see below)
- **Benchmark:** synthetic deterministic benchmark measured (see below)

## Test breakdown

| Area | File | Tests |
|------|------|-------|
| REST API | `tests/test_api_integration.py` | 23 |
| Benchmark harness | `tests/test_benchmark.py` | 9 |
| Deterministic rules | `tests/test_deterministic_rules.py` | 18 |
| Effective-version retrieval | `tests/test_effective_version_retrieval.py` | 11 |
| Evidence provenance | `tests/test_evidence_provenance.py` | 5 |
| Live LLM integration (network/credential-gated) | `tests/test_live_llm_integration.py` | 9 |
| Pipeline security + HITL | `tests/test_pipeline_security_hitl.py` | 13 |
| Report persistence | `tests/test_reporting.py` | 15 |
| Version-aware retrieval | `tests/test_version_aware_retrieval.py` | 9 |
| Regression — citation URL | `tests/regression/test_citation_browse_url.py` | 6 |
| Regression — verification report | `tests/regression/test_verification_report.py` | 3 |
| **Total** | | **139** |

## Benchmark results

### Synthetic deterministic benchmark (current, reproducible)

Measured with the offline harness in `benchmark/compliance_benchmark.py`
(deterministic LLM stub, network disabled):

```text
24 clauses
Sequential: ~1.348s
Concurrent: ~0.112s
Improvement: ~91.7%
Concurrency: 12
Stub latency: 50 ms/clause
Network: disabled
```

This validates concurrency behavior and regression characteristics only. It
does **not** represent real-world eCFR/network/LLM production latency.

### Historical live benchmark (NOT reproduced)

The project previously measured approximately:

```text
24 clauses
Sequential: ~12 minutes
Optimized: ~4 minutes 10 seconds
Reduction: ~65%
```

This is a historical measurement from a previous live run and was **not**
reproduced during the current deterministic validation. The 65% and 91.7%
figures measure different things and must never be presented as equivalent.

## Docker verification

- **Build:** `docker build -t cfr-compliance-mcp .` succeeded (Python 3.13
  slim base; dependencies installed from the committed `uv.lock` via `uv` for
  reproducibility; non-root `appuser`).
- **Runtime:** container started and served requests without any credentials:
  - `GET /health` → `{"status":"healthy","service":"cfr-compliance-mcp","llm_available":"False"}`
  - `POST /evaluate-clause` → structured NEEDS_REVIEW response with a full
    `review_audit` (expected: no `cfr_text` supplied, so no evidence-grounded
    verdict).
- **Security:** image contains no `.env`, no contract fixtures, and runs as
  non-root.
- Live inference / live eCFR retrieval inside the container was not exercised
  (no external credentials supplied to the test run).

## Known limitations

- AI-assisted compliance analysis is not autonomous legal authorization.
- NEEDS_REVIEW findings require human judgment.
- Historical regulation text depends on available eCFR/version support.
- Live LLM tests depend on external ATM availability and credentials.
- Synthetic benchmarks do not represent real external network/model latency.
- Report persistence is filesystem-based, not multi-node distributed
  persistence.
- Jaeger trace delivery requires a live Jaeger agent; the application degrades
  gracefully (logs a warning) when none is present.
- The eCFR cache is process-local memory; `CACHE_BACKEND=redis` is declared in
  configuration but intentionally not implemented.