"""agent/cfr_query_optimizer.py

Deterministic CFR-domain query optimization layer, sitting between
`build_search_query()` and the `search_regulations` MCP tool call in
`agent/mcp_search.py`.

Why this exists: `build_search_query()` strips contract boilerplate
down to a generic keyword bag (e.g. "occupational safety health
maintain safe working environment") that is faithful to the clause's
own wording but often *dilutes* the domain-specific signal eCFR's
search ranking responds best to -- a contract clause rarely uses the
CFR's own vocabulary verbatim. This module adds a second, independent
pass: match the clause's raw text (title + body, *before* boilerplate
stripping, so multi-word domain phrases like "hazardous waste" or
"equal opportunity" survive intact) against a fixed table of CFR-domain
keyword sets. If a domain is confidently detected, the optimizer
proposes a query built from that domain's own matched canonical terms
-- always grounded in words the clause and the domain table both
actually contain, never fabricated -- plus a predicted CFR title, so
`agent/mcp_search.py`'s result-selection step can prefer on-domain hits.

Deliberately deterministic: exact case-insensitive substring matching
against a fixed, hand-curated table only. No LLM, no embeddings, no
fuzzy/semantic matching -- this keeps the optimizer's behavior fully
predictable and auditable, matching the project's existing
`build_search_query()` philosophy (see that function's own docstring in
`agent/mcp_search.py`). LLM-based ranking is an explicitly later
pipeline stage, not this module's job.

Depends only on the standard library -- no dependency on `fastmcp`,
`cfr_compliance_mcp`, or `agent.mcp_search`, so it can be unit-tested in
isolation and reused outside the retrieval pipeline if ever needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

__all__ = ["optimize_clause", "OptimizedQueryResult"]


class OptimizedQueryResult(TypedDict):
    """Return shape of `optimize_clause()`.

    Deliberately a plain `TypedDict` (not a dataclass): this is a
    boundary value handed straight back to `agent/mcp_search.py` and
    logged as-is, not manipulated further within this module.
    """

    search_query: str
    title: str | None
    matches: list[str]


@dataclass(frozen=True, slots=True)
class _DomainRule:
    """One CFR-domain keyword table entry.

    `keywords` are matched as case-insensitive substrings against the
    clause's raw (title + text) content. Multi-word phrases are matched
    whole (e.g. "hazardous waste" must appear as that exact phrase, not
    just both words anywhere in the clause) -- deliberately stricter
    than a bag-of-words match, to avoid false positives like a clause
    mentioning "waste" in an unrelated sense while still catching
    genuine bigrams like "equal opportunity".
    """

    name: str
    keywords: tuple[str, ...]
    title: str


# Order is significant: it is the tie-break order when two or more
# domains match the same (highest) number of keywords in a clause --
# the first-listed domain wins. Chosen to put the more specific/less
# ambiguous domains first (environmental, employment) ahead of the
# broader ones (labor, procurement) that share vocabulary with others
# (e.g. "employment" and "labor" both plausibly apply to a
# wage/payroll clause).
_DOMAIN_RULES: tuple[_DomainRule, ...] = (
    _DomainRule(
        name="environmental",
        keywords=(
            "hazardous waste",
            "waste",
            "disposal",
            "epa",
            "chemical",
            "pollution",
        ),
        title="40",
    ),
    _DomainRule(
        name="employment",
        keywords=(
            "discrimination",
            "equal opportunity",
            "affirmative action",
            "race",
            "gender",
            "employment",
        ),
        title="41",
    ),
    _DomainRule(
        name="safety",
        keywords=(
            "workplace safety",
            "construction safety",
            "fall protection",
            "scaffold",
            "hazard communication",
            "osha",
        ),
        title="29",
    ),
    _DomainRule(
        name="labor",
        keywords=(
            "wage",
            "payroll",
            "laborer",
            "mechanic",
            "certified payroll",
        ),
        title="29",
    ),
    _DomainRule(
        name="procurement",
        keywords=(
            "government contract",
            "federal contract",
            "subcontractor",
        ),
        title="48",
    ),
)


def _matched_keywords(rule: _DomainRule, haystack_lower: str) -> list[str]:
    """Return the subset of `rule.keywords` that appear as a
    case-insensitive substring in `haystack_lower`, preserving the
    rule's own canonical keyword order (not the order they occur in the
    clause) -- so `search_query` construction downstream is
    deterministic regardless of clause phrasing.
    """
    return [kw for kw in rule.keywords if kw in haystack_lower]


def _detect_domain(clause_title: str, clause_text: str) -> tuple[_DomainRule | None, list[str]]:
    """Score every domain rule against the clause's raw content and
    return the best match `(rule, matched_keywords)`, or `(None, [])`
    if no domain matched at all.

    Scoring is a simple match count -- the domain with the most matched
    keywords wins; ties keep the first-listed domain in `_DOMAIN_RULES`
    (see that table's own comment for why that order was chosen). This
    is intentionally simple: a weighted/TF-IDF-style score would need a
    labeled corpus to calibrate against, which this deterministic,
    no-LLM stage doesn't have.
    """
    haystack = f"{clause_title} {clause_text}".lower()

    best_rule: _DomainRule | None = None
    best_matches: list[str] = []
    best_score = 0

    for rule in _DOMAIN_RULES:
        matches = _matched_keywords(rule, haystack)
        if len(matches) > best_score:
            best_rule = rule
            best_matches = matches
            best_score = len(matches)

    return best_rule, best_matches


def _dedupe_contained(keywords: list[str]) -> list[str]:
    """Drop any keyword that is itself a substring of another matched
    keyword (e.g. drop bare "waste" when "hazardous waste" also
    matched) -- avoids redundant repeated terms in the built query
    string. Order-preserving; only used for `search_query`
    construction, never for the `matches` list returned to the caller
    (which stays the full, unfiltered set for audit/logging purposes).
    """
    return [kw for kw in keywords if not any(kw != other and kw in other for other in keywords)]


def optimize_clause(clause_title: str, clause_text: str, base_query: str) -> OptimizedQueryResult:
    """Run the deterministic CFR-domain optimizer for one clause.

    Args:
        clause_title: the clause's own heading/title text (e.g.
            `Clause.title`), included in domain matching alongside the
            body since a heading like "Hazardous Waste Disposal" is
            often the clearest domain signal in the whole clause.
        clause_text: the clause's full body text (e.g. `Clause.text`).
        base_query: the already-built `build_search_query(clause)`
            output -- used as the fallback `search_query` when no
            domain is confidently detected, so this function never
            makes the query *worse* than the existing pipeline, only
            potentially better.

    Returns:
        An `OptimizedQueryResult`:
          - `search_query`: the matched domain's keywords (in the
            domain table's own canonical order, with any keyword that
            is a substring of another match deduped out -- e.g. bare
            "waste" dropped when "hazardous waste" also matched) joined
            into a query string, if a domain was detected; otherwise
            `base_query` unchanged.
          - `title`: the predicted CFR title as a string (e.g. "29"),
            or `None` if no domain was confidently detected.
          - `matches`: the specific keyword phrases that were found in
            the clause and drove the prediction -- the full,
            undeduped set, always grounded in the clause's actual
            wording and never fabricated, so callers can log/audit
            exactly why a given title was predicted.
    """
    rule, matches = _detect_domain(clause_title, clause_text)

    if rule is None:
        return OptimizedQueryResult(search_query=base_query, title=None, matches=[])

    optimized_query = " ".join(_dedupe_contained(matches))
    return OptimizedQueryResult(search_query=optimized_query, title=rule.title, matches=matches)