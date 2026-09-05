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

from . import db, reasoning, retrieval, trace
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


# What a Decision is credited to, and how a node is named, both live in `trace` now: this
# worksheet and `dg query` render the same answer for the same reader, and an adjudicator
# comparing one against the other is doing the job §9 exists for. Two implementations would
# drift precisely there (issue #69). The names are kept bound here because they were part of
# this module's surface and the tests address them by it.
DECISION_FACTS_SQL = trace.DECISION_FACTS_SQL
_format_implementer = trace.format_implementer
decision_annotations = trace.decision_annotations


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
    """Run the §9 query set.

    This took an `argv` it never parsed, so every argument was ignored -- including
    `--help`, which ran the entire evaluation and wrote over `eval/results.json` (issue
    #82). That file is the record of an adjudicated run, and a module whose only mode is
    "overwrite the record" has no safe way to be explored.
    """
    args = build_parser().parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Arguments are checked before the environment: a mistyped --query-set is the caller's
    # own line to fix, and reporting a missing DATABASE_URL instead sends them to look at
    # something that was never wrong.
    query_set = Path(args.query_set)
    if not query_set.exists():
        print(f"error: no query set at {query_set}", file=sys.stderr)
        return 2
    out = Path(args.output)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2

    return _run(dsn, query_set, out)


def build_parser():
    """The argument parser, exposed so a test can ask what this accepts.

    Separate from `main` because the alternative is a test that rebuilds a parser it
    believes matches -- which passes while the real one drifts.
    """
    import argparse

    root = Path(__file__).resolve().parents[2]

    parser = argparse.ArgumentParser(
        prog="python -m decision_graph.evaluation",
        description="Run the §9 evaluation query set against the graph.",
        epilog=(
            "The runner grades nothing: `verdict` is emitted null for every query and "
            "filled in by a human against the repository's real history. Adjudication "
            "lives in eval/RESULTS.md, not in the JSON."
        ),
    )
    parser.add_argument(
        "--query-set",
        default=str(root / "eval" / "query_set.json"),
        help="the query set to run (default: eval/query_set.json)",
    )
    parser.add_argument(
        "--output",
        default=str(root / "eval" / "results.json"),
        help="where to write the results (default: eval/results.json -- the committed record)",
    )
    return parser


def _run(dsn, query_set, out) -> int:
    report = run_all(dsn, query_set)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    s = report["summary"]
    log.info("")
    log.info("queries              : %s", s["total"])
    log.info("answered             : %s", s["answered"])
    log.info("returned no answer   : %s", s["returned_no_answer"])
    log.info("engine contracts held: %s/%s", s["contracts_held"], s["contracts_checked"])
    log.info("wrote %s", out)
    # Said plainly because it is easy to run this without meaning to replace anything. The
    # numbers here are what the engine returned; whether they are CORRECT is a human
    # judgement that lives in a different file and is not carried in this one.
    log.info("")
    log.info("This file records what the engine returned, with every verdict null.")
    log.info("The adjudication is in eval/RESULTS.md and is not regenerated by this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
