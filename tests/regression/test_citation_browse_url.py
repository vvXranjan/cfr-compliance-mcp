"""Regression tests for the eCFR browse URL construction.

The section-level browse URL previously used the CFR title in place of
the part number (``section-40.10`` instead of ``section-261.10``),
which produced an invalid link for every multi-part title. These tests
pin the correct URL pattern.
"""

from cfr_compliance_mcp.parsing.xml_parser import Citation


def test_section_url_uses_part_number() -> None:
    citation = Citation(title=40, date="2026-08-17", part="261", section="10")
    assert citation.url == "https://www.ecfr.gov/current/title-40/section-261.10"


def test_part_url() -> None:
    citation = Citation(title=40, date="2026-08-17", part="261")
    assert citation.url == "https://www.ecfr.gov/current/title-40/part-261"


def test_title_only_url() -> None:
    citation = Citation(title=40, date="2026-08-17")
    assert citation.url == "https://www.ecfr.gov/current/title-40"


def test_dotted_section() -> None:
    citation = Citation(title=40, date="2026-08-17", part="257", section="257.3")
    assert citation.url == "https://www.ecfr.gov/current/title-40/section-257.3"


def test_explicit_url_not_overwritten() -> None:
    citation = Citation(
        title=40,
        date="2026-08-17",
        part="261",
        section="10",
        url="https://example.com/custom",
    )
    assert citation.url == "https://example.com/custom"

def test_section_url_parts_are_required_for_section() -> None:
    """A section without its part cannot produce a valid section URL."""
    citation = Citation(title=40, date="2026-08-17", section="10")
    assert citation.url == "https://www.ecfr.gov/current/title-40"