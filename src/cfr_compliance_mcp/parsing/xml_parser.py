"""Raw eCFR XML -> clean plain text + structured citation metadata.

Architecture summary:
    - `EcfrClient.retrieve_section/retrieve_part/retrieve_title` (clients
      layer) return raw XML strings from eCFR's `/full/` endpoints. This
      module's only job is turning that XML into something an LLM can
      read cleanly, plus a citation object the eventual compliance
      report needs for its audit trail.
    - This module makes **no network calls** and knows **nothing about
      caching** — it is a pure transformation, matching the dependency
      graph in `PROJECT_HANDOFF.md` (depends only on `exceptions.py` and
      `logging_config.py`).
    - Citation values (title/part/section/date) are **passed in
      explicitly by the caller**, not scraped from the XML. The caller
      (the tool layer, built next) already knows exactly which
      title/part/section/date it requested — re-deriving that from the
      XML's own attributes would be fragile and redundant. The one
      thing we *do* extract from the XML itself is the human-readable
      heading (e.g. "\u00a7 261.10 Criteria for identifying..."), which
      adds real value beyond the bare numbers the caller already has.
    - Parsing is tag-agnostic by design: rather than hardcoding an
      exhaustive list of eCFR's paragraph-like tags (`P`, `FP`,
      `EXTRACT`, `NOTE`, `CITA`, ...), this module extracts the direct
      text of *every* element that has any, in document order. This is
      simpler, more robust to eCFR schema variations across titles, and
      cannot silently drop legal text because a tag name wasn't on a
      hardcoded whitelist.
    - Parsing uses `xml.etree.ElementTree.iterparse` (a streaming
      parser) rather than `ET.fromstring` (which builds the entire DOM
      in memory at once), and calls `elem.clear()` on each element once
      its text has been extracted. This bounds memory use even for a
      full Title-level payload (some titles, e.g. Title 40, are large
      enough that a non-streaming parse would be wasteful).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field

from cfr_compliance_mcp.exceptions import XmlParsingError
from cfr_compliance_mcp.logging_config import get_logger

logger = get_logger(__name__)

__all__ = ["Citation", "ParsedRegulation", "parse_regulation_xml"]

# Collapse 3-or-more consecutive newlines down to a single blank line,
# so the output reads as clean paragraphs rather than having ragged
# vertical whitespace from empty/structural XML elements.
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

# eCFR's source XML is itself line-wrapped and indented for human
# readability in the raw file (e.g. a <P> element's text often contains
# embedded "\n  " from how the source document is formatted). That
# formatting is meaningless to us and must not leak into the output as
# stray line breaks / indentation inside what should be one paragraph —
# collapse any run of internal whitespace (including newlines) to a
# single space before we do our own line-based formatting.
_INTERNAL_WHITESPACE = re.compile(r"\s+")

# The eCFR schema's heading tag. Its text is both included in the clean
# output (so the LLM sees section headings in context) and pulled out
# separately as `Citation.heading` (so callers get it as structured data
# too, without having to re-parse the clean text to find it).
_HEADING_TAG = "HEAD"


@dataclass(slots=True)
class Citation:
    """Structured citation metadata for a piece of retrieved CFR text.

    This is what makes the eventual compliance report auditable — every
    piece of regulation text handed to the LLM must be traceable back
    to an exact title/part/section/date.
    """

    title: int
    date: str
    part: str | None = None
    section: str | None = None
    heading: str | None = None
    url: str = field(default="")

    def __post_init__(self) -> None:
        if not self.url:
            self.url = self._build_browse_url()

    def _build_browse_url(self) -> str:
        """Best-effort human-readable eCFR browse URL for this citation.

        NOTE: this URL is constructed from eCFR's publicly documented
        browse-page URL pattern (`ecfr.gov/current/title-X/part-Y/section-X.Y`)
        but has **not** been verified against a live request in this
        session, since this sandbox has no network access. Treat it as
        a best-effort convenience link for humans reviewing a
        compliance report, not as a value the system depends on for
        correctness — the authoritative identifier is the
        (title, part, section, date) tuple, not this URL.
        """
        base = f"https://www.ecfr.gov/current/title-{self.title}"
        if self.part is None:
            return base
        url = f"{base}/part-{self.part}"
        if self.section is not None:
            url = f"{base}/section-{self.title}.{self.section}"
        return url

    def as_dict(self) -> dict[str, str | int | None]:
        """Structured dict form, suitable for embedding directly in a
        tool's JSON response (see `models/responses.py`, built next)."""
        return {
            "title": self.title,
            "part": self.part,
            "section": self.section,
            "date": self.date,
            "heading": self.heading,
            "url": self.url,
        }


@dataclass(slots=True)
class ParsedRegulation:
    """The result of parsing one piece of raw eCFR XML: clean text plus
    its citation."""

    text: str
    citation: Citation


def _iter_text_blocks(raw_xml: str) -> Iterator[tuple[str, str]]:
    """Yield `(tag, text)` for every XML element with non-empty direct
    text content, in document order, using a streaming parser.

    Uses `iterparse` + `elem.clear()` rather than a full-DOM parse so
    memory use stays bounded even for large Title-level XML payloads —
    each element's text is extracted and the element is immediately
    cleared, freeing the memory for that subtree before moving on.
    """
    try:
        for _event, elem in ET.iterparse(io.StringIO(raw_xml), events=("end",)):
            if elem.text and elem.text.strip():
                collapsed = _INTERNAL_WHITESPACE.sub(" ", elem.text.strip())
                yield elem.tag, collapsed
            # Free this element's text/children/attributes now that
            # we've extracted what we need. Ancestors keep a (now-empty)
            # reference to this element, but the heavy payload -- text
            # content -- is released immediately rather than held until
            # the whole document has been walked.
            elem.clear()
    except ET.ParseError as exc:
        raise XmlParsingError(f"Failed to parse eCFR XML: {exc}") from exc


def _build_clean_text(raw_xml: str) -> tuple[str, str | None]:
    """Convert raw XML into (clean_text, first_heading_or_none).

    `first_heading` is the text of the first `HEAD` element encountered
    — for a `retrieve_section` result this is the section's own
    heading (e.g. "\u00a7 261.10 Criteria for identifying the
    characteristics of hazardous waste."); for a `retrieve_part` or
    `retrieve_title` result it's the top-level heading for that part/title.
    """
    lines: list[str] = []
    first_heading: str | None = None

    for tag, text in _iter_text_blocks(raw_xml):
        if tag == _HEADING_TAG:
            if first_heading is None:
                first_heading = text
            lines.append("")  # blank line before each heading, for readability
            lines.append(text)
        else:
            lines.append(text)

    clean_text = "\n".join(lines).strip()
    clean_text = _EXCESS_BLANK_LINES.sub("\n\n", clean_text)
    return clean_text, first_heading


def parse_regulation_xml(
    raw_xml: str,
    *,
    title: int,
    date: str,
    part: str | None = None,
    section: str | None = None,
) -> ParsedRegulation:
    """Parse raw eCFR XML into clean text plus structured citation metadata.

    Args:
        raw_xml: the raw XML string returned by `EcfrClient.retrieve_section`,
            `retrieve_part`, or `retrieve_title`.
        title: the CFR title number the XML was retrieved for. Passed in
            explicitly by the caller (not re-derived from the XML), since
            the caller already knows exactly what it requested.
        date: the as-of date used for the retrieval (resolved or explicit).
        part: the CFR part number, if the XML was scoped to a part or section.
        section: the CFR section number, if the XML was scoped to a section.

    Returns:
        A `ParsedRegulation` with `.text` (clean, LLM-readable plain text)
        and `.citation` (a `Citation` with title/part/section/date/heading/url).

    Raises:
        XmlParsingError: if `raw_xml` is empty/whitespace-only, or is not
            well-formed XML.
    """
    if not raw_xml or not raw_xml.strip():
        raise XmlParsingError(
            f"Cannot parse empty XML content for title {title}"
            f"{f' part {part}' if part else ''}{f' section {section}' if section else ''}."
        )

    logger.debug(
        "Parsing eCFR XML",
        extra={"title": title, "part": part, "section": section, "date": date},
    )

    clean_text, heading = _build_clean_text(raw_xml)

    if not clean_text:
        # Well-formed XML that nonetheless yielded no extractable text
        # content is treated as a parsing failure, not a valid empty
        # result -- a real CFR section/part/title always has some body
        # text, so an empty result here almost certainly indicates the
        # eCFR schema used a structure this parser didn't anticipate.
        raise XmlParsingError(
            f"eCFR XML for title {title}"
            f"{f' part {part}' if part else ''}{f' section {section}' if section else ''} "
            "parsed successfully as XML but yielded no text content."
        )

    citation = Citation(title=title, date=date, part=part, section=section, heading=heading)

    logger.info(
        "Parsed eCFR XML",
        extra={
            "title": title,
            "part": part,
            "section": section,
            "text_length": len(clean_text),
            "heading": heading,
        },
    )

    return ParsedRegulation(text=clean_text, citation=citation)
