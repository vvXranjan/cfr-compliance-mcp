"""Deterministic, rule-based compliance checking.

Purpose: provide a fast, LLM-free compliance decision for clear-cut cases,
so the LLM is only invoked when the deterministic rules cannot reach a
confident conclusion. This reduces cost, latency, and exposure to LLM
unreliability on straightforward clauses.

Rule philosophy:
 - Each rule is a pure function: given a clause and a CFR regulation text,
   it returns a verdict ("Compliant", "Non-Compliant", "Needs Review") plus
   a reason and a confidence (0.0 - 1.0).
 - Rules never invent or rewrite clause titles -- the caller-supplied title
   always wins.
 - Rules are additive: multiple rules can fire for one clause; the highest-
   confidence non-"Needs Review" verdict wins.
 - Rules are auditable: every rule that fires is recorded in the compliance
   result's `evidence` list.
"""

from __future__ import annotations

from typing import Any

from agent.models import Clause, ComplianceResult, EvidencePassage


def _extract_keywords(text: str) -> list[str]:
    """Extract significant keywords from text (simple tokenizer, stopword-filtered)."""
    STOP_WORDS = {
        "the", "and", "or", "but", "if", "as", "is", "are", "was", "were",
        "be", "been", "being", "to", "of", "in", "on", "at", "by", "for",
        "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "from", "up", "down", "out",
        "over", "under", "again", "further", "then", "once", "here", "there",
        "when", "where", "why", "how", "all", "any", "both", "each", "few",
        "more", "most", "other", "some", "such", "no", "nor", "not", "only",
        "own", "same", "so", "than", "too", "very", "s", "t", "can", "will",
        "just", "don", "should", "now",
        # contract/legal boilerplate
        "contractor", "owner", "agreement", "work", "shall", "comply",
        "perform", "performed", "performing", "services", "party",
        "parties", "section", "clause", "contract", "hereby", "herein",
        "hereof", "hereunder", "thereof", "pursuant", "applicable",
        "obligation", "obligations", "requirement", "requirements",
        "provision", "provisions", "term", "terms", "condition",
        "conditions", "including", "include", "includes", "provide",
        "provided", "provides", "ensure", "ensures", "course", "date",
        "time", "days", "written", "notice", "respect", "regard",
    }

    import re
    words = re.findall(r"[A-Za-z][A-Za-z\-]*", text.lower())
    return [w for w in words if len(w) >= 3 and w not in STOP_WORDS]


def _build_citation(title: int, part: str | None, section: str | None) -> str:
    """Build a human-readable CFR citation from its components."""
    part_prefix = f" {part}" if part else ""
    section_prefix = f".{section}" if section else ""
    return f"{title} CFR{part_prefix}{section_prefix}"


def _has_mandatory_language(text: str) -> bool:
    """Check if text contains CFR mandatory markers: shall, must, will be."""
    kw = _extract_keywords(text)
    return any(m in kw for m in ["shall", "must", "will be"])


def _has_prohibited_language(text: str) -> bool:
    """Check if text contains CFR prohibited markers: prohibited, forbidden,
    without exception, may not."""
    kw = _extract_keywords(text)
    return any(m in kw for m in ["prohibited", "forbidden", "without exception", "may not"])


def _topic_overlap(clause_kw: list[str], cfr_kw: list[str]) -> set[str]:
    """Return the intersection of clause and CFR keyword sets."""
    return set(clause_kw) & set(cfr_kw)


def rule_1_mandatory_obligation(clause: Clause, cfr_text: str, title: int) -> dict[str, Any]:
    """Rule 1: If CFR has mandatory language and the clause mentions NONE
    of the key regulatory topics, the clause fails to address the mandate ->
    Non-Compliant.

    If the clause does mention topics, this rule is inconclusive (Needs Review),
    since the clause may be addressing the requirement in ways the rule
    doesn't capture.
    """
    clause_kw = _extract_keywords(clause.text)
    cfr_kw = _extract_keywords(cfr_text)

    if not _has_mandatory_language(cfr_text):
        return {
            "verdict": "Needs Review",
            "reason": "CFR text contains no mandatory language (shall/must/will be)",
            "confidence": 0.0,
            "evidence": None,
        }

    # CFR has mandatory language - check if clause covers any key topics
    overlap = _topic_overlap(clause_kw, cfr_kw)

    if not overlap:
        # Clause has zero topic overlap with CFR despite mandatory language
        evidence = EvidencePassage(
            title=title,
            part=None,
            section=None,
            date="",
            text_span=cfr_text[:200] + ("..." if len(cfr_text) > 200 else ""),
            citation=_build_citation(title, None, None),
        )
        return {
            "verdict": "Non-Compliant",
            "reason": "Clause mentions none of the CFR's required topics; CFR has mandatory language",  # noqa: E501
            "confidence": 0.80,
            "evidence": evidence,
        }

    # Clause has some topic overlap - cannot decide from this rule alone
    return {
        "verdict": "Needs Review",
        "reason": f"Clause has topic overlap {sorted(overlap)} but rule cannot assess compliance completeness",  # noqa: E501
        "confidence": 0.0,
        "evidence": None,
    }


def rule_2_prohibited_violation(clause: Clause, cfr_text: str, title: int) -> dict[str, Any]:
    """Rule 2: If CFR prohibits a practice and the clause describes or
    requests that same practice, the clause violates the prohibition ->
    Non-Compliant.

    If no prohibited language in CFR, or clause doesn't request the
    prohibited practice, this rule is inconclusive (Needs Review).
    """
    if not _has_prohibited_language(cfr_text):
        return {
            "verdict": "Needs Review",
            "reason": "CFR text contains no prohibited/forbidden language",
            "confidence": 0.0,
            "evidence": None,
        }

    # CFR has prohibited language - check if clause requests the prohibited action
    import re
    # Match mandatory verbs in clause text, including conjugated forms
    # Patterns: "shall", "must", "will", "agrees to", "agree to", etc.
    verb_patterns = [
        r"\bshall\b",
        r"\bmust\b",
        r"\bwill\b",
        r"\bagrees?\s+to\b",  # "agree to" or "agrees to"
    ]
    clause_action_verb = None
    for pattern in verb_patterns:
        match = re.search(pattern, clause.text, re.IGNORECASE)
        if match:
            verb = match.group(0).lower()
            clause_action_verb = verb
            break

    if clause_action_verb:
        # Clause has mandatory language that may conflict with CFR prohibition
        evidence = EvidencePassage(
            title=title,
            part=None,
            section=None,
            date="",
            text_span=cfr_text[:200] + ("..." if len(cfr_text) > 200 else ""),
            citation=_build_citation(title, None, None),
        )
        return {
            "verdict": "Non-Compliant",
            "reason": f"CFR prohibits certain practices; clause uses mandatory language "
                       f"({clause_action_verb}) that may conflict with prohibition",
            "confidence": 0.70,
            "evidence": evidence,
        }

    return {
        "verdict": "Needs Review",
        "reason": "Prohibited violation check inconclusive - no clear clash between clause actions and CFR prohibitions",  # noqa: E501
        "confidence": 0.0,
        "evidence": None,
    }


def rule_3_specific_requirement(clause: Clause, cfr_text: str, title: int,
                                part: str | None = None, section: str | None = None) -> dict[str, Any]:  # noqa: E501
    """Rule 3: If the clause explicitly references the CFR terms that the
    regulation requires, the clause satisfies that specific requirement ->
    Compliant.

    If the clause does NOT reference the required CFR terms for the
    referenced section/part, the clause fails the requirement ->
    Non-Compliant.

    This is the most targeted rule - it checks for specific terminology
    that appears in both the clause and the CFR regulation. Uses both
    keyword-based matching (for terminology) and direct substring matching
    (for section/part numbers, which keyword extraction loses since they
    start with digits). Also checks the clause text for section number
    references via regex patterns.
    """
    clause_kw = _extract_keywords(clause.text)

    # Build section/part markers from the provided identifiers
    section_markers = []
    if section:
        section_markers.append(section.lower())
        # Also try the part-after-dot form
        if "." in section:
            section_markers.append(section.split(".")[-1])
    if part:
        section_markers.append(part.lower())

    if not section_markers:
        # No specific section/part to check against; fall back to title-level
        return {
            "verdict": "Needs Review",
            "reason": "Rule 3 requires part and/or section scope; none provided",
            "confidence": 0.0,
            "evidence": None,
        }

    cfr_lower = cfr_text.lower()
    
    # First: check via direct substring match for section/part numbers
    # (keyword extraction loses numeric identifiers like "257.3")
    direct_matches = [m for m in section_markers if m in cfr_lower]
    
    if not direct_matches:
        # Direct substring match failed - fall back to keyword-based check
        # (which may miss section numbers but catches terminology)
        matches_in_cfr = [m for m in section_markers if any(kw == m for kw in _extract_keywords(cfr_text))]  # noqa: E501
        if not matches_in_cfr:
            # Neither direct nor keyword-based match found
            return {
                "verdict": "Needs Review",
                "reason": f"Section/part markers {section_markers} not found in CFR text (neither direct nor keyword-based)",  # noqa: E501
                "confidence": 0.0,
                "evidence": None,
            }
        # Use keyword-based matches only
        direct_matches = matches_in_cfr

    # Check if clause mentions these markers via keyword extraction
    clause_has_markers = any(m in clause_kw for m in direct_matches)

    # SECONDARY CHECK: also look for section numbers in clause text via regex
    # (keywords like "257.3" are lost during stopword filtering, so we
    #  search the raw clause text for the section number pattern)
    if not clause_has_markers and section:
        import re
        # Look for the section number (with or without part prefix) in clause text
        section_patterns = []
        if part:
            section_patterns.append(rf"\b{re.escape(section)}\b")
            section_patterns.append(rf"\b{part}\s*{re.escape(section)}\b")
        else:
            section_patterns.append(rf"\b{re.escape(section)}\b")
        
        clause_text_lower = clause.text.lower()
        section_found_in_clause = any(
            re.search(p, clause_text_lower)
            for p in section_patterns
        )
        
        if section_found_in_clause:
            # Clause references the section number via text - treat as marker match
            clause_has_markers = True

    if clause_has_markers:
        evidence = EvidencePassage(
            title=title,
            part=part,
            section=section,
            date="",
            text_span=cfr_text[:200] + ("..." if len(cfr_text) > 200 else ""),
            citation=_build_citation(title, part, section),
        )
        return {
            "verdict": "Compliant",
            "reason": f"Clause references CFR-required terms for {_build_citation(title, part, section)}",  # noqa: E501
            "confidence": 0.90,
            "evidence": evidence,
        }

    # Clause does not mention the required terms -> Non-Compliant
    evidence = EvidencePassage(
        title=title,
        part=part,
        section=section,
        date="",
        text_span=cfr_text[:200] + ("..." if len(cfr_text) > 200 else ""),
        citation=_build_citation(title, part, section),
    )
    return {
        "verdict": "Non-Compliant",
        "reason": f"Clause does not reference CFR-required terms for {_build_citation(title, part, section)}",  # noqa: E501
        "confidence": 0.75,
        "evidence": evidence,
    }


def evaluate_deterministic(
    clause: Clause,
    cfr_text: str,
    title: int,
    part: str | None = None,
    section: str | None = None,
) -> ComplianceResult:
    """Run all deterministic rules against a clause-CFR pair and return
    a ComplianceResult.

    The verdict hierarchy: Non-Compliant > Compliant > Needs Review.
    Rules are evaluated in order (3, then 1, then 2); the first rule
    to produce a verdict other than "Needs Review" wins. If all rules
    are "Needs Review", the caller should fall back to the LLM.

    Returns a ComplianceResult with evidence populated from any rules
    that fired.
    """
    # Rule 3: Specific requirement check (most specific, requires part/section)
    r3 = rule_3_specific_requirement(clause, cfr_text, title, part, section)
    if r3["verdict"] != "Needs Review":
        return ComplianceResult(
            clause_title=clause.title,
            clause_id=clause.clause_id,
            status=r3["verdict"],
            confidence=r3["confidence"],
            reason=r3["reason"],
            evidence=r3["evidence"]
            if isinstance(r3["evidence"], list)
            else [r3["evidence"]] if r3["evidence"] is not None else [],
        )

    # Rule 1: Mandatory obligation check
    r1 = rule_1_mandatory_obligation(clause, cfr_text, title)
    if r1["verdict"] != "Needs Review":
        return ComplianceResult(
            clause_title=clause.title,
            clause_id=clause.clause_id,
            status=r1["verdict"],
            confidence=r1["confidence"],
            reason=r1["reason"],
            evidence=r1["evidence"]
            if isinstance(r1["evidence"], list)
            else [r1["evidence"]] if r1["evidence"] is not None else [],
        )

    # Rule 2: Prohibited violation check
    r2 = rule_2_prohibited_violation(clause, cfr_text, title)
    if r2["verdict"] != "Needs Review":
        return ComplianceResult(
            clause_title=clause.title,
            clause_id=clause.clause_id,
            status=r2["verdict"],
            confidence=r2["confidence"],
            reason=r2["reason"],
            evidence=r2["evidence"]
            if isinstance(r2["evidence"], list)
            else [r2["evidence"]] if r2["evidence"] is not None else [],
        )

    # All rules Needs Review - fall back to LLM
    return ComplianceResult(
        clause_title=clause.title,
        clause_id=clause.clause_id,
        status="Needs Review",
        confidence=0.0,
        reason="No deterministic rule could reach a conclusion; LLM evaluation required",
        evidence=[],
    )