# """Raw eCFR XML -> clean plain text + structured citation metadata.

# Architecture summary:
#     - `EcfrClient.retrieve_section/retrieve_part/retrieve_title` (clients
#       layer) return raw XML strings from eCFR's `/full/` endpoints. This
#       module's only job is turning that XML into something an LLM can
#       read cleanly, plus a citation object the eventual compliance
#       report needs for its audit trail.
#     - This module makes **no network calls** and knows **nothing about
#       caching** — it is a pure transformation, matching the dependency
#       graph in `PROJECT_HANDOFF.md` (depends only on `exceptions.py` and
#       `logging_config.py`).
#     - Citation values (title/part/section/date) are **passed in
#       explicitly by the caller**, not scraped from the XML. The caller
#       (the tool layer, built next) already knows exactly which
#       title/part/section/date it requested — re-deriving that from the
#       XML's own attributes would be fragile and redundant. The one
#       thing we *do* extract from the XML itself is the human-readable
#       heading (e.g. "\u00a7 261.10 Criteria for identifying..."), which
#       adds real value beyond the bare numbers the caller already has.
#     - Parsing is tag-agnostic by design: rather than hardcoding an
#       exhaustive list of eCFR's paragraph-like tags (`P`, `FP`,
#       `EXTRACT`, `NOTE`, `CITA`, ...), this module extracts the direct
#       text of *every* element that has any, in document order. This is
#       simpler, more robust to eCFR schema variations across titles, and
#       cannot silently drop legal text because a tag name wasn't on a
#       hardcoded whitelist.
#     - Parsing uses `xml.etree.ElementTree.iterparse` (a streaming
#       parser) rather than `ET.fromstring` (which builds the entire DOM
#       in memory at once), and calls `elem.clear()` on each element once
#       its text has been extracted. This bounds memory use even for a
#       full Title-level payload (some titles, e.g. Title 40, are large
#       enough that a non-streaming parse would be wasteful).
# """

# from __future__ import annotations

# import io
# import re
# import xml.etree.ElementTree as ET
# from collections.abc import Iterator
# from dataclasses import dataclass, field

# from cfr_compliance_mcp.exceptions import XmlParsingError
# from cfr_compliance_mcp.logging_config import get_logger

# logger = get_logger(__name__)

# __all__ = ["Citation", "ParsedRegulation", "parse_regulation_xml"]

# # Collapse 3-or-more consecutive newlines down to a single blank line,
# # so the output reads as clean paragraphs rather than having ragged
# # vertical whitespace from empty/structural XML elements.
# _EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

# # eCFR's source XML is itself line-wrapped and indented for human
# # readability in the raw file (e.g. a <P> element's text often contains
# # embedded "\n  " from how the source document is formatted). That
# # formatting is meaningless to us and must not leak into the output as
# # stray line breaks / indentation inside what should be one paragraph —
# # collapse any run of internal whitespace (including newlines) to a
# # single space before we do our own line-based formatting.
# _INTERNAL_WHITESPACE = re.compile(r"\s+")

# # The eCFR schema's heading tag. Its text is both included in the clean
# # output (so the LLM sees section headings in context) and pulled out
# # separately as `Citation.heading` (so callers get it as structured data
# # too, without having to re-parse the clean text to find it).
# _HEADING_TAG = "HEAD"


# @dataclass(slots=True)
# class Citation:
#     """Structured citation metadata for a piece of retrieved CFR text.

#     This is what makes the eventual compliance report auditable — every
#     piece of regulation text handed to the LLM must be traceable back
#     to an exact title/part/section/date.
#     """

#     title: int
#     date: str
#     part: str | None = None
#     section: str | None = None
#     heading: str | None = None
#     url: str = field(default="")

#     def __post_init__(self) -> None:
#         if not self.url:
#             self.url = self._build_browse_url()

#     def _build_browse_url(self) -> str:
#         """Best-effort human-readable eCFR browse URL for this citation.

#         NOTE: this URL is constructed from eCFR's publicly documented
#         browse-page URL pattern (`ecfr.gov/current/title-X/part-Y/section-X.Y`)
#         but has **not** been verified against a live request in this
#         session, since this sandbox has no network access. Treat it as
#         a best-effort convenience link for humans reviewing a
#         compliance report, not as a value the system depends on for
#         correctness — the authoritative identifier is the
#         (title, part, section, date) tuple, not this URL.
#         """
#         base = f"https://www.ecfr.gov/current/title-{self.title}"
#         if self.part is None:
#             return base
#         url = f"{base}/part-{self.part}"
#         if self.section is not None:
#             url = f"{base}/section-{self.title}.{self.section}"
#         return url

#     def as_dict(self) -> dict[str, str | int | None]:
#         """Structured dict form, suitable for embedding directly in a
#         tool's JSON response (see `models/responses.py`, built next)."""
#         return {
#             "title": self.title,
#             "part": self.part,
#             "section": self.section,
#             "date": self.date,
#             "heading": self.heading,
#             "url": self.url,
#         }


# @dataclass(slots=True)
# class ParsedRegulation:
#     """The result of parsing one piece of raw eCFR XML: clean text plus
#     its citation."""

#     text: str
#     citation: Citation


# def _iter_text_blocks(raw_xml: str) -> Iterator[tuple[str, str]]:
#     """Yield `(tag, text)` for every XML element with non-empty direct
#     text content, in document order, using a streaming parser.

#     Uses `iterparse` + `elem.clear()` rather than a full-DOM parse so
#     memory use stays bounded even for large Title-level XML payloads —
#     each element's text is extracted and the element is immediately
#     cleared, freeing the memory for that subtree before moving on.
#     """
#     try:
#         for _event, elem in ET.iterparse(io.StringIO(raw_xml), events=("end",)):
#             if elem.text and elem.text.strip():
#                 collapsed = _INTERNAL_WHITESPACE.sub(" ", elem.text.strip())
#                 yield elem.tag, collapsed
#             # Free this element's text/children/attributes now that
#             # we've extracted what we need. Ancestors keep a (now-empty)
#             # reference to this element, but the heavy payload -- text
#             # content -- is released immediately rather than held until
#             # the whole document has been walked.
#             elem.clear()
#     except ET.ParseError as exc:
#         raise XmlParsingError(f"Failed to parse eCFR XML: {exc}") from exc


# def _build_clean_text(raw_xml: str) -> tuple[str, str | None]:
#     """Convert raw XML into (clean_text, first_heading_or_none).

#     `first_heading` is the text of the first `HEAD` element encountered
#     — for a `retrieve_section` result this is the section's own
#     heading (e.g. "\u00a7 261.10 Criteria for identifying the
#     characteristics of hazardous waste."); for a `retrieve_part` or
#     `retrieve_title` result it's the top-level heading for that part/title.
#     """
#     lines: list[str] = []
#     first_heading: str | None = None

#     for tag, text in _iter_text_blocks(raw_xml):
#         if tag == _HEADING_TAG:
#             if first_heading is None:
#                 first_heading = text
#             lines.append("")  # blank line before each heading, for readability
#             lines.append(text)
#         else:
#             lines.append(text)

#     clean_text = "\n".join(lines).strip()
#     clean_text = _EXCESS_BLANK_LINES.sub("\n\n", clean_text)
#     return clean_text, first_heading


# def parse_regulation_xml(
#     raw_xml: str,
#     *,
#     title: int,
#     date: str,
#     part: str | None = None,
#     section: str | None = None,
# ) -> ParsedRegulation:
#     """Parse raw eCFR XML into clean text plus structured citation metadata.

#     Args:
#         raw_xml: the raw XML string returned by `EcfrClient.retrieve_section`,
#             `retrieve_part`, or `retrieve_title`.
#         title: the CFR title number the XML was retrieved for. Passed in
#             explicitly by the caller (not re-derived from the XML), since
#             the caller already knows exactly what it requested.
#         date: the as-of date used for the retrieval (resolved or explicit).
#         part: the CFR part number, if the XML was scoped to a part or section.
#         section: the CFR section number, if the XML was scoped to a section.

#     Returns:
#         A `ParsedRegulation` with `.text` (clean, LLM-readable plain text)
#         and `.citation` (a `Citation` with title/part/section/date/heading/url).

#     Raises:
#         XmlParsingError: if `raw_xml` is empty/whitespace-only, or is not
#             well-formed XML.
#     """
#     if not raw_xml or not raw_xml.strip():
#         raise XmlParsingError(
#             f"Cannot parse empty XML content for title {title}"
#             f"{f' part {part}' if part else ''}{f' section {section}' if section else ''}."
#         )

#     logger.debug(
#         "Parsing eCFR XML",
#         extra={"title": title, "part": part, "section": section, "date": date},
#     )

#     clean_text, heading = _build_clean_text(raw_xml)

#     if not clean_text:
#         # Well-formed XML that nonetheless yielded no extractable text
#         # content is treated as a parsing failure, not a valid empty
#         # result -- a real CFR section/part/title always has some body
#         # text, so an empty result here almost certainly indicates the
#         # eCFR schema used a structure this parser didn't anticipate.
#         raise XmlParsingError(
#             f"eCFR XML for title {title}"
#             f"{f' part {part}' if part else ''}{f' section {section}' if section else ''} "
#             "parsed successfully as XML but yielded no text content."
#         )

#     citation = Citation(title=title, date=date, part=part, section=section, heading=heading)

#     logger.info(
#         "Parsed eCFR XML",
#         extra={
#             "title": title,
#             "part": part,
#             "section": section,
#             "text_length": len(clean_text),
#             "heading": heading,
#         },
#     )

#     return ParsedRegulation(text=clean_text, citation=citation)







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
      (the tool layer) already knows exactly which title/part/section/
      date it requested — re-deriving that from the XML's own
      attributes would be fragile and redundant. The one thing we *do*
      extract from the XML itself is the human-readable heading (e.g.
      "\u00a7 261.10 Criteria for identifying..."), which adds real
      value beyond the bare numbers the caller already has.
    - NODE FILTERING (fixes a real retrieval-accuracy bug): a
      `retrieve_part`/`retrieve_section` call must return content for
      the *requested* part/section, not merely the first PART/SECTION
      element encountered in the payload. Earlier versions of this
      module extracted every text-bearing element in document order
      regardless of which part/section it belonged to — if eCFR's XML
      for a scoped request ever contains more than just the target node
      (surrounding chapter context, or an unexpectedly title-wide
      payload), that produced wrong results silently (e.g. `title=40,
      part=261` returning PART 1's text). When `part`/`section` is
      given, this module now does a DOM parse (`ET.fromstring`) and
      locates the specific PART/SECTION element whose own number
      matches what was requested, then extracts text *only* from that
      element's subtree. If no matching node is found, that's a hard
      `XmlParsingError` — never a silent fallback to unrelated content.
    - Node identification is tag-agnostic and matches on eCFR's
      documented `TYPE`/`N` attributes (`TYPE="PART"`/`TYPE="SECTION"`,
      `N="<number>"`), falling back to parsing the element's own `HEAD`
      text (e.g. "PART 261\u2014...", "\u00a7 261.10 ...") when those
      attributes are absent. NOTE: this attribute/heading schema is
      based on the publicly documented eCFR bulk-XML DTD shape but has
      **not been live-verified** against a real eCFR response in this
      build environment (no network access this session) — same
      disclosed-risk pattern as the rest of this codebase. First thing
      to confirm once network access is available: run this against a
      real `retrieve_part(40, "261")`/`retrieve_section(41, "60-1",
      "60-1.4")` response and check the `matched:` log line.
    - For a whole-title retrieval (`part` and `section` both `None`,
      i.e. `retrieve_title`), there is no node to filter to — the
      original memory-bounded streaming extraction
      (`xml.etree.ElementTree.iterparse` + `elem.clear()`) is kept
      unchanged for that path, since full-title payloads are the case
      that streaming was specifically built for. The DOM-parse path is
      only used when there's an actual node to search for, and
      `retrieve_part`/`retrieve_section` payloads are expected to be
      far smaller than a full title even when (per the bug above) they
      currently contain more than just the target node.
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

# eCFR's documented node-type attribute values for the two node kinds
# this module ever needs to locate directly (PARTs and SECTIONs).
# NOT live-verified this session -- see module docstring.
_PART_TYPE = "PART"
_SECTION_TYPE = "SECTION"

# Fallback matchers against a node's own HEAD text, used only when that
# node's TYPE attribute doesn't identify it (schema variation) --
# "PART 261\u2014IDENTIFICATION AND LISTING..." -> num group "261";
# "\u00a7 60-1.4 Equal Opportunity Clause." -> num group "60-1.4".
_PART_HEAD_RE = re.compile(r"^PART\s+(?P<num>[\w-]+)", re.IGNORECASE)
_SECTION_HEAD_RE = re.compile(r"^\u00a7\s*(?P<num>[\w.\-]+)")


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


# ---------------------------------------------------------------------------
# Whole-document streaming extraction (used only for retrieve_title,
# where there is no specific PART/SECTION node to filter to).
# ---------------------------------------------------------------------------


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
    """Convert raw XML into (clean_text, first_heading_or_none) by
    walking the *entire* document. Only used for whole-title retrieval,
    where there's no single node to scope to and every heading in the
    payload is legitimately part of the result.
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


# ---------------------------------------------------------------------------
# Targeted node lookup (used for retrieve_part / retrieve_section) --
# this is the fix: locate the element that actually matches the
# requested part/section number, never just "the first PART in the
# document".
# ---------------------------------------------------------------------------


def _direct_head_text(elem: ET.Element) -> str | None:
    """Text of `elem`'s own direct `HEAD` child, or None if it has
    none. Deliberately direct-child only (`elem.find(_HEADING_TAG)`,
    not `.//HEAD`) so a part's heading is never confused with a
    heading belonging to one of its child sections."""
    head = elem.find(_HEADING_TAG)
    if head is None or not head.text:
        return None
    return _INTERNAL_WHITESPACE.sub(" ", head.text.strip())


def _node_number(elem: ET.Element, expected_type: str, heading_re: re.Pattern[str]) -> str | None:
    """Return this element's own PART/SECTION number if it is a node of
    `expected_type` -- from its `TYPE`/`N` attributes when present,
    falling back to parsing its own `HEAD` text otherwise. Returns None
    if this element doesn't identify as `expected_type` at all (the
    normal case for the vast majority of elements walked)."""
    attrib_type = (elem.get("TYPE") or "").upper()
    if attrib_type == expected_type:
        return elem.get("N")

    if attrib_type:
        # Has a TYPE attribute, just not this one -- definitely not a
        # match, don't fall through to a heading-text guess that could
        # produce a false positive on an unrelated element.
        return None

    head_text = _direct_head_text(elem)
    if head_text is None:
        return None
    match = heading_re.match(head_text)
    return match.group("num") if match else None


def _find_all(scope: ET.Element, expected_type: str, heading_re: re.Pattern[str], requested_num: str) -> list[ET.Element]:
    """All elements within `scope` (inclusive) whose own number equals
    `requested_num` (case-insensitive) and whose type is `expected_type`."""
    target = requested_num.strip().upper()
    return [
        elem
        for elem in scope.iter()
        if (num := _node_number(elem, expected_type, heading_re)) is not None
        and num.strip().upper() == target
    ]


def _find_target_node(
    root: ET.Element, *, part: str | None, section: str | None
) -> tuple[ET.Element | None, str | None, str | None]:
    """Locate the element for the requested part and/or section.

    A section lookup is scoped to *within* the matched part element
    (not the whole document), so a same-numbered section belonging to a
    different part can't be picked up by accident -- this is what fixes
    the "wrong chapter" failure mode, not just uniqueness of the section
    number alone.

    Returns `(element, resolved_part, resolved_section)`, or
    `(None, None, None)` if no matching node was found -- callers must
    treat that as a hard failure, never fall back to "closest" content.
    """
    scope = root
    resolved_part: str | None = None
    resolved_section: str | None = None

    if part is not None:
        matches = _find_all(root, _PART_TYPE, _PART_HEAD_RE, part)
        if not matches:
            return None, None, None
        if len(matches) > 1:
            logger.warning(
                "Multiple PART elements matched N=%r in returned XML; using the first",
                part,
            )
        scope = matches[0]
        resolved_part = _node_number(scope, _PART_TYPE, _PART_HEAD_RE) or part
        if section is None:
            return scope, resolved_part, None

    if section is not None:
        matches = _find_all(scope, _SECTION_TYPE, _SECTION_HEAD_RE, section)
        if not matches:
            return None, None, None
        if len(matches) > 1:
            logger.warning(
                "Multiple SECTION elements matched N=%r in returned XML; using the first",
                section,
            )
        target = matches[0]
        resolved_section = _node_number(target, _SECTION_TYPE, _SECTION_HEAD_RE) or section
        return target, resolved_part, resolved_section

    return None, None, None


def _extract_text_from_node(elem: ET.Element) -> tuple[str, str | None]:
    """Same tag-agnostic text extraction as `_build_clean_text`, but
    scoped to `elem`'s own subtree only -- this is what actually
    prevents a `retrieve_part`/`retrieve_section` result from including
    text that belongs to a different part/section in the same payload.
    """
    lines: list[str] = []
    own_heading: str | None = None

    for sub in elem.iter():
        text = sub.text
        if text and text.strip():
            collapsed = _INTERNAL_WHITESPACE.sub(" ", text.strip())
            if sub.tag == _HEADING_TAG:
                if own_heading is None:
                    own_heading = collapsed
                lines.append("")
                lines.append(collapsed)
            else:
                lines.append(collapsed)

    clean_text = "\n".join(lines).strip()
    clean_text = _EXCESS_BLANK_LINES.sub("\n\n", clean_text)
    return clean_text, own_heading


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


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
            When given, the returned text is filtered to *that specific
            part's* subtree, not the first PART element in the payload.
        section: the CFR section number, if the XML was scoped to a section.
            When given (along with `part`), the returned text is filtered
            to that specific section, scoped within the matched part.

    Returns:
        A `ParsedRegulation` with `.text` (clean, LLM-readable plain text)
        and `.citation` (a `Citation` with title/part/section/date/heading/url).

    Raises:
        XmlParsingError: if `raw_xml` is empty/whitespace-only, is not
            well-formed XML, or (when `part`/`section` is given) no
            element in the XML actually matches the requested
            part/section number.
    """
    if not raw_xml or not raw_xml.strip():
        raise XmlParsingError(
            f"Cannot parse empty XML content for title {title}"
            f"{f' part {part}' if part else ''}{f' section {section}' if section else ''}."
        )

    logger.debug(
        "requested:\n   title: %s\n   part: %s\n   section: %s",
        title,
        part,
        section,
    )

    if part is None and section is None:
        clean_text, heading = _build_clean_text(raw_xml)
        matched_part: str | None = None
        matched_section: str | None = None
    else:
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError as exc:
            raise XmlParsingError(f"Failed to parse eCFR XML: {exc}") from exc

        target, matched_part, matched_section = _find_target_node(root, part=part, section=section)
        if target is None:
            # Hard failure -- never silently fall back to "the first
            # PART/SECTION we happened to find" (that was the bug).
            raise XmlParsingError(
                f"Could not locate title {title}"
                f"{f' part {part}' if part else ''}"
                f"{f' section {section}' if section else ''} "
                "in the returned eCFR XML: no PART/SECTION element matched the "
                "requested number."
            )
        clean_text, heading = _extract_text_from_node(target)

    if not clean_text:
        # Well-formed XML, a matching node was found (or none was
        # required), but it nonetheless yielded no extractable text
        # content -- treated as a parsing failure, not a valid empty
        # result, since a real CFR section/part/title always has some
        # body text.
        raise XmlParsingError(
            f"eCFR XML for title {title}"
            f"{f' part {part}' if part else ''}{f' section {section}' if section else ''} "
            "parsed successfully as XML but yielded no text content."
        )

    logger.info(
        "matched:\n   title: %s\n   part: %s\n   section: %s\n   heading: %s",
        title,
        matched_part,
        matched_section,
        heading,
    )

    citation = Citation(title=title, date=date, part=part, section=section, heading=heading)

    return ParsedRegulation(text=clean_text, citation=citation)