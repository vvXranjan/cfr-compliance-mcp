"""tests/test_optimizer_manual.py

Manual/standalone test suite for `agent/cfr_query_optimizer.py`.

"Manual" here means this file is meant to be run directly and read by
a human -- `python -m tests.test_optimizer_manual` (or
`python tests/test_optimizer_manual.py`) prints a pass/fail summary for
every case, including the actual `OptimizedQueryResult` returned, not
just an assertion result. It is also a normal `unittest.TestCase`
suite, so it collects and runs cleanly under `pytest tests/` or
`python -m unittest` for CI, without needing pytest installed --
stdlib `unittest` only, consistent with the optimizer module itself
being standard-library-only.

Coverage:
    - One positive-detection case per configured domain (environmental,
      employment, safety, labor, procurement).
    - The no-domain fallback path (search_query falls back to
      base_query unchanged, title/domain are None, confidence "none").
    - The word-boundary false-positive regression cases this revision
      of the optimizer fixed ("separate" no longer matching "epa",
      "embrace" no longer matching "race", "sewage" no longer matching
      "wage").
    - Dedup behavior ("waste" dropped from the query when "hazardous
      waste" also matched).
    - Deterministic tie-break order when two domains score equally.
    - Full audit-field shape and content (`domain`, `score`,
      `confidence`, `all_scores`, `reason`, `matches`).
    - Case-insensitivity.
    - Repeated-call determinism (same input -> byte-identical output).

Run directly:
    python -m tests.test_optimizer_manual
    python tests/test_optimizer_manual.py

Run under pytest:
    pytest tests/test_optimizer_manual.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow running this file directly (`python tests/test_optimizer_manual.py`)
# without the project being installed/on PYTHONPATH -- insert the repo
# root (parent of this file's `tests/` directory) at the front of
# sys.path before importing the `agent` package. No-op when the project
# is already installed/importable (e.g. under pytest with the repo root
# as the working directory), since inserting a path that's already
# resolvable simply makes `agent` resolve the same way.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.cfr_query_optimizer import optimize_clause  # noqa: E402


class TestDomainDetection(unittest.TestCase):
    """One positive case per configured domain, using realistic clause
    wording rather than bare keyword lists -- proves the optimizer
    works against prose, not just its own keyword table."""

    def test_environmental_domain(self) -> None:
        result = optimize_clause(
            clause_title="Hazardous Waste Management",
            clause_text=(
                "Company must properly manage and dispose hazardous waste "
                "according to applicable laws, including EPA regulations on "
                "chemical pollution."
            ),
            base_query="manage dispose hazardous waste applicable laws epa regulations chemical pollution",  # noqa: E501
        )
        self.assertEqual(result["domain"], "environmental")
        self.assertEqual(result["title"], "40")
        self.assertIn("hazardous waste", result["matches"])
        self.assertGreaterEqual(result["score"], 3)
        self.assertEqual(result["confidence"], "high")

    def test_employment_domain(self) -> None:
        result = optimize_clause(
            clause_title="Equal Employment Opportunity",
            clause_text=(
                "Contractor shall not engage in discrimination on the basis of "
                "race or gender and shall maintain an affirmative action "
                "program for equal opportunity in employment."
            ),
            base_query="engage discrimination basis race gender maintain affirmative action program equal opportunity employment",  # noqa: E501
        )
        self.assertEqual(result["domain"], "employment")
        self.assertEqual(result["title"], "41")
        self.assertIn("discrimination", result["matches"])
        self.assertIn("equal opportunity", result["matches"])

    def test_safety_domain(self) -> None:
        result = optimize_clause(
            clause_title="Construction Site Safety",
            clause_text=(
                "Contractor must ensure workplace safety and construction "
                "safety standards are met, including fall protection and "
                "scaffold requirements per OSHA hazard communication rules."
            ),
            base_query="ensure workplace safety construction safety standards met fall protection scaffold requirements osha hazard communication rules",  # noqa: E501
        )
        self.assertEqual(result["domain"], "safety")
        self.assertEqual(result["title"], "29")
        self.assertIn("fall protection", result["matches"])

    def test_labor_domain(self) -> None:
        result = optimize_clause(
            clause_title="Certified Payroll Requirements",
            clause_text=(
                "Laborer and mechanic wage rates must be reported via "
                "certified payroll pursuant to prevailing wage law."
            ),
            base_query="laborer mechanic wage rates reported certified payroll pursuant prevailing wage law",  # noqa: E501
        )
        self.assertEqual(result["domain"], "labor")
        self.assertEqual(result["title"], "29")
        self.assertIn("certified payroll", result["matches"])

    def test_procurement_domain(self) -> None:
        result = optimize_clause(
            clause_title="Subcontractor Flow-Down",
            clause_text=(
                "This is a federal contract and all subcontractor agreements "
                "under this government contract must include the same terms."
            ),
            base_query="federal contract subcontractor agreements government contract include terms",  # noqa: E501
        )
        self.assertEqual(result["domain"], "procurement")
        self.assertEqual(result["title"], "48")
        self.assertIn("subcontractor", result["matches"])


class TestNoDomainFallback(unittest.TestCase):
    """When nothing in the fixed keyword table matches, the optimizer
    must fall back to the caller's base_query untouched -- it must
    never make the query worse or invent a title with no evidence."""

    def test_no_domain_falls_back_to_base_query(self) -> None:
        base_query = "clause has no domain specific vocabulary whatsoever"
        result = optimize_clause(
            clause_title="Miscellaneous",
            clause_text="This clause has no domain-specific vocabulary whatsoever.",
            base_query=base_query,
        )
        self.assertIsNone(result["domain"])
        self.assertIsNone(result["title"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["confidence"], "none")
        self.assertEqual(result["search_query"], base_query)
        self.assertIn("No domain keywords matched", result["reason"])

    def test_all_scores_present_even_with_no_winner(self) -> None:
        result = optimize_clause("Miscellaneous", "Nothing domain-specific here.", "nothing domain specific")  # noqa: E501
        # Every configured domain must appear in all_scores, all at 0.
        self.assertEqual(
            set(result["all_scores"].keys()),
            {"environmental", "employment", "safety", "labor", "procurement"},
        )
        self.assertTrue(all(score == 0 for score in result["all_scores"].values()))


class TestWordBoundaryRegression(unittest.TestCase):
    """Regression tests for the false-positive substring-matching bug
    fixed in this revision: short/common keywords ("epa", "race",
    "wage") must NOT match when they appear only as a substring inside
    an unrelated, longer word. Plain `kw in haystack` matching (the
    optimizer's original implementation) would have failed every case
    in this class.
    """

    def test_epa_does_not_match_inside_separate(self) -> None:
        result = optimize_clause(
            clause_title="Confidentiality",
            clause_text="The parties agree to keep their obligations separate under this agreement.",  # noqa: E501
            base_query="parties agree keep obligations separate agreement",
        )
        self.assertIsNone(result["domain"])
        self.assertEqual(result["all_scores"]["environmental"], 0)

    def test_race_does_not_match_inside_embrace(self) -> None:
        result = optimize_clause(
            clause_title="Confidentiality",
            clause_text="Each party shall embrace transparency in all dealings.",
            base_query="party embrace transparency dealings",
        )
        self.assertIsNone(result["domain"])
        self.assertEqual(result["all_scores"]["employment"], 0)

    def test_wage_does_not_match_inside_sewage(self) -> None:
        result = optimize_clause(
            clause_title="Utilities",
            clause_text="The building requires proper sewage handling.",
            base_query="building requires proper sewage handling",
        )
        self.assertIsNone(result["domain"])
        self.assertEqual(result["all_scores"]["labor"], 0)

    def test_whole_word_wage_still_matches(self) -> None:
        # Sanity check paired with the sewage case above: the fix must
        # not overcorrect into never matching "wage" at all.
        result = optimize_clause(
            clause_title="Wage Rates",
            clause_text="The prevailing wage must be paid to every laborer on site.",
            base_query="prevailing wage paid laborer site",
        )
        self.assertEqual(result["domain"], "labor")
        self.assertIn("wage", result["matches"])


class TestDedupeAndQueryConstruction(unittest.TestCase):
    """The built `search_query` must not contain a keyword that is a
    redundant substring of another matched keyword (e.g. bare "waste"
    alongside "hazardous waste"), even though `matches` itself keeps
    the full, undeduped set for audit purposes."""

    def test_waste_deduped_from_query_but_kept_in_matches(self) -> None:
        result = optimize_clause(
            clause_title="Hazardous Waste Management",
            clause_text=(
                "Company must properly manage and dispose hazardous waste "
                "according to applicable laws, including EPA regulations on "
                "chemical pollution."
            ),
            base_query="irrelevant fallback",
        )
        # Bare "waste" must not appear as its own separate phrase
        # alongside "hazardous waste" -- the phrase "hazardous waste"
        # legitimately contains the substring "waste" once as part of
        # itself, so the correct check is "the phrase 'waste' is not
        # ALSO present as a distinct match in the query", not a naive
        # whitespace-token check (which would wrongly flag the "waste"
        # inside "hazardous waste" itself).
        self.assertEqual(
            result["search_query"],
            "hazardous waste epa chemical pollution",
        )
        self.assertEqual(
            result["search_query"].count("waste"),
            1,
            "the word 'waste' should appear exactly once, as part of 'hazardous waste'",
        )
        self.assertIn("hazardous waste", result["matches"], "full phrase must still be in matches")
        self.assertIn("waste", result["matches"], "bare 'waste' must still be in matches (audit trail)")  # noqa: E501


class TestTieBreakDeterminism(unittest.TestCase):
    """When two domains score equally, the winner must be the
    first-listed domain in the internal table (environmental before
    employment before safety before labor before procurement) --
    every time, with no randomness."""

    def test_environmental_beats_employment_on_equal_score(self) -> None:
        # Exactly one keyword each: "waste" (environmental) and
        # "employment" (employment) -- a genuine tie.
        result = optimize_clause(
            clause_title="General",
            clause_text="This clause references waste and employment in the same sentence.",
            base_query="clause references waste employment same sentence",
        )
        self.assertEqual(result["all_scores"]["environmental"], 1)
        self.assertEqual(result["all_scores"]["employment"], 1)
        self.assertEqual(result["domain"], "environmental", "environmental must win the tie (listed first)")  # noqa: E501

    def test_result_is_repeatable_across_calls(self) -> None:
        args = (
            "Hazardous Waste",
            "Dispose of hazardous waste per EPA rules.",
            "dispose hazardous waste epa rules",
        )
        first = optimize_clause(*args)
        second = optimize_clause(*args)
        self.assertEqual(first, second, "identical input must produce byte-identical output")


class TestCaseInsensitivity(unittest.TestCase):
    def test_uppercase_clause_still_matches(self) -> None:
        result = optimize_clause(
            clause_title="HAZARDOUS WASTE DISPOSAL",
            clause_text="COMPANY MUST DISPOSE OF HAZARDOUS WASTE PER EPA RULES.",
            base_query="company must dispose hazardous waste epa rules",
        )
        self.assertEqual(result["domain"], "environmental")
        self.assertIn("hazardous waste", result["matches"])


class TestAuditFieldShape(unittest.TestCase):
    """Every field promised by `OptimizedQueryResult` must actually be
    present and correctly typed on every call, detected or not."""

    def _assert_shape(self, result: dict) -> None:
        expected_keys = {
            "search_query",
            "title",
            "matches",
            "domain",
            "score",
            "confidence",
            "all_scores",
            "reason",
        }
        self.assertEqual(set(result.keys()), expected_keys)
        self.assertIsInstance(result["search_query"], str)
        self.assertIsInstance(result["matches"], list)
        self.assertIsInstance(result["score"], int)
        self.assertIsInstance(result["confidence"], str)
        self.assertIn(result["confidence"], {"none", "low", "medium", "high"})
        self.assertIsInstance(result["all_scores"], dict)
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(result["reason"], "reason must never be an empty string")

    def test_shape_when_domain_detected(self) -> None:
        result = optimize_clause(
            "Hazardous Waste", "Dispose of hazardous waste per EPA rules.", "fallback"
        )
        self._assert_shape(result)
        self.assertIn("Matched", result["reason"])

    def test_shape_when_no_domain_detected(self) -> None:
        result = optimize_clause("Nothing", "Nothing relevant here at all.", "fallback query")
        self._assert_shape(result)
        self.assertIn("No domain keywords matched", result["reason"])

    def test_reason_mentions_runner_up_when_present(self) -> None:
        # "hazardous waste" (2 matches: "hazardous waste" + "waste")
        # outscores the single "employment" match -> environmental wins
        # with a real runner-up.
        result = optimize_clause(
            "Mixed Domain Clause",
            "This covers hazardous waste as well as general employment matters.",
            "fallback",
        )
        self.assertEqual(result["domain"], "environmental")
        self.assertIn("Runner-up", result["reason"])
        self.assertIn("employment", result["reason"])


def _print_manual_report() -> None:
    """Human-readable demo of `optimize_clause()`'s output, printed
    when this file is run directly (not under pytest/unittest's normal
    dot-per-test output) -- useful for eyeballing real audit output
    quickly without reading assertions."""
    samples = [
        ("Hazardous Waste Management", "Company must properly manage and dispose hazardous waste per EPA rules on chemical pollution."),  # noqa: E501
        ("Equal Employment Opportunity", "Contractor shall not engage in discrimination on the basis of race or gender."),  # noqa: E501
        ("Construction Site Safety", "Contractor must ensure workplace safety and fall protection per OSHA scaffold rules."),  # noqa: E501
        ("Certified Payroll", "Laborer and mechanic wage rates must be reported via certified payroll."),  # noqa: E501
        ("Subcontractor Flow-Down", "This federal contract requires all subcontractor agreements to include the same terms."),  # noqa: E501
        ("Miscellaneous", "This clause has no domain-specific vocabulary whatsoever."),
    ]
    print("\n" + "=" * 70)
    print("MANUAL REPORT: agent.cfr_query_optimizer.optimize_clause()")
    print("=" * 70)
    for title, text in samples:
        result = optimize_clause(title, text, base_query="<base_query placeholder>")
        print(f"\nClause title : {title}")
        print(f"Clause text  : {text}")
        print(f"  domain       : {result['domain']}")
        print(f"  title        : {result['title']}")
        print(f"  score        : {result['score']}  (confidence: {result['confidence']})")
        print(f"  matches      : {result['matches']}")
        print(f"  search_query : {result['search_query']!r}")
        print(f"  all_scores   : {result['all_scores']}")
        print(f"  reason       : {result['reason']}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    _print_manual_report()
    unittest.main()