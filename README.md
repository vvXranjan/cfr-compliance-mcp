# CFR Compliance Checker

AI-assisted compliance checking system that takes a contract PDF, extracts its clauses, retrieves the relevant CFR (Code of Federal Regulations) sections through a custom eCFR MCP server, and evaluates each clause's compliance using an LLM agent.

## Overview

This project has two independent layers:

- **MCP Server** (`src/cfr_compliance_mcp/`) — wraps the official [eCFR REST API](https://www.ecfr.gov/) and exposes it as a set of [MCP](https://modelcontextprotocol.io/) tools (search, section/part/title retrieval, version history, agency lookup).
- **Agent Pipeline** (`agent/`) — parses a contract PDF into clauses, builds an optimized CFR search query for each clause, retrieves the matching regulation via the MCP server, and runs an LLM-based compliance evaluation.

The agent layer never talks to eCFR directly — all retrieval goes through the MCP server as an MCP client.

```
Contract PDF
     │
     ▼
Contract Parser ──▶ Clauses
     │
     ▼
Query Optimizer ──▶ Optimized Query
     │
     ▼
MCP Search (search_regulations → retrieve_section) ──▶ MCP Server ──▶ eCFR REST API
     │
     ▼
Compliance Agent (Agno + Ollama / Llama 3.1)
     │
     ▼
Compliance Result (status, confidence, reason)
```

## Features

- **Custom eCFR MCP server** built on FastMCP, with async HTTP client, XML parsing, caching, and typed request/response models.
- **Deterministic query optimizer** — detects a clause's regulatory domain (environmental, safety, employment, labor, procurement) and predicts likely CFR titles before retrieval, without an LLM call.
- **Compliance agent** built with [Agno](https://github.com/agno-agi/agno) running on local [Ollama](https://ollama.com/) (Llama 3.1), returning a validated Pydantic `ComplianceResult` (status, confidence, reason).
- **End-to-end pipeline** that runs a contract PDF through extraction, retrieval, and compliance evaluation with no manual intervention between steps.

## MCP Tools

| Tool | Purpose |
|---|---|
| `search_regulations` | Search eCFR content for a query, returning candidate matches |
| `retrieve_section` | Retrieve the full text of a specific CFR section |
| `retrieve_part` | Retrieve a specific CFR part |
| `retrieve_title` | Retrieve a specific CFR title |
| `get_title_structure` | Retrieve the hierarchical structure of a CFR title |
| `get_version_history` | Retrieve version/revision history for CFR content |
| `list_agencies` | Retrieve the list of agencies with CFR content |

## Project Structure

```
agent/
├── contract_parser.py          # Extracts text from a contract PDF, splits into clauses
├── cfr_query_optimizer.py      # Deterministic domain detection + query optimization
├── mcp_search.py                # MCP client: search_regulations + retrieve_section
├── compliance_agent.py          # Agno/Ollama compliance evaluation, structured output
├── compliance_pipeline.py       # Orchestrates the full clause-by-clause workflow
└── models.py                    # Shared data models (clauses, ComplianceResult, etc.)

src/cfr_compliance_mcp/
├── clients/                     # Async HTTP client for the eCFR API
├── parsing/                     # XML parsing for eCFR API responses
├── tools/                       # MCP tool implementations
└── server.py                    # FastMCP server setup, tool registration, caching

contracts/
└── sample_contract.pdf          # Sample contract for manual/end-to-end testing

tests/
├── test_optimizer_manual.py
└── test_compliance_agent_manual.py
```

## Tech Stack

- Python 3.13
- [FastMCP](https://gofastmcp.com/) — MCP server framework
- [Agno](https://github.com/agno-agi/agno) — agent framework with structured (Pydantic) output
- [Ollama](https://ollama.com/) (Llama 3.1) — local LLM runtime
- Pydantic v2 — validation and typed models
- [uv](https://docs.astral.sh/uv/) — dependency management and running project modules

## Setup

1. Install dependencies:
   ```bash
   uv sync
   ```
2. Install and run [Ollama](https://ollama.com/) locally, and pull the model:
   ```bash
   ollama pull llama3.1
   ```
3. Copy `.env.example` to `.env` and fill in any required configuration.

## Usage

Run the full pipeline against the sample contract:

```bash
uv run python -m agent.compliance_pipeline
```

Run the contract parser / clause extraction step on its own:

```bash
uv run python -m agent.workflow
```

Run the manual test suites:

```bash
uv run python -m tests.test_optimizer_manual
uv run python -m tests.test_compliance_agent_manual
```

## Sample Result

For the clause `SECTION 2. HAZARDOUS MATERIALS HANDLING`, the pipeline correctly matched **40 CFR 262.84** (hazardous waste generator standards) and returned:

| Field | Value |
|---|---|
| Status | Compliant |
| Confidence | 0.80 |
| Reason | The retrieved regulation matched the contract clause and the compliance agent produced a structured compliance assessment. |

## Current Capabilities

- Parse a contract PDF and extract its text
- Split contract text into individual, structured clauses
- Deterministically optimize a CFR search query per clause, based on detected regulatory domain
- Search official eCFR content via the custom MCP server
- Retrieve the matched CFR section's full text
- Evaluate clause-vs-regulation compliance using an LLM agent with structured, validated output
- Produce a per-clause compliance result (status, confidence, reason) tied back to the correct clause title

## Known Limitations

- No automated CI test suite — testing is currently via manual scripts only
- No multi-clause batch reporting (single clause per run)
- No PDF/Markdown compliance report export
- No ranking mechanism when `search_regulations` returns multiple plausible candidates — current behavior is best-result selection
- No support yet for clauses governed by more than one CFR section

## Roadmap

- [ ] Multi-clause contract reports (aggregate results across all clauses)
- [ ] PDF/Markdown compliance report export
- [ ] Improved retrieval ranking for multiple plausible candidates
- [ ] Support for multiple CFR references per clause
- [ ] Automated evaluation metrics against a labeled test set
- [ ] Automated unit/integration test suite runnable in CI
- [ ] REST API / UI for the pipeline
- [ ] Normalize LLM confidence values (e.g., convert `95` → `0.95` before validation)
- [ ] Detect clauses with no applicable CFR regulation and skip unnecessary retrieval
- [ ] Retrieval explainability (optimized query, predicted CFR title, matched keywords, retrieval score)

## Branching

| Branch | Purpose |
|---|---|
| `main` | Stable, production/reference branch |
| `development` | Active integration branch |
| `improve-retrieval-ranking` | Feature branch for retrieval optimization and compliance-agent work |

## Author

Vaibhav Vikas Ranjan
