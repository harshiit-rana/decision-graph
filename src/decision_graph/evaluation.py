"""Evaluation runner (PRD v3.1 §9).

Runs the hand-curated query set and records, for every query, exactly what the engines
returned — candidates, paths, per-step evidence tiers, and the extractor behind each
edge.

**This module does not grade anything.** §9 calls for answers "manually checked against
the repo's actual history", and a system scoring its own output against its own graph
would be checking self-consistency, not correctness. The runner emits `verdict: null`
for every query; a human fills it in.

The one thing it does assert is `contract` — mechanically checkable properties of the
engine itself, like "must return no answer for an artifact outside the window". Those
are statements about the code's contract, not about flask, so the runner can check them
without grading itself.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, reasoning, retrieval
from .reasoning import Mode

log = logging.getLogger(__name__)


@dataclass
class StepRecord:
    edge_type: str
    tier: str
    tag: str
    extractor: str
    source_ref: str | None
    direction: str
    to_node: str


@dataclass
class PathRecord:
    tier: str
    depth: int
    start: str
    steps: list[StepRecord]


@dataclass
class QueryResult:
    id: str
    category: str
    mode: str
    query: str
    intent: str
    verify: str | None
    contract: str | None
    as_of: str | None
    depth: int

    candidates: list[dict[str, Any]] = field(default_factory=list)
    paths: list[PathRecord] = field(default_factory=list)
    used_inferred_fallback: bool = False
    explanation: str = ""
    answered: bool = False

    # Filled by a human. §9 requires manual verification against real history.
    verdict: str | None = None
    notes: str | None = None


def _describe(path: reasoning.Path, node_id: int) -> str:
    node_type = path.types.get(node_id) or "?"
    title = (path.titles.get(node_id) or "").strip()
    return f"{node_type}:{node_id}" + (f" — {title}" if title else "")


def run_query(conn, spec: dict[str, Any]) -> QueryResult:
    as_of = datetime.fromisoformat(spec["as_of"]) if spec.get("as_of") else None
    depth = spec.get("depth", reasoning.DEFAULT_MAX_DEPTH)

    result = QueryResult(
        id=spec["id"],
        category=spec["category"],
        mode=spec["mode"],
        query=spec["query"],
        intent=spec["intent"],
        verify=spec.get("verify"),
        contract=spec.get("contract"),
        as_of=spec.get("as_of"),
        depth=depth,
    )

    candidates = retrieval.find_candidates(conn, spec["query"], limit=3)
    result.candidates = [
        {
            "node_id": c.node_id,
            "node_type": c.node_type,
            "external_id": c.external_id,
            "title": c.title,
            "match": c.match,
            "score": round(c.score, 3),
        }
        for c in candidates
    ]

    if not candidates:
        result.explanation = "retrieval returned no candidate start node"
        return result

    # Only the top candidate is answered, so the record shows what a user would
    # actually see rather than the best of several attempts.
    start = candidates[0]
    answer = reasoning.reason(
        conn, start.node_id, Mode(spec["mode"]), max_depth=depth, as_of=as_of
    )

    result.answered = answer.found
    result.used_inferred_fallback = answer.used_inferred_fallback
    result.explanation = answer.explanation

    for path in sorted(answer.paths, key=lambda p: (p.depth, p.target_id)):
        steps = []
        for step, node_id in zip(path.steps, path.node_ids[1:]):
            steps.append(
                StepRecord(
                    edge_type=step.edge_type,
                    tier=step.evidence_tier,
                    tag=step.tag,
                    extractor=step.extractor,
                    source_ref=step.source_ref,
                    direction="forward" if step.to_node_id == node_id else "reverse",
                    to_node=_describe(path, node_id),
                )
            )
        result.paths.append(
            PathRecord(
                tier=path.tier,
                depth=path.depth,
                start=_describe(path, path.node_ids[0]),
                steps=steps,
            )
        )

    return result


def check_contract(result: QueryResult) -> str | None:
    """Verify engine-contract properties only. Never judges factual correctness."""
    if not result.contract:
        return None
    text = result.contract.lower()

    if "no candidates" in text:
        return "HELD" if not result.candidates else "VIOLATED: candidates were returned"
    if "no answer" in text:
        return "HELD" if not result.answered else "VIOLATED: an answer was returned"
    if "fewer paths" in text or "same paths" in text:
        return "COMPARE"  # resolved against its sibling query during review
    return None


def run_all(dsn: str, query_set_path: Path) -> dict[str, Any]:
    spec = json.loads(query_set_path.read_text(encoding="utf-8"))
    conn = db.connect(dsn)

    results = []
    for query_spec in spec["queries"]:
        log.info("running %s: %s", query_spec["id"], query_spec["query"])
        result = run_query(conn, query_spec)
        payload = asdict(result)
        payload["contract_check"] = check_contract(result)
        results.append(payload)

    conn.close()

    answered = sum(1 for r in results if r["answered"])
    contracts = [r for r in results if r["contract_check"] in ("HELD", "VIOLATED")]

    return {
        "about": spec["about"],
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "total": len(results),
            "answered": answered,
            "returned_no_answer": len(results) - answered,
            "contracts_checked": len(contracts),
            "contracts_held": sum(1 for r in contracts if r["contract_check"] == "HELD"),
            "used_inferred_fallback": sum(1 for r in results if r["used_inferred_fallback"]),
            "adjudicated": 0,
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[2]
    query_set = root / "eval" / "query_set.json"
    out = root / "eval" / "results.json"

    report = run_all(dsn, query_set)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    s = report["summary"]
    log.info("")
    log.info("queries              : %s", s["total"])
    log.info("answered             : %s", s["answered"])
    log.info("returned no answer   : %s", s["returned_no_answer"])
    log.info("engine contracts held: %s/%s", s["contracts_held"], s["contracts_checked"])
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
