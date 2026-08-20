"""Security module for cfr-compliance-mcp.

Provides prompt injection protection and untrusted document handling
for the compliance engineering system.

Security design principles:
 - Defense-in-depth: multiple layers of security checks
 - Fail-safe: suspicious inputs default to "Needs Review" / rejection
 - Auditable: every security check is recorded in the compliance trail
 - Non-blocking: security checks add minimal latency for valid inputs
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.deterministic_rules import evaluate_deterministic
from agent.models import Clause

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt injection detection
# ---------------------------------------------------------------------------

# Patterns that indicate potential prompt injection in clause text
# These are patterns that try to make the LLM ignore previous instructions,
# extract the system prompt, or always return a specific answer
_PROMPT_INJECTION_PATTERNS = [
    # Ignore/inhibit previous instructions
    r"ignore (all |previous |prior |above )?instructions",
    r"disregard (all |previous |prior |above )?instructions",
    r"forget (all |previous |prior |above )?context",
    r"you were (not |never )?to",
    r"do not (listen to |follow |obey)",
    # System prompt extraction
    r"what (are |is) (your |the )?system (prompt |instruction)",
    r"can you (show |tell |give) (me |us) (the |your )?system",
    r"print (the |your )?system (prompt |instruction |message)",
    # Always-specific-answer
    r"always (answer |respond with |say )",
    r"you must (always |never )",
    r"return (only |just) ",
    # Role-playing / persona injection
    r"you are (now |henceforth) ",
    r"pretend (to be |that you )",
    # Boundary violations
    r"<\/\?pli(?:g|gable)>",
    r"</?prompt>",
    r"<\|channel\|>",
    # Pattern context markers common in prompt injection
    r"\bIgnore:\s*",
    r"\bOverride:\s*",
    r"\bSystem:\s*\n",
]

_compile_injection_patterns = [re.compile(p, re.IGNORECASE) for p in _PROMPT_INJECTION_PATTERNS]


def detect_prompt_injection(text: str) -> dict[str, Any]:
    """Detect prompt injection patterns in clause text.

    Args:
        text: The contract clause text to check for injection patterns.

    Returns:
        {"suspicious": bool, "patterns_matched": list[str], "details": str}
    """
    found: list[str] = []
    text_lower = text.lower()

    for pattern in _compile_injection_patterns:
        if pattern.search(text_lower):
            # Find which pattern matched
            for p in _PROMPT_INJECTION_PATTERNS:
                if re.search(p, text_lower, re.IGNORECASE) and p not in found:
                    found.append(p)

    suspicious = len(found) > 0

    if suspicious:
        details = f"Prompt injection patterns detected: {len(found)} match(es)".format(
            len=found,
        )
        # Show first few matches for debugging
        details += f": {found[:3]}"
    else:
        details = "No prompt injection patterns detected"

    return {
        "suspicious": suspicious,
        "patterns_matched": found,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Untrusted document handling
# -----------------------------------------------------------------------


# Maximum clause text length (prevents OOM from extremely large PDFs)
_MAX_CLAUSE_TEXT_CHARS = 500_000

# Maximum CFR regulation text length (_same purpose)
_MAX_CFR_TEXT_CHARS = 2_000_000

# Maximum allowed title number (CFR title bounds)
_MAX_CFR_TITLE = 50
_MIN_CFR_TITLE = 1


def sanitize_clause_text(text: str) -> str:
    """Sanitize clause text by removing or neutralizing potentially
    harmful content while preserving the core contractual meaning.

    Security measures:
    - Truncate to maximum length (prevent OOM)
    - Remove null bytes and control characters (except whitespace)
    - Normalize excessive whitespace
    - Strip non-printable characters

    Returns sanitized text that is safe for further processing.
    """
    if not text:
        return text

    # Truncate to maximum length
    if len(text) > _MAX_CLAUSE_TEXT_CHARS:
        text = text[:_MAX_CLAUSE_TEXT_CHARS]
        logger.warning(
            "Clause text truncated to %d characters (max %d)",
            _MAX_CLAUSE_TEXT_CHARS,
            _MAX_CLAUSE_TEXT_CHARS,
        )

    # Remove null bytes
    text = text.replace("\x00", "")

    # Remove or replace control characters (except whitespace)
    # Keep printable ASCII and Unicode, strip everything else
    cleaned_chars: list[str] = []
    for ch in text:
        code = ord(ch)
        # Keep: printable ASCII (32-126), common Unicode categories
        if code >= 0x20 and code <= 0x7E:
            cleaned_chars.append(ch)
        elif code > 0x7E:
            # Keep non-ASCII printable characters (preserve international text)
            cleaned_chars.append(ch)
        # Skip: control characters (0-31, except tab=9, newline=10, carriage return=13)

    text = "".join(cleaned_chars)

    # Normalize excessive whitespace (3+ consecutive newlines -> 2)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize excessive spaces
    text = re.sub(r"[ \t]{3,}", " ", text)

    return text.strip()


def validate_cfr_title(title: int | str) -> int | None:
    """Validate that a CFR title number is within the valid range.

    Returns the title as an int if valid, or None if invalid.
    This prevents out-of-range requests to the eCFR API.
    """
    try:
        t = int(title)
    except (ValueError, TypeError):
        return None

    if t < _MIN_CFR_TITLE or t > _MAX_CFR_TITLE:
        return None

    return t


def sanitize_cfr_text(text: str) -> str:
    """Sanitize CFR regulation text for the same security purposes as
    clause text, with a higher character limit since CFR texts are
    typically generated by the trusted eCFR API (not user-uploaded)."""
    if not text:
        return text

    # Truncate to maximum length
    if len(text) > _MAX_CFR_TEXT_CHARS:
        text = text[:_MAX_CFR_TEXT_CHARS]
        logger.warning(
            "CFR text truncated to %d characters (max %d)",
            _MAX_CFR_TEXT_CHARS,
            _MAX_CFR_TEXT_CHARS,
        )

    # Remove null bytes
    text = text.replace("\x00", "")

    # Normalize excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", " ", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Security-aware clause evaluation
# -----------------------------------------------------------------------


def check_clause_security(
    clause: Clause,
    *,
    title: int | str | None = None,
    cfr_text: str | None = None,
) -> dict[str, Any]:
    """Run the shared security gates on clause/regulatory input.

    This is the single gate-check function every evaluation path must
    pass through (FastAPI endpoint and CLI compliance pipeline alike),
    so the canonical evaluation path cannot accidentally bypass the
    security layer.

    Gates:
      - ``prompt_injection``: injection patterns in the clause text.
      - ``title_validation``: CFR title within 1-50, when a title is
        actually supplied (None skips the gate -- an absent title is
        not an invalid one).
      - ``clause_length``: clause text within the OOM-prevention limit.
      - ``cfr_length``: CFR text within the OOM-prevention limit, when
        supplied.

    Returns ``{"safe": bool, "checks": {gate: {"safe": bool, "details": str}},
    "reason": str}``. ``safe`` is False when ANY gate fails. Legitimate
    contract/regulatory language is not blocked: these checks only fire
    on genuine injection patterns, out-of-range titles, or oversized
    inputs.
    """
    checks: dict[str, Any] = {}

    injection = detect_prompt_injection(clause.text)
    checks["prompt_injection"] = {
        "safe": not injection["suspicious"],
        "details": injection["details"],
    }

    if title is not None:
        validated_title = validate_cfr_title(title)
        checks["title_validation"] = {
            "safe": validated_title is not None,
            "details": (
                f"CFR title {title!r} is valid"
                if validated_title is not None
                else f"Invalid CFR title {title!r}; must be 1-50"
            ),
        }
    else:
        checks["title_validation"] = {
            "safe": True,
            "details": "No CFR title supplied; gate skipped",
        }

    clause_len = len(clause.text)
    checks["clause_length"] = {
        "safe": clause_len <= _MAX_CLAUSE_TEXT_CHARS,
        "details": f"clause={clause_len} chars (limit {_MAX_CLAUSE_TEXT_CHARS})",
    }

    if cfr_text is not None:
        cfr_len = len(cfr_text)
        checks["cfr_length"] = {
            "safe": cfr_len <= _MAX_CFR_TEXT_CHARS,
            "details": f"CFR={cfr_len} chars (limit {_MAX_CFR_TEXT_CHARS})",
        }
    else:
        checks["cfr_length"] = {"safe": True, "details": "No CFR text supplied; gate skipped"}

    safe = all(check["safe"] for check in checks.values())
    failed_gates = [name for name, check in checks.items() if not check["safe"]]
    reason = (
        "Security checks: all passed"
        if safe
        else f"Security checks failed: {', '.join(failed_gates)}"
    )
    return {"safe": safe, "checks": checks, "reason": reason}


def evaluate_with_security(
    clause: Clause,
    cfr_text: str,
    title: int,
    *,
    run_deterministic: bool = True,
    run_verification: bool = True,
    run_injection_check: bool = True,
) -> dict[str, Any]:
    """Run the full compliance evaluation with security checks integrated.

    Returns a dict with:
        - "result": the ComplianceResult
        - "security_checks": dict of security check results
        - "safe": bool indicating if the evaluation was allowed to complete
        - "reason": additional reasoning about the security posture

    Security checks performed (via `check_clause_security`):
    1. Prompt injection detection on clause text
    2. CFR title validation
    3. Text length limits (OOM prevention)
    4. (Optional) Deterministic rule evaluation
    5. (Optional) LLM agent evaluation
    6. (Optional) Verification agent cross-check

    Prompt injection and invalid CFR titles fail closed (early return,
    "safe": False). Oversized text is truncated (sanitized) and the
    evaluation continues, flagged "safe": False -- matching prior
    behavior.
    """
    gate = check_clause_security(clause, title=title, cfr_text=cfr_text)
    security_checks = dict(gate["checks"])

    if run_injection_check and not security_checks["prompt_injection"]["safe"]:
        return {
            "result": None,
            "security_checks": security_checks,
            "safe": False,
            "reason": (
                f"Prompt injection detected in clause text: "
                f"{security_checks['prompt_injection']['details']}"
            ),
        }

    validated_title = validate_cfr_title(title)
    if validated_title is None:
        return {
            "result": None,
            "security_checks": security_checks,
            "safe": False,
            "reason": f"Invalid CFR title: {security_checks['title_validation']['details']}",
        }

    if not security_checks["clause_length"]["safe"]:
        # Oversized input: truncate and continue, but flag it.
        clause.text = sanitize_clause_text(clause.text)

    # --- Run deterministic rules (if enabled and safe so far) ---
    det_result = None
    det_reason = ""
    if run_deterministic:
        try:
            det_result = evaluate_deterministic(clause=clause, cfr_text=cfr_text, title=validated_title)  # noqa: E501
            det_reason = det_result.reason if det_result else ""
        except Exception as exc:
            logger.warning("Deterministic rule evaluation error: %s", exc)
            security_checks["deterministic"] = {
                "safe": False,
                "details": f"Deterministic evaluation error: {exc}",
            }

    # --- Run verification (if enabled) ---
    ver_result = None
    if run_verification and det_result and det_result.status == "Needs Review":
        try:
            from agent.verification_agent import verify_compliance

            ver_result = verify_compliance(
                result=det_result,
                clause=clause,
                cfr_text=cfr_text,
                title=validated_title,
                version_payload=None,
            )
        except Exception as exc:
            logger.warning("Verification agent error: %s", exc)

    # Build and return the evaluation outcome
    safe = all(check["safe"] for check in security_checks.values())

    return {
        "result": det_result,  # Will be overridden by LLM if needed
        "security_checks": security_checks,
        "verification": ver_result,  # None unless verification ran and passed
        "safe": safe,
        "reason": (
            f"Security checks: {', '.join(f'{k}={v['safe']}' for k, v in security_checks.items())}. "  # noqa: E501
            f"{det_reason}" if det_reason else ""
        ),
    }