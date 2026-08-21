import re
from pathlib import Path

from pypdf import PdfReader

from .models import Clause

# Matches section headings such as:
#   SECTION 1. ENVIRONMENTAL WASTE DISPOSAL
#   Section 2. Worker Safety
#   SECTION 3 Hazardous Materials
# One heading per line: optional leading whitespace, "SECTION" (any case),
# a number, an optional period, then the heading text.
_SECTION_HEADING_PATTERN = re.compile(
    r"^[ \t]*(?:SECTION\s+\d+\.?|\d+\.\d+)\s+.+$",
    re.MULTILINE | re.IGNORECASE,
)

class ContractParser:
    """Simple PDF contract parser."""

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)

    def extract_text(self) -> str:
        """Extract all text from the PDF."""

        reader = PdfReader(self.pdf_path)

        pages = []

        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)

        return "\n".join(pages)

    def page_count(self) -> int:
        """Return number of pages."""

        return len(PdfReader(self.pdf_path).pages)


def split_into_clauses(text: str) -> list[Clause]:
    """Split contract text into clauses based on "SECTION n. TITLE" headings.

    Each heading (e.g. "SECTION 1. ENVIRONMENTAL WASTE DISPOSAL") becomes a
    `Clause.title`; the text between that heading and the next one (or the
    end of the document) becomes `Clause.text`. Text before the first
    heading is not included in any clause.
    """

    headings = list(_SECTION_HEADING_PATTERN.finditer(text))

    clauses: list[Clause] = []

    for index, heading in enumerate(headings):
        title = heading.group().strip()

        body_start = heading.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[body_start:body_end].strip()

        clauses.append(Clause(title=title, text=body))

    return clauses