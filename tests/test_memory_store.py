"""Compliance Memory store tests (agent.memory_store).

Covers durability (append + reload), deduplication, malformed-line
tolerance (fail-open), missing-field validation, and the atomic-append
design. No external dependencies; all records are synthetic.
"""

from __future__ import annotations

import json

import pytest

from agent.memory_store import MemoryStore

MIN_RECORD = {
    "record_id": "abc123",
    "format_version": 1,
    "clause_id": "clause-1",
    "status": "Compliant",
}


def test_append_then_reload_is_durable(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    assert store.append(dict(MIN_RECORD)) is True

    reloaded = MemoryStore(tmp_path / "mem")
    records = reloaded.load()
    assert len(records) == 1
    assert records[0]["record_id"] == "abc123"


def test_duplicate_record_id_is_rejected(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    assert store.append(dict(MIN_RECORD)) is True
    assert store.append(dict(MIN_RECORD)) is False
    assert len(store.load()) == 1
    assert store.contains("abc123") is True


def test_malformed_line_is_skipped_not_fatal(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store._ensure_dir()
    bad = '{"record_id": "good", "format_version": 1}\n{truncated\n'
    store.memory_path.write_text(bad, encoding="utf-8")

    assert store.load() == [{"record_id": "good", "format_version": 1}]
    assert store.contains("good") is True


def test_non_object_line_is_skipped(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store._ensure_dir()
    bad = '"just a string"\n{"record_id": "x", "format_version": 1}\n'
    store.memory_path.write_text(bad, encoding="utf-8")

    assert [r["record_id"] for r in store.load()] == ["x"]


def test_append_requires_record_id(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    with pytest.raises(ValueError):
        store.append({"format_version": 1})


def test_append_requires_format_version(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    with pytest.raises(ValueError):
        store.append({"record_id": "x"})


def test_memory_path_not_a_directory_fails_loudly(tmp_path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    store = MemoryStore(path)
    with pytest.raises(OSError):
        store.append(dict(MIN_RECORD))


def test_append_is_jsonl_round_trippable(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.append(dict(MIN_RECORD))
    lines = store.memory_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["record_id"] == "abc123"