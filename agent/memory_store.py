"""agent/memory_store.py

Durable, append-only persistence for the Compliance Memory layer.

Verified historical compliance outcomes are stored as newline-delimited
JSON (JSONL) records in a dedicated, configurable directory. The store
is intentionally minimal and self-contained:

  - durable: records survive process restarts (no Redis/PostgreSQL);
  - inspectable: a plain `memory.jsonl`, one record per line;
  - auditable: append-only; records are never rewritten or deleted;
  - atomic where practical: each append is a single buffered write plus
    fsync, so a crash can only truncate the *trailing* line -- which the
    loader skips as malformed instead of failing;
  - deduplicated: ``append`` refuses a record whose ``record_id`` is
    already present;
  - migration-friendly: every line carries a ``format_version`` field.

The filename is a fixed constant and the directory comes from
configuration, so there is no user-controlled path component to traverse
(the containment convention mirrors ``agent.reporting``).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Schema version of a stored memory record. Bump on breaking changes so
#: older files can be detected/ignored during load.
MEMORY_FORMAT_VERSION = 1

#: Fixed filename inside the configured memory directory.
MEMORY_FILENAME = "memory.jsonl"


class MemoryStore:
    """Append-only JSONL store for compliance memory records."""

    def __init__(self, memory_dir: str | Path) -> None:
        self.memory_dir = Path(memory_dir)
        self.memory_path = self.memory_dir / MEMORY_FILENAME
        self._record_ids: set[str] | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> Path:
        if not self.memory_dir.exists():
            self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_dir.is_dir():
            raise OSError(f"CFR memory path is not a directory: {self.memory_dir}")
        return self.memory_dir

    def _known_ids(self) -> set[str]:
        """All stored record IDs (lazily loaded, cached for the lifetime)."""
        if self._record_ids is None:
            ids: set[str] = set()
            for data in self.load():
                record_id = data.get("record_id")
                if isinstance(record_id, str) and record_id:
                    ids.add(record_id)
            self._record_ids = ids
        return self._record_ids

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def contains(self, record_id: str) -> bool:
        """True when a record with this stable ID is already stored."""
        return record_id in self._known_ids()

    def append(self, record: dict[str, Any]) -> bool:
        """Append one record atomically.

        Returns:
            True when the record was appended, False when a record with
            the same ``record_id`` already exists (deduplication).

        Raises:
            ValueError: if the record is missing ``record_id`` /
                ``format_version``.
            OSError: if the memory path is not a directory or the write
                fails. Callers must treat this as non-fatal (the core
                compliance result stays valid).
        """
        if not isinstance(record.get("record_id"), str) or not record["record_id"]:
            raise ValueError("Memory record missing a non-empty record_id")
        if "format_version" not in record:
            raise ValueError("Memory record missing format_version")

        if self.contains(record["record_id"]):
            return False

        self._ensure_dir()
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        # Unbuffered binary append + fsync: a crash mid-write can only
        # leave a truncated trailing line, which load() skips.
        with open(self.memory_path, "ab", buffering=0) as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self._record_ids.add(record["record_id"])
        return True

    def load(self) -> list[dict[str, Any]]:
        """Read every well-formed record.

        Malformed/truncated trailing lines (the only corruption the
        append design can produce) are skipped with a warning, never
        fatal -- memory must fail open.
        """
        records: list[dict[str, Any]] = []
        if not self.memory_path.exists():
            return records
        for lineno, line in enumerate(
            self.memory_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "memory_load_skipped malformed record at %s line %d",
                    self.memory_path,
                    lineno,
                )
                continue
            if not isinstance(data, dict):
                logger.warning(
                    "memory_load_skipped non-object record at %s line %d",
                    self.memory_path,
                    lineno,
                )
                continue
            records.append(data)
        return records