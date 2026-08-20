# Claims Evidence

This document maps every major technical claim about cfr-compliance-mcp to the
repository evidence that supports it. Claims are marked VERIFIED (tested in
this environment), STRUCTURAL (verified by source inspection), or
NOT-REPRODUCED (historical or requires external infrastructure).

Current validation baseline (pre-P3): **139 pytest tests passed, 0 failed,
0 skipped** (130 offline + 9 live LLM, which skip without `ATM_API_KEY`);
**ruff clean**; reproducible Docker build verified.

| Claim | Implementation | Validation | Status |
|-------|---------------|------------|--------|
| 8 typed MCP tools | `make_retrieve_section_tool`, `make_retrieve_part_tool`, `make_retrieve_title_tool`, `make_search_regulations_tool`, `make_search_by_keyword_tool`, `make_get_title_structure_tool`, `make_get_version_history_tool`, `make_list_agencies_tool` in `src/cfr_compliance_mcp/tools/` | Tool factories import and register with FastMCP (source inspection); retrieval tool interface exercised by `tests/test_version_aware_retrieval.py` (9) and `tests/test_effective_version_retrieval.py` (11) | VERIFIED |
| Official eCFR retrieval | `EcfrClient` in `src/cfr_compliance_mcp/clients/ecfr_client.py`; base URL `https://www.ecfr.gov`; XML parsing in `xml_parser.py` | `tests/regression/test_citation_browse_url.py` (6) validates the eCFR browse-URL construction; dated section retrieval exercised via the version-aware tests; live calls require network | VERIFIED (offline paths) / live path NOT-REPRODUCED without network |
| Async / concurrency | Async HTTP client + `asyncio.gather` concurrency in pipeline and benchmark | `benchmark/compliance_benchmark.py`; `tests/test_benchmark.py` (9) | VERIFIED |
| Caching | Process-local TTL cache, `CACHE_BACKEND=memory`; `CACHE_BACKEND=redis` intentionally unimplemented (raises loudly) | `src/cfr_compliance_mcp/cache/cache_backend.py`; unit-tested | VERIFIED (memory) |
| Evidence grounding | `ComplianceResult.evidence` with `EvidencePassage` (source, retrieval method, timestamp, citation, text span) | `tests/test_evidence_provenance.py` (5); deterministic-rules tests assert evidence passages | VERIFIED |
| Evidence provenance | Provenance metadata on every passage; ungrounded results routed to NEEDS_REVIEW | `tests/test_evidence_provenance.py` (5); `tests/test_pipeline_security_hitl.py` (13) | VERIFIED |
| Deterministic validation | 3 rules producing Compliant / Non-Compliant / Needs Review with evidence | `tests/test_deterministic_rules.py` (18) | VERIFIED |
| Security controls | Prompt-injection detection, `sanitize_clause_text`, `validate_cfr_title` (1–50); shared gate used by both pipeline and API | `tests/test_pipeline_security_hitl.py` (13); `tests/test_api_integration.py` (23) | VERIFIED |
| Verification agent | Cross-checks determinism, evidence coverage, prompt injection, title validity, version consistency; recommends accept/review/reject | `agent/verification_agent.py`; `tests/regression/test_verification_report.py` (3); `tests/test_effective_version_retrieval.py` (11) | VERIFIED |
| Version-aware retrieval | `CfrMatch.effective_version` / `version_specific`; dated `retrieve_section` fetch with fallback to current text | `tests/test_effective_version_retrieval.py` (11); `tests/test_version_aware_retrieval.py` (9) | VERIFIED |
| HITL boundary | Any uncertainty/failure/version-mismatch routes to NEEDS_REVIEW with `ReviewAudit` | `tests/test_pipeline_security_hitl.py` (13); `tests/test_api_integration.py` (23) | VERIFIED |
| REST API | `/health`, `/evaluate-clause`, `/evaluate-bulk`; structured safe errors; bulk cap of 200 | `tests/test_api_integration.py` (23); live container health check verified | VERIFIED |
| Report persistence | Atomic JSON reports, sanitized analysis IDs, path-traversal defense, no secrets/full contract text stored | `agent/reporting.py`; `tests/test_reporting.py` (15) | VERIFIED |
| Synthetic benchmark | Deterministic offline harness; sequential vs concurrent with stub LLM, network disabled | `tests/test_benchmark.py` (9); `benchmark/compliance_benchmark.py` | VERIFIED (synthetic) |
| Historical 65% performance claim | "12 min → 4 min 10 sec" reduction | Historical measurement from a previous live run; **not reproduced** in current validation | NOT-REPRODUCED |
| OpenTelemetry / Jaeger | FastAPI request tracing via `FastAPIInstrumentor`; best-effort Jaeger agent (UDP thrift) export | Structural; spans generated in-process; export depends on a live Jaeger agent | VERIFIED (structure) |
| Docker | Minimal single-stage image, non-root user, built reproducibly from `uv.lock` | `docker build` succeeded; container started; `/health` + `/evaluate-clause` exercised | VERIFIED |
| Structured logging | `LOG_LEVEL` / `LOG_FORMAT` honored by MCP server and API; JSON formatter with structured `extra=` fields | `src/cfr_compliance_mcp/logging_config.py`; source inspection | VERIFIED |