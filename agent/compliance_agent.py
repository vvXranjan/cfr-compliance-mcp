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
from typing import Any, Mapping, Optional

from agno.agent import Agent
# from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat

from .models import ComplianceResult

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

Return ONLY valid JSON.

Do not include markdown.

Do not include explanations.

Use exactly this schema:

{
  "status": "Compliant",
  "confidence": 0.95,
  "reason": "..."
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

    def __init__(self, model: Optional[Any] = None) -> None:
        """
        Args:
            model: An Agno-compatible model instance. Defaults to
                `Ollama(id="llama3.1")` when not provided.
        """
        self.agent = Agent(
            name="CFR Compliance Reviewer",
            # model=model or Ollama(id="llama3.1"),
            model=model or OpenAIChat(
                id="nvidia/nemotron-3-super",
                base_url="https://atm.accure.ai/v1",
                api_key="atm_JIxbkUNzYqsRpRXlnSAnHUODVaIflcoQFa",
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
            RuntimeError: If the agent's response cannot be parsed or
                validated into a `ComplianceResult`.
        """
        prompt = self._build_prompt(
            clause_title=clause_title,
            clause_text=clause_text,
            cfr_citation=cfr_citation,
            cfr_text=cfr_text,
        )

        response = self.agent.run(prompt)
        print("\n" + "=" * 80)
        print("RAW MODEL RESPONSE")
        print("=" * 80)
        print(response.content)
        print("=" * 80 + "\n")

        result = self._parse_response(
            response.content,
            clause_title=clause_title,
        )

        return result

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
    # Response parsing
    # ------------------------------------------------------------------
    #
    # `response.content` from the Agno agent may arrive in several
    # shapes depending on the model backend and how well it honored
    # `output_schema`. Each shape is handled explicitly and safely; none
    # of them are allowed to fail silently.

    def _parse_response(self, raw: Any, clause_title: str) -> ComplianceResult:
        """
        Dispatch `response.content` to the appropriate parser based on
        its runtime type.

        Raises:
            RuntimeError: If `raw` cannot be turned into a
                `ComplianceResult`.
        """
        # Case A: already a validated ComplianceResult. The LLM's own
        # clause_title is never trusted here either -- it sometimes
        # echoes the CFR heading instead of the original contract
        # clause title, so the caller-supplied value always wins.
        if isinstance(raw, ComplianceResult):
            return raw.model_copy(update={"clause_title": clause_title})
         

        # Case B: a plain dict or other Mapping.
        if isinstance(raw, Mapping):
            return self._parse_mapping(raw, clause_title=clause_title, source="dict")

        # Case C / D: a string, which may be a JSON object (Case C) or
        # arbitrary, non-JSON prose (Case D).
        if isinstance(raw, str):
            mapping = self._parse_json(raw)
            return self._parse_mapping(mapping, clause_title=clause_title, source="JSON string")

        # Case E: anything else is an unexpected response shape.
        raise RuntimeError(
            "Failed to parse the compliance agent's response into a "
            f"ComplianceResult: unexpected response.content type "
            f"{type(raw).__name__!r}."
        )

    @staticmethod
    def _parse_json(raw: str) -> Mapping[str, Any]:
        """
        Decode a JSON string into a mapping.

        Raises:
            RuntimeError: If `raw` is not valid JSON, or decodes to
                something other than a JSON object.
        """
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            # Plain text that isn't JSON at all. There's no reliable way
            # to recover structured fields from arbitrary prose, so this
            # is a genuine parsing failure.
            raise RuntimeError(
                "Failed to parse the compliance agent's response into a "
                "ComplianceResult: response was plain text, not JSON or "
                f"a dict. Raw response (truncated): {repr(raw)[:200]!r}"
            ) from exc

        if not isinstance(parsed, Mapping):
            raise RuntimeError(
                "Failed to parse the compliance agent's response into a "
                "ComplianceResult: JSON response did not decode to an "
                f"object, got {type(parsed).__name__}."
            )

        return parsed

    @staticmethod
    def _parse_mapping(
        data: Mapping[str, Any],
        *,
        clause_title: str,
        source: str,
    ) -> ComplianceResult:
        """
        Validate a mapping (from a dict response, or from decoding a
        JSON string response) into a `ComplianceResult`.

        The LLM is only instructed to return `status`, `confidence`,
        and `reason` -- `clause_title` is never part of its output, so
        the caller-supplied `clause_title` is injected into the
        candidate mapping here, before validation, rather than relying
        on `ComplianceResult.model_validate()` to accept a mapping that
        doesn't yet satisfy the model's required fields.

        Confidence is clamped *before* validation, not after:
        `ComplianceResult`'s `Field(ge=0.0, le=1.0)` constraint makes
        `model_validate()` raise on an out-of-range value rather than
        clamp it, so a slightly-out-of-range LLM confidence has to be
        fixed up here first, or the whole evaluation fails instead of
        being clamped as intended.

        Raises:
            RuntimeError: If the mapping fails schema validation.
        """
        candidate = dict(data)
        candidate["clause_title"] = clause_title

        confidence = candidate.get("confidence")
        if isinstance(confidence, (int, float)):
            candidate["confidence"] = _clamp_confidence(float(confidence))

        return ComplianceAgent._validate_result(candidate, source=source)

    @staticmethod
    def _validate_result(candidate: Mapping[str, Any], *, source: str) -> ComplianceResult:
        """
        Run final pydantic validation on a candidate mapping.

        Raises:
            RuntimeError: If validation fails, wrapping the original
                exception for debuggability.
        """
        try:
            return ComplianceResult.model_validate(candidate)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to parse the compliance agent's {source} response "
                "into a ComplianceResult"
            ) from exc


def _clamp_confidence(confidence: float) -> float:
    """Clamp a confidence value into the inclusive range [0.0, 1.0]."""
    return max(0.0, min(1.0, confidence))


def evaluate_compliance(
    clause_title: str,
    clause_text: str,
    cfr_citation: str,
    cfr_text: str,
    model: Optional[Any] = None,
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