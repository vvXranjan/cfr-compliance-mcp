"""P1 tests: version-aware retrieval metadata flow.

Covers version derivation, authoritative as-of date extraction, the
fixed `_fetch_version_history` (must return a plain dict, not a fastmcp
ToolResult), and that `retrieve_for_clause` attaches version/provenance
metadata to the resulting CfrMatch.
"""

from __future__ import annotations

from typing import Any

from agent.mcp_search import (
    CfrMatch,
    _derive_effective_version,
    _fetch_version_history,
    _retrieve_text,
    retrieve_for_clause,
)
from agent.models import Clause


class _FakeResult:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.content: list[Any] = []


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeResult:
        self.calls.append((name, arguments))
        return _FakeResult(self.responses[name])


class TestDeriveEffectiveVersion:
    def test_picks_latest_issue_date(self) -> None:
        payload = {
            "title": 40,
            "versions": [
                {"issue_date": "2020-01-01"},
                {"issue_date": "2024-05-15"},
                {"issue_date": "2026-08-01"},
            ],
        }
        assert _derive_effective_version(payload) == "2026-08-01"

    def test_returns_empty_for_no_versions(self) -> None:
        assert _derive_effective_version({"title": 40, "versions": []}) == ""
        assert _derive_effective_version(None) == ""
        assert _derive_effective_version({}) == ""

    def test_returns_empty_when_no_usable_issue_date(self) -> None:
        payload = {"title": 40, "versions": [{"other": "x"}, {"issue_date": ""}]}
        assert _derive_effective_version(payload) == ""


class TestFetchVersionHistory:
    async def test_returns_plain_dict(self) -> None:
        payload = {"title": 40, "versions": [{"issue_date": "2026-08-01"}]}
        client = _FakeClient({"get_version_history": payload})
        result = await _fetch_version_history(client, 40, "261", "10")
        assert result == payload
        assert isinstance(result, dict)
        assert client.calls == [("get_version_history", {"title": 40, "part": "261", "section": "10"})]  # noqa: E501

    async def test_error_response_raises(self) -> None:
        client = _FakeClient(
            {"get_version_history": {"error": True, "error_type": "EcfrServerError", "message": "boom", "retryable": True}}  # noqa: E501
        )
        import pytest

        with pytest.raises(RuntimeError):
            await _fetch_version_history(client, 40)


class TestRetrieveText:
    async def test_returns_date_and_heading(self) -> None:
        payload = {
            "text": "261.10 - Standards for hazardous waste.",
            "citation": {
                "title": 40,
                "part": "261",
                "section": "10",
                "date": "2026-08-17",
                "heading": "261.10 Identification and listing of hazardous waste.",
                "url": "https://www.ecfr.gov/current/title-40/section-261.10",
            },
        }
        client = _FakeClient({"retrieve_section": payload})
        citation, text, date, heading = await _retrieve_text(client, 40, "261", "10")
        assert citation == "40 CFR 261.10"
        assert text == payload["text"]
        assert date == "2026-08-17"
        assert heading == payload["citation"]["heading"]


class TestRetrieveForClause:
    async def test_attaches_version_metadata(self) -> None:
        clause = Clause(
            title="Environmental",
            text="Contractor shall properly dispose hazardous waste per EPA requirements.",
        )
        client = _FakeClient(
            {
                "search_regulations": {
                    "results": [
                        {"hierarchy": {"title": 40, "part": "261", "section": "10"}}
                    ],
                    "total_count": 1,
                    "current_page": 1,
                    "total_pages": 1,
                },
                "retrieve_section": {
                    "text": "261.10 - Standards for hazardous waste.",
                    "citation": {
                        "title": 40,
                        "part": "261",
                        "section": "10",
                        "date": "2026-08-17",
                        "heading": "261.10",
                        "url": "https://www.ecfr.gov/current/title-40/section-261.10",
                    },
                },
                "get_version_history": {
                    "title": 40,
                    "versions": [{"issue_date": "2026-07-01"}, {"issue_date": "2026-08-01"}],
                },
            }
        )

        match = await retrieve_for_clause(client, clause)

        assert match.error is None
        assert match.citation == "40 CFR 261.10"
        assert match.part == "261"
        assert match.section == "10"
        assert match.date == "2026-08-17"
        assert match.version == "2026-08-01"
        assert match.version_payload == {
            "title": 40,
            "versions": [{"issue_date": "2026-07-01"}, {"issue_date": "2026-08-01"}],
        }
        assert match.retrieval_method == "ecfr_api"
        assert match.source == "eCFR"
        assert match.retrieved_at  # populated

        # The version-history tool is reached with title/part/section args,
        # never with a Clause.
        tool_names = [name for name, _ in client.calls]
        assert "get_version_history" in tool_names

    async def test_version_fetch_failure_does_not_block(self) -> None:
        clause = Clause(title="Environmental", text="Contractor shall dispose hazardous waste")
        client = _FakeClient(
            {
                "search_regulations": {
                    "results": [
                        {"hierarchy": {"title": 40, "part": "261", "section": "10"}}
                    ],
                    "total_count": 1,
                    "current_page": 1,
                    "total_pages": 1,
                },
                "retrieve_section": {
                    "text": "261.10 - Standards for hazardous waste.",
                    "citation": {
                        "title": 40,
                        "part": "261",
                        "section": "10",
                        "date": "2026-08-17",
                        "heading": "261.10",
                        "url": "u",
                    },
                },
                "get_version_history": {
                    "error": True,
                    "error_type": "EcfrServerError",
                    "message": "boom",
                    "retryable": True,
                },
            }
        )

        match = await retrieve_for_clause(client, clause)
        assert match.error is None
        assert match.citation == "40 CFR 261.10"
        assert match.version_payload is None
        assert match.version == ""


class TestCfrMatchMetadata:
    def test_cfrmatch_provenance_defaults(self) -> None:
        match = CfrMatch(clause=Clause(title="T", text="body"))
        assert match.source == "eCFR"
        assert match.retrieval_method == "ecfr_api"
        assert match.date == ""
        assert match.version == ""
        assert match.retrieved_at  # auto-stamped