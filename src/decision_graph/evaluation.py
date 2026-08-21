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

**Deliberately whole-database, not repo-scoped** (issue #28). The §9 query set is
hand-written against `pallets/flask`, and every database this runs against today holds
exactly that one repo, so there is nothing to scope against yet. Give this a `--repo`
filter, matching `dg-query`'s, only when a second repo actually shares a database with
flask -- not speculatively ahead of that.
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


# A Decision carries the motivating issue's title and sits under a thread_key naming its
# cluster. That key is chosen by `threads._rank` — PR-preferring, then lowest number —
# purely so two ingestion orders produce the same key. It is a stable LABEL, not a claim
# about which pull request did the work, and when a change took two attempts it names the
# ABANDONED one: decision 928 sits in `thread:30:pr-5867`, but 5867 was never merged and
# PR 5899 is the credited implementer.
#
# A Why-walk reaching a Decision across `motivated_by` stops there and never traverses
# `implemented_by`, so the only PR number a reader saw was the wrong one (issue #19).
# The graph was right; the report was misleading — which is worse than it sounds, because
# it makes a stale label and a stale edge look identical on the page. Adjudication depends
# on telling those apart.
DECISION_FACTS_SQL = """
SELECT d.node_id,
       im.node_type   AS implementer_type,
       im.external_id AS implementer_ref,
       pr.merged_at
FROM decision d
LEFT JOIN edge e  ON e.src_node_id = d.node_id
                 AND e.edge_type   = 'implemented_by'
                 AND e.valid_to IS NULL
LEFT JOIN node im ON im.id = e.dst_node_id
LEFT JOIN pull_request pr ON pr.node_id = im.id
WHERE d.node_id = ANY(%s)
ORDER BY d.node_id, im.external_id
"""


def _format_implementer(row: dict[str, Any]) -> str:
    kind = row["implementer_type"]
    ref = row["implementer_ref"]
    if kind == "commit":
        return f"commit {ref[:7]}"
    if kind != "pull_request":
        return f"{kind} {ref}"
    # Merge state is spelled out rather than implied. An unmerged implementer should not
    # be able to sit quietly in a trace — post-#17 it cannot exist, and this is how a
    # regression would announce itself instead of reading as an ordinary path.
    when = f"merged {row['merged_at']:%Y-%m-%d}" if row["merged_at"] else "NOT MERGED"
    return f"PR {ref}, {when}"


def decision_annotations(conn, node_ids: list[int]) -> dict[int, str]:
    """Map Decision node ids to a short statement of what the graph credits them to."""
    if not node_ids:
        return {}

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(DECISION_FACTS_SQL, (sorted(set(node_ids)),)).fetchall():
        grouped.setdefault(row["node_id"], []).append(row)

    notes: dict[int, str] = {}
    for node_id, rows in grouped.items():
        credited = [r for r in rows if r["implementer_ref"]]
        if not credited:
            # Unreachable while the rubric holds. Surfaced rather than skipped: a Decision
            # asserting nothing is the single most important thing to show an adjudicator.
            notes[node_id] = "no current implementer"
            continue
        # Every current implementer, not the first. A LIMIT 1 here would have concealed
        # the double-implementer bug that the #17 fix introduced and then repaired.
        notes[node_id] = "implemented by " + "; ".join(_format_implementer(r) for r in credited)
    return notes


def _describe(
    path: reasoning.Path, node_id: int, annotations: dict[int, str] | None = None
) -> str:
    node_type = path.types.get(node_id) or "?"
    title = (path.titles.get(node_id) or "").strip()
    text = f"{node_type}:{node_id}" + (f" — {title}" if title else "")
    note = (annotations or {}).get(node_id)
    return f"{text}  [{note}]" if note else text


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

    # Annotated once for the whole answer rather than per step, so a Decision reached by
    # several paths reads identically in each.
    decision_ids = [
        nid
        for path in answer.paths
        for nid in path.node_ids
        if path.types.get(nid) == "decision"
    ]
    annotations = decision_annotations(conn, decision_ids)

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
                    to_node=_describe(path, node_id, annotations),
                )
            )
        result.paths.append(
            PathRecord(
                tier=path.tier,
                depth=path.depth,
                start=_describe(path, path.node_ids[0], annotations),
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
