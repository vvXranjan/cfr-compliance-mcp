# """agent/cfr_query_optimizer.py

# Deterministic CFR-domain query optimization layer, sitting between
# `build_search_query()` and the `search_regulations` MCP tool call in
# `agent/mcp_search.py`.

# Why this exists: `build_search_query()` strips contract boilerplate
# down to a generic keyword bag (e.g. "occupational safety health
# maintain safe working environment") that is faithful to the clause's
# own wording but often *dilutes* the domain-specific signal eCFR's
# search ranking responds best to -- a contract clause rarely uses the
# CFR's own vocabulary verbatim. This module adds a second, independent
# pass: match the clause's raw text (title + body, *before* boilerplate
# stripping, so multi-word domain phrases like "hazardous waste" or
# "equal opportunity" survive intact) against a fixed table of CFR-domain
# keyword sets. If a domain is confidently detected, the optimizer
# proposes a query built from that domain's own matched canonical terms
# -- always grounded in words the clause and the domain table both
# actually contain, never fabricated -- plus a predicted CFR title, so
# `agent/mcp_search.py`'s result-selection step can prefer on-domain hits.

# Deliberately deterministic: exact case-insensitive substring matching
# against a fixed, hand-curated table only. No LLM, no embeddings, no
# fuzzy/semantic matching -- this keeps the optimizer's behavior fully
# predictable and auditable, matching the project's existing
# `build_search_query()` philosophy (see that function's own docstring in
# `agent/mcp_search.py`). LLM-based ranking is an explicitly later
# pipeline stage, not this module's job.

# Depends only on the standard library -- no dependency on `fastmcp`,
# `cfr_compliance_mcp`, or `agent.mcp_search`, so it can be unit-tested in
# isolation and reused outside the retrieval pipeline if ever needed.
# """

# from __future__ import annotations

# from dataclasses import dataclass
# from typing import TypedDict

# __all__ = ["optimize_clause", "OptimizedQueryResult"]


# class OptimizedQueryResult(TypedDict):
#     """Return shape of `optimize_clause()`.

#     Deliberately a plain `TypedDict` (not a dataclass): this is a
#     boundary value handed straight back to `agent/mcp_search.py` and
#     logged as-is, not manipulated further within this module.
#     """

#     search_query: str
#     title: str | None
#     matches: list[str]


# @dataclass(frozen=True, slots=True)
# class _DomainRule:
#     """One CFR-domain keyword table entry.

#     `keywords` are matched as case-insensitive substrings against the
#     clause's raw (title + text) content. Multi-word phrases are matched
#     whole (e.g. "hazardous waste" must appear as that exact phrase, not
#     just both words anywhere in the clause) -- deliberately stricter
#     than a bag-of-words match, to avoid false positives like a clause
#     mentioning "waste" in an unrelated sense while still catching
#     genuine bigrams like "equal opportunity".
#     """

#     name: str
#     keywords: tuple[str, ...]
#     title: str


# # Order is significant: it is the tie-break order when two or more
# # domains match the same (highest) number of keywords in a clause --
# # the first-listed domain wins. Chosen to put the more specific/less
# # ambiguous domains first (environmental, employment) ahead of the
# # broader ones (labor, procurement) that share vocabulary with others
# # (e.g. "employment" and "labor" both plausibly apply to a
# # wage/payroll clause).
# _DOMAIN_RULES: tuple[_DomainRule, ...] = (
#     _DomainRule(
#         name="environmental",
#         keywords=(
#             "hazardous waste",
#             "waste",
#             "disposal",
#             "epa",
#             "chemical",
#             "pollution",
#         ),
#         title="40",
#     ),
#     _DomainRule(
#         name="employment",
#         keywords=(
#             "discrimination",
#             "equal opportunity",
#             "affirmative action",
#             "race",
#             "gender",
#             "employment",
#         ),
#         title="41",
#     ),
#     _DomainRule(
#         name="safety",
#         keywords=(
#             "workplace safety",
#             "construction safety",
#             "fall protection",
#             "scaffold",
#             "hazard communication",
#             "osha",
#         ),
#         title="29",
#     ),
#     _DomainRule(
#         name="labor",
#         keywords=(
#             "wage",
#             "payroll",
#             "laborer",
#             "mechanic",
#             "certified payroll",
#         ),
#         title="29",
#     ),
#     _DomainRule(
#         name="procurement",
#         keywords=(
#             "government contract",
#             "federal contract",
#             "subcontractor",
#         ),
#         title="48",
#     ),
# )


# def _matched_keywords(rule: _DomainRule, haystack_lower: str) -> list[str]:
#     """Return the subset of `rule.keywords` that appear as a
#     case-insensitive substring in `haystack_lower`, preserving the
#     rule's own canonical keyword order (not the order they occur in the
#     clause) -- so `search_query` construction downstream is
#     deterministic regardless of clause phrasing.
#     """
#     return [kw for kw in rule.keywords if kw in haystack_lower]


# def _detect_domain(clause_title: str, clause_text: str) -> tuple[_DomainRule | None, list[str]]:
#     """Score every domain rule against the clause's raw content and
#     return the best match `(rule, matched_keywords)`, or `(None, [])`
#     if no domain matched at all.

#     Scoring is a simple match count -- the domain with the most matched
#     keywords wins; ties keep the first-listed domain in `_DOMAIN_RULES`
#     (see that table's own comment for why that order was chosen). This
#     is intentionally simple: a weighted/TF-IDF-style score would need a
#     labeled corpus to calibrate against, which this deterministic,
#     no-LLM stage doesn't have.
#     """
#     haystack = f"{clause_title} {clause_text}".lower()

#     best_rule: _DomainRule | None = None
#     best_matches: list[str] = []
#     best_score = 0

#     for rule in _DOMAIN_RULES:
#         matches = _matched_keywords(rule, haystack)
#         if len(matches) > best_score:
#             best_rule = rule
#             best_matches = matches
#             best_score = len(matches)

#     return best_rule, best_matches


# def _dedupe_contained(keywords: list[str]) -> list[str]:
#     """Drop any keyword that is itself a substring of another matched
#     keyword (e.g. drop bare "waste" when "hazardous waste" also
#     matched) -- avoids redundant repeated terms in the built query
#     string. Order-preserving; only used for `search_query`
#     construction, never for the `matches` list returned to the caller
#     (which stays the full, unfiltered set for audit/logging purposes).
#     """
#     return [kw for kw in keywords if not any(kw != other and kw in other for other in keywords)]


# def optimize_clause(clause_title: str, clause_text: str, base_query: str) -> OptimizedQueryResult:
#     """Run the deterministic CFR-domain optimizer for one clause.

#     Args:
#         clause_title: the clause's own heading/title text (e.g.
#             `Clause.title`), included in domain matching alongside the
#             body since a heading like "Hazardous Waste Disposal" is
#             often the clearest domain signal in the whole clause.
#         clause_text: the clause's full body text (e.g. `Clause.text`).
#         base_query: the already-built `build_search_query(clause)`
#             output -- used as the fallback `search_query` when no
#             domain is confidently detected, so this function never
#             makes the query *worse* than the existing pipeline, only
#             potentially better.

#     Returns:
#         An `OptimizedQueryResult`:
#           - `search_query`: the matched domain's keywords (in the
#             domain table's own canonical order, with any keyword that
#             is a substring of another match deduped out -- e.g. bare
#             "waste" dropped when "hazardous waste" also matched) joined
#             into a query string, if a domain was detected; otherwise
#             `base_query` unchanged.
#           - `title`: the predicted CFR title as a string (e.g. "29"),
#             or `None` if no domain was confidently detected.
#           - `matches`: the specific keyword phrases that were found in
#             the clause and drove the prediction -- the full,
#             undeduped set, always grounded in the clause's actual
#             wording and never fabricated, so callers can log/audit
#             exactly why a given title was predicted.
#     """
#     rule, matches = _detect_domain(clause_title, clause_text)

#     if rule is None:
#         return OptimizedQueryResult(search_query=base_query, title=None, matches=[])

#     optimized_query = " ".join(_dedupe_contained(matches))
#     return OptimizedQueryResult(search_query=optimized_query, title=rule.title, matches=matches)


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
pass that runs *before* that stripping happens: match the clause's raw
text (title + body, untouched by boilerplate/stopword removal, so
multi-word domain phrases like "hazardous waste" or "equal
opportunity" survive intact) against a fixed table of CFR-domain
keyword sets. If a domain is confidently detected, the optimizer
proposes a query built from that domain's own matched canonical terms
-- always grounded in words the clause and the domain table both
actually contain, never fabricated -- plus a predicted CFR title and a
full audit trail explaining the decision, so a caller (or a human
reviewing a compliance run later) can see exactly why a given title was
predicted, not just that it was.

Deliberately deterministic: whole-word/whole-phrase, case-insensitive
matching against a fixed, hand-curated table only -- no LLM, no
embeddings, no fuzzy/semantic matching, no scoring model that needs
calibration data. This keeps the optimizer's behavior fully predictable
and auditable, matching the project's existing `build_search_query()`
philosophy (see that function's own docstring in `agent/mcp_search.py`).
LLM-based ranking is an explicitly later pipeline stage, not this
module's job.

Matching note: keywords are matched with regex word boundaries
(`\\bkeyword\\b`), not a plain substring check. A plain substring check
was this module's original approach and had a real false-positive bug:
`"epa"` matched inside `"separate"`, `"race"` matched inside
`"embrace"`, `"wage"` matched inside `"sewage"` -- all of which are
common enough words to show up in ordinary contract prose and would
have silently mispredicted a domain. Word-boundary matching fixes this
while still catching phrases like "hazardous waste" as a whole unit.

Standard-library only (`dataclasses`, `re`, `typing`) -- no dependency
on `fastmcp`, `cfr_compliance_mcp`, or `agent.mcp_search`, so this
module can be imported and unit-tested in complete isolation, and
reused outside the retrieval pipeline if ever needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypedDict

__all__ = ["optimize_clause", "OptimizedQueryResult"]


class OptimizedQueryResult(TypedDict):
    """Return shape of `optimize_clause()`.

    Deliberately a plain `TypedDict` (not a dataclass): this is a
    boundary value handed straight back to `agent/mcp_search.py` (and
    to anything logging/auditing a compliance run) and is not
    manipulated further within this module once built.

    Fields:
        search_query: the query string to hand to `search_regulations`
            -- the matched domain's keywords (deduped, canonical order)
            if a domain was detected, otherwise the caller's
            `base_query` unchanged.
        title: the predicted CFR title as a string (e.g. "29"), or
            `None` if no domain was confidently detected.
        matches: the specific keyword phrases found in the clause that
            drove the prediction for the *winning* domain -- the full,
            undeduped set, always grounded in the clause's actual
            wording. Empty if no domain was detected.
        domain: the internal name of the winning domain (e.g.
            "environmental"), or `None` if no domain was detected.
        score: the winning domain's match count (0 if none detected).
        confidence: a deterministic tier derived from `score` --
            "none" (score 0), "low" (1), "medium" (2), or "high" (3+).
            Not a probability; a coarse, auditable signal only.
        all_scores: every configured domain's match count, keyed by
            domain name, including domains that scored 0 -- lets a
            caller see not just the winner but how close/far every
            other domain was, e.g. to spot an ambiguous clause where
            two domains scored close to each other.
        reason: a short, human-readable explanation of the decision,
            naming the winning domain and score and (when relevant) the
            runner-up -- or explaining why no domain was detected.
    """

    search_query: str
    title: str | None
    matches: list[str]
    domain: str | None
    score: int
    confidence: str
    all_scores: dict[str, int]
    reason: str


@dataclass(frozen=True, slots=True)
class _DomainRule:
    """One CFR-domain keyword table entry.

    `keywords` are matched as whole-word/whole-phrase, case-insensitive
    patterns against the clause's raw (title + text) content -- see the
    module docstring's "Matching note" for why this is a regex
    word-boundary match rather than a plain substring check.
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


def _compile_keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a single keyword/phrase into a case-insensitive,
    word-boundary-bounded regex.

    `\\b` at each end matches on a transition between a "word" (letter/
    digit/underscore) character and a non-word character (or string
    edge). For a multi-word phrase like "hazardous waste", the internal
    space is untouched -- `\\b` only anchors the two outer edges of the
    whole phrase, so "hazardous waste" matches as that exact phrase and
    not, say, "hazardous wasteful" or "non-hazardous waste-adjacent".
    `re.escape` guards against any keyword ever containing a regex
    metacharacter.
    """
    return re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)


# Compiled once at import time, keyed by the raw keyword string --
# every keyword across every domain is compiled exactly once even
# though "wage" etc. only appears in one domain today; this avoids
# recompiling the same pattern on every `optimize_clause()` call, which
# matters once this runs per-clause across a multi-clause contract.
_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    keyword: _compile_keyword_pattern(keyword)
    for rule in _DOMAIN_RULES
    for keyword in rule.keywords
}


def _matched_keywords(rule: _DomainRule, haystack: str) -> list[str]:
    """Return the subset of `rule.keywords` that appear as a
    whole-word/whole-phrase, case-insensitive match in `haystack`,
    preserving the rule's own canonical keyword order (not the order
    they occur in the clause) -- so `search_query` construction
    downstream is deterministic regardless of clause phrasing.
    """
    return [kw for kw in rule.keywords if _KEYWORD_PATTERNS[kw].search(haystack)]


def _score_all_domains(haystack: str) -> list[tuple[_DomainRule, list[str]]]:
    """Score every configured domain against `haystack`, in table
    order. Returns a `(rule, matched_keywords)` pair per domain --
    including domains with zero matches -- so callers have full,
    unfiltered visibility into every domain's result, not just the
    winner. This is the basis for both the winner selection and the
    `all_scores` audit field.
    """
    return [(rule, _matched_keywords(rule, haystack)) for rule in _DOMAIN_RULES]


def _confidence_for_score(score: int) -> str:
    """Map a raw match count to a coarse, deterministic confidence
    tier. Not a probability or a statistically calibrated value --
    just a stable, auditable bucket: "none" (0 matches), "low" (1),
    "medium" (2), "high" (3 or more).
    """
    if score <= 0:
        return "none"
    if score == 1:
        return "low"
    if score == 2:
        return "medium"
    return "high"


def _dedupe_contained(keywords: list[str]) -> list[str]:
    """Drop any keyword that is itself a substring of another matched
    keyword (e.g. drop bare "waste" when "hazardous waste" also
    matched) -- avoids redundant repeated terms in the built query
    string. Order-preserving; only used for `search_query`
    construction, never for the `matches` list returned to the caller
    (which stays the full, unfiltered set for audit/logging purposes).
    """
    return [kw for kw in keywords if not any(kw != other and kw in other for other in keywords)]


def _build_reason(
    winner: _DomainRule | None,
    winner_score: int,
    matches: list[str],
    scored_domains: list[tuple[_DomainRule, list[str]]],
) -> str:
    """Build the human-readable `reason` audit string.

    Deliberately built from the same data already computed for
    `all_scores`/`matches` rather than re-deriving anything, so the
    explanation can never drift out of sync with the actual decision.
    """
    if winner is None:
        domain_count = len(scored_domains)
        return (
            f"No domain keywords matched any of the {domain_count} configured "
            "CFR domains; falling back to the base query unchanged."
        )

    runner_up_name: str | None = None
    runner_up_score = 0
    for rule, rule_matches in scored_domains:
        if rule is winner:
            continue
        if len(rule_matches) > runner_up_score:
            runner_up_name = rule.name
            runner_up_score = len(rule_matches)

    base = (
        f"Matched {winner_score} keyword(s) {matches} for domain "
        f"'{winner.name}' (predicted title {winner.title})."
    )
    if runner_up_name is not None:
        return base + f" Runner-up domain: '{runner_up_name}' ({runner_up_score} match(es))."
    return base + " No other domain matched any keywords."


def optimize_clause(clause_title: str, clause_text: str, base_query: str) -> OptimizedQueryResult:
    """Run the deterministic CFR-domain optimizer for one clause.

    Args:
        clause_title: the clause's own heading/title text (e.g.
            `Clause.title`), included in domain matching alongside the
            body since a heading like "Hazardous Waste Disposal" is
            often the clearest domain signal in the whole clause. Must
            be the raw, un-stripped text -- this function is meant to
            run *before* `build_search_query()`'s boilerplate/stopword
            removal, so multi-word phrases stay intact.
        clause_text: the clause's full raw body text (e.g.
            `Clause.text`), same caveat as `clause_title`.
        base_query: the already-built `build_search_query(clause)`
            output -- used as the fallback `search_query` when no
            domain is confidently detected, so this function never
            makes the query *worse* than the existing pipeline, only
            potentially better.

    Returns:
        See `OptimizedQueryResult` for the full field-by-field
        description of the audit trail returned alongside the query
        and title prediction.
    """
    haystack = f"{clause_title} {clause_text}"
    scored_domains = _score_all_domains(haystack)
    all_scores = {rule.name: len(matches) for rule, matches in scored_domains}

    winner: _DomainRule | None = None
    winner_matches: list[str] = []
    winner_score = 0
    for rule, matches in scored_domains:
        if len(matches) > winner_score:
            winner = rule
            winner_matches = matches
            winner_score = len(matches)

    reason = _build_reason(winner, winner_score, winner_matches, scored_domains)

    if winner is None:
        return OptimizedQueryResult(
            search_query=base_query,
            title=None,
            matches=[],
            domain=None,
            score=0,
            confidence="none",
            all_scores=all_scores,
            reason=reason,
        )

    search_query = " ".join(_dedupe_contained(winner_matches))
    return OptimizedQueryResult(
        search_query=search_query,
        title=winner.title,
        matches=winner_matches,
        domain=winner.name,
        score=winner_score,
        confidence=_confidence_for_score(winner_score),
        all_scores=all_scores,
        reason=reason,
    )