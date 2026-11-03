"""Accuracy evaluation for the deterministic (LLM-free) compliance path.

The existing benchmark/compliance_benchmark.py measures orchestration speed
with a stub LLM. It says nothing about whether verdicts are CORRECT. This
harness does: it runs evaluate_deterministic over hand-labeled
(clause, regulation text) pairs and reports accuracy, per-class
precision/recall, and the cases the rules got wrong.

Labels MUST come from a human who has read the regulation text. Do not
generate labels with an LLM -- that makes the metric circular.

Usage:
    python -m benchmark.accuracy_eval benchmark/labeled_cases.jsonl
    python -m benchmark.accuracy_eval benchmark/labeled_cases.jsonl --json out.json

Each JSONL row:
    {"id": "c001", "clause_title": "...", "clause_text": "...",
     "cfr_title": 40, "cfr_part": "261", "cfr_section": "10",
     "cfr_text": "<exact regulation text>", "expected": "Compliant",
     "notes": "why you labeled it this way"}
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from agent.deterministic_rules import evaluate_deterministic
from agent.models import Clause

LABELS = ["Compliant", "Non-Compliant", "Needs Review"]


def load_cases(path: Path) -> list[dict]:
    cases = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if row.get("expected") not in LABELS:
            raise ValueError(f"line {n}: expected must be one of {LABELS}")
        cases.append(row)
    return cases


def run(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        clause = Clause(title=c["clause_title"], text=c["clause_text"])
        res = evaluate_deterministic(
            clause, c["cfr_text"], c["cfr_title"], c.get("cfr_part"), c.get("cfr_section")
        )
        rows.append({"id": c["id"], "expected": c["expected"],
                     "predicted": res.status, "confidence": res.confidence,
                     "reason": res.reason, "notes": c.get("notes", "")})

    n = len(rows)
    metrics = {}
    for label in LABELS:
        tp = sum(r["expected"] == label and r["predicted"] == label for r in rows)
        fp = sum(r["expected"] != label and r["predicted"] == label for r in rows)
        fn = sum(r["expected"] == label and r["predicted"] != label for r in rows)
        metrics[label] = {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "support": tp + fn,
        }

    decided = [r for r in rows if r["predicted"] != "Needs Review"]
    # The number that matters for a compliance tool: confident verdicts that are wrong.
    wrong_decisions = [r for r in decided if r["predicted"] != r["expected"]]
    # Safe-failure rate: wrong-class cases the system escalated instead of guessing.
    escalated_unsure = [r for r in rows
                        if r["expected"] != "Needs Review" and r["predicted"] == "Needs Review"]
    return {
        "n": n,
        "accuracy": sum(r["expected"] == r["predicted"] for r in rows) / n if n else None,
        "decided_rate": len(decided) / n if n else None,
        "wrong_confident_rate": len(wrong_decisions) / len(decided) if decided else None,
        "escalated_but_decidable": len(escalated_unsure),
        "per_class": metrics,
        "confusion": dict(Counter((r["expected"], r["predicted"]) for r in rows).items()
                          ).__repr__(),
        "errors": [r for r in rows if r["expected"] != r["predicted"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    report = run(load_cases(a.cases))
    print(f"cases: {report['n']}")
    print(f"accuracy: {report['accuracy']:.3f}")
    print(f"decided without LLM: {report['decided_rate']:.3f}")
    wc = report["wrong_confident_rate"]
    print(f"wrong among confident verdicts: {'n/a' if wc is None else f'{wc:.3f}'}")
    for label, m in report["per_class"].items():
        p = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
        r = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
        print(f"  {label:14s} precision={p} recall={r} support={m['support']}")
    print(f"\nerrors ({len(report['errors'])}):")
    for e in report["errors"]:
        print(f"  {e['id']}: expected={e['expected']} got={e['predicted']} -- {e['reason']}")
    if a.json:
        a.json.write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
