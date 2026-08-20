"""
Agno-based CFR compliance evaluation agent.

This module is responsible ONLY for the final LLM-driven compliance
evaluation step of the pipeline:

    Contract Parser
          |
    build_search_query()
          |
    optimize_clause()
          |
    retrieve_for_clauses()  (MCP)
          |
    ComplianceAgent            <-- this module
          |
    ComplianceResult

Given a contract clause and a retrieved CFR regulation, it asks the LLM
to decide whether the clause is Compliant, Non-Compliant, or Needs
Review, and returns a validated `ComplianceResult`.

This module does NOT perform CFR search, query optimization, MCP tool
calls, contract parsing, or pipeline orchestration -- those live
elsewhere.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agno.agent import Agent
from agno.models.openai import OpenAIChat

from .models import ComplianceResult, _clause_id_from_text

# System instructions given to the compliance-review agent.
#
# Kept concise and deterministic on purpose: the agent's only job is to
# classify a clause against a regulation and explain why. It must never
# invent or rewrite the clause title -- the caller already knows it and
# will overwrite whatever the LLM returns.
_COMPLIANCE_REVIEWER_INSTRUCTIONS = """\
You are a federal contract compliance reviewer.

Compare a contract clause against a CFR regulation and determine
exactly one compliance status:

1. Compliant -- the clause clearly satisfies the CFR requirements.
2. Non-Compliant -- the clause conflicts with the CFR, or omits a
   mandatory obligation.
3. Needs Review -- the clause is related to the CFR, but there is not
   enough evidence to confidently decide.

Evaluation process:
1. Identify the obligations imposed by the CFR regulation.
2. Identify the obligations stated in the contract clause.
3. Compare the two sets of obligations.
4. Note any missing or conflicting requirements.
5. Briefly explain the compliance risk, if any.

Evidence grounding rules (MANDATORY):
- Every `evidence` entry MUST reference ONLY the provided CFR
  regulation, never the contract clause.
- `title` is the integer CFR title number (e.g. 40).
- `part` and `section` are the CFR part/section numbers (e.g. "257" and
  "257.3").
- `text_span` MUST be a verbatim quote from the provided regulation
  text.
- `citation` is the human-readable citation, e.g. "40 CFR 257.3".
- `date` is the regulation date only if it appears in the provided
  regulation text; otherwise use "".
- Never invent a title, part, section, citation, or quote that does not
  appear in the provided regulation text.
- If you cannot ground your decision in the provided regulation text,
  return status "Needs Review" and evidence [].

Return ONLY valid JSON.

Do not include markdown.

Do not include explanations.

Use exactly this schema:

{
  "status": "Compliant",
  "confidence": 0.95,
  "reason": "...",
  "evidence": [{"title": 40, "part": "257", "section": "257.3",
                "date": "", "text_span": "...", "citation": "40 CFR 257.3"}]
}

The application already knows the contract clause title. Do not
infer, rename, rewrite, or otherwise modify it -- focus entirely on
determining status, confidence, and reason.
"""


class ComplianceAgent:
    """
    Evaluates whether a contract clause satisfies the requirements of a
    CFR regulation, using an LLM-backed Agno agent.
    """

    def __init__(self, model: Any | None = None) -> None:
        """
        Args:
            model: An Agno-compatible model instance. Defaults to the
                configured LLM (ATM/Nemotron via `ATM_API_KEY`) when not
                provided.
        """
        self.agent = Agent(
            name="CFR Compliance Reviewer",
            model=model or OpenAIChat(
                id=_llm_model(),
                base_url=_llm_base_url(),
                api_key=_llm_api_key() or None,
            ),
            output_schema=ComplianceResult,
            instructions=[_COMPLIANCE_REVIEWER_INSTRUCTIONS],
        )

    def evaluate(
        self,
        clause_title: str,
        clause_text: str,
        cfr_citation: str,
        cfr_text: str,
    ) -> ComplianceResult:
        """
        Evaluate a single contract clause against a single CFR regulation.

        Args:
            clause_title: Authoritative title of the contract clause, as
                known by the caller. This value always wins over anything
                the LLM returns.
            clause_text: Full text of the contract clause.
            cfr_citation: Citation identifying the CFR regulation
                (e.g. "48 CFR 52.204-21").
            cfr_text: Full text of the retrieved CFR regulation.

        Returns:
            A validated `ComplianceResult`.

        Raises:
            RuntimeError: If no LLM API key is configured, the API call
                fails, or the response cannot be parsed/validated into a
                `ComplianceResult`.
        """
        api_key = _llm_api_key()
        if not api_key:
            raise RuntimeError(
                "No LLM API key configured. Set ATM_API_KEY (or OPENAI_API_KEY) "
                "to enable LLM-based compliance evaluation."
            )

        prompt = self._build_prompt(
            clause_title=clause_title,
            clause_text=clause_text,
            cfr_citation=cfr_citation,
            cfr_text=cfr_text,
        )

        # Send the prompt to the LLM via a direct HTTP request to the
        # configured OpenAI-compatible endpoint (ATM/Nemotron by default).
        url = f"{_llm_base_url()}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _llm_model(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a federal contract compliance reviewer. "
                        "Compare the contract clause against the provided CFR "
                        "regulation and return ONLY valid JSON with exactly "
                        "these fields: status (one of Compliant, "
                        "Non-Compliant, Needs Review), confidence (0.0-1.0), "
                        "reason (a short explanation), and evidence (an array "
                        "of objects referencing ONLY the provided CFR "
                        "regulation). Each evidence object has: title (integer "
                        "CFR title, e.g. 40), part (e.g. '257'), section (e.g. "
                        "'257.3'), date ('' unless the regulation text shows "
                        "it), text_span (a verbatim quote FROM the provided "
                        "regulation text, never the clause), citation (e.g. "
                        "'40 CFR 257.3'). Never invent titles, citations, or "
                        "quotes absent from the provided regulation text. If "
                        "you cannot ground your decision in the provided "
                        "regulation text, return status 'Needs Review' with "
                        "evidence []. Do not include markdown or explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"LLM API returned status {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"LLM API request failed: {exc}"
            ) from exc

        try:
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("LLM API returned no choices")
            raw = choices[0].get("message", {}).get("content")
            if not isinstance(raw, str) or not raw.strip():
                raise RuntimeError("LLM API returned an empty response")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise RuntimeError("LLM API response was not a JSON object")
        except (json.JSONDecodeError, RuntimeError) as exc:
            raise RuntimeError(
                f"Failed to parse the compliance agent's JSON response: {exc}"
            ) from exc

        # ------------------------------------------------------------------
        # Validation into a strongly-typed ComplianceResult
        # ------------------------------------------------------------------
        # The caller-supplied title and the deterministic clause id always
        # win over anything the LLM echoes -- the LLM has been known to
        # echo the CFR heading instead of the contract clause title.
        parsed["clause_title"] = clause_title
        parsed["clause_id"] = _clause_id_from_text(clause_title, clause_text)

        confidence = parsed.get("confidence")
        if isinstance(confidence, (int, float)):
            parsed["confidence"] = max(0.0, min(1.0, float(confidence)))

        status = parsed.get("status")
        if isinstance(status, str):
            normalized = {
                "compliant": "Compliant",
                "non-compliant": "Non-Compliant",
                "noncompliant": "Non-Compliant",
                "needs review": "Needs Review",
                "needs-review": "Needs Review",
                "needs_review": "Needs Review",
            }.get(status.strip().lower())
            if normalized is not None:
                parsed["status"] = normalized

        if parsed.get("status") not in ("Compliant", "Non-Compliant", "Needs Review"):
            parsed["status"] = "Needs Review"

        if not isinstance(parsed.get("reason"), str) or not parsed["reason"].strip():
            parsed["reason"] = "The LLM response omitted a reason; routed to human review."
        else:
            parsed["reason"] = parsed["reason"].strip()

        evidence = parsed.pop("evidence", [])
        parsed_evidence: list[Any] = []
        for ev in evidence or []:
            if isinstance(ev, dict):
                parsed_evidence.append(self._coerce_evidence(ev))
        parsed["evidence"] = parsed_evidence

        try:
            return ComplianceResult.model_validate(parsed)
        except Exception as exc:
            raise RuntimeError(
                "Failed to parse the compliance agent's JSON response "
                "into a ComplianceResult"
            ) from exc

    @staticmethod
    def _coerce_evidence(ev: dict[str, Any]) -> dict[str, Any]:
        """Coerce an LLM evidence dict into a schema-safe mapping.

        The LLM has been observed echoing the clause title into
        ``evidence[].title`` (which must be an int) and embedding whole
        sentences in ``section``/``part``. This normalizes each field
        defensively so a single malformed evidence item cannot sink the
        entire evaluation; unparseable fields fall back to safe values.

        Provenance rule (HITL/evidence grounding): the LLM describes the
        passage it read but is never the authority for where the material
        came from. Any ``source``/``retrieved_at``/``retrieval_method``/
        ``version``/``confidence``/``date`` the LLM tries to include is
        dropped here -- ``date`` is always reset to "" and the retrieval
        layer injects the authoritative date/version afterward.
        """
        title = ev.get("title", 0)
        try:
            title_int = int(title)
        except (TypeError, ValueError):
            title_int = 0

        part = ev.get("part")
        if isinstance(part, (int, float)):
            part = str(int(part))
        elif not isinstance(part, str):
            part = None

        section = ev.get("section")
        if isinstance(section, (int, float)):
            section = str(int(section))
        elif not isinstance(section, str):
            section = None

        text_span = ev.get("text_span", "")
        if not isinstance(text_span, str):
            text_span = str(text_span)

        citation = ev.get("citation", "")
        if not isinstance(citation, str):
            citation = str(citation)

        return {
            "title": title_int,
            "part": part,
            "section": section,
            "date": "",  # LLM never supplies the authoritative date; retrieval does
            "text_span": text_span,
            "citation": citation,
        }

    @staticmethod
    def _build_prompt(
        *,
        clause_title: str,
        clause_text: str,
        cfr_citation: str,
        cfr_text: str,
    ) -> str:
        """Build the user-facing prompt for a single evaluation."""
        return f"""\
Review the following contract clause.

=====================
CONTRACT CLAUSE
=====================

Title:
{clause_title}

Text:
{clause_text}


=====================
CFR REGULATION
=====================

Citation:
{cfr_citation}

Regulation Text:
{cfr_text}


Compare the contract clause against the CFR regulation and determine
compliance.
"""

    # ------------------------------------------------------------------
# LLM configuration
# ------------------------------------------------------------------
#
# The compliance agent talks to an OpenAI-compatible chat endpoint
# (by default the ATM-hosted Nemotron service). Endpoint, model and
# credential are environment-driven -- never hard-coded -- so the
# same code can point at any compatible provider (ATM, OpenAI,
# Ollama's OpenAI-compatible API, a local vLLM, ...) without edits.
#
# Preference order: ATM_API_KEY (ATM/Nemotron default endpoint)
# falls back to OPENAI_API_KEY for a generic OpenAI-compatible host.


def _llm_api_key() -> str | None:
    """Return the configured LLM credential, or None if none is set."""
    return os.getenv("ATM_API_KEY") or os.getenv("OPENAI_API_KEY") or None


def _llm_base_url() -> str:
    """Return the LLM endpoint base URL (default: ATM /v1 endpoint)."""
    return os.getenv("ATM_BASE_URL", "https://atm.accure.ai/v1").rstrip("/")


def _llm_model() -> str:
    """Return the LLM model identifier (default: Nemotron 3 nano omni)."""
    return os.getenv("ATM_MODEL", "nvidia/nemotron-3-nano-omni")


def evaluate_compliance(
    clause_title: str,
    clause_text: str,
    cfr_citation: str,
    cfr_text: str,
    model: Any | None = None,
) -> ComplianceResult:
    """
    Convenience function used by the compliance pipeline.

    Constructs a `ComplianceAgent` and runs a single evaluation. See
    `ComplianceAgent.evaluate` for argument and return details.
    """
    agent = ComplianceAgent(model=model)

    return agent.evaluate(
        clause_title=clause_title,
        clause_text=clause_text,
        cfr_citation=cfr_citation,
        cfr_text=cfr_text,
    )


if __name__ == "__main__":
    print(
        """
CFR Compliance Agent module loaded.

Use evaluate_compliance()
with:
- contract clause
- CFR citation
- CFR regulation text
"""
    )