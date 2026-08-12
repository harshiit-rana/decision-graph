"""CLI for the Why Engine and Impact Analysis (§6 feature surface).

A thin view over Retrieval + Reasoning. It holds no logic of its own — per §6, no
feature may introduce engine-level behaviour, and formatting a path is presentation.
The trace it prints is the path data Engine 2 already computed, which is what
Explainability Mode (Phase 4) will render properly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from . import db, reasoning, retrieval
from .reasoning import Answer, Mode, Path

TIER_MARK = {"explicit": "==", "corroborated": "++", "inferred": "~~"}


def _label(path: Path, node_id: int) -> str:
    node_type = path.types.get(node_id) or "?"
    title = (path.titles.get(node_id) or "").strip()
    if len(title) > 58:
        title = title[:55] + "..."
    return f"{node_type}:{node_id}" + (f' "{title}"' if title else "")


def render(answer: Answer, out=sys.stdout) -> None:
    header = f"{answer.mode.value.upper()}  start=node:{answer.start_node_id}"
    print(header, file=out)
    print("-" * len(header), file=out)

    if not answer.found:
        print(f"  no answer — {answer.explanation}", file=out)
        return

    if answer.used_inferred_fallback:
        # Never let this pass silently: an inferred answer is a different kind of claim.
        print("  !! INFERRED FALLBACK — no explicit path existed", file=out)
    print(f"  {answer.explanation}\n", file=out)

    for i, path in enumerate(sorted(answer.paths, key=lambda p: (p.depth, p.target_id)), 1):
        print(f"  [{i}] tier={path.tier}  depth={path.depth}", file=out)
        print(f"      {_label(path, path.node_ids[0])}", file=out)
        for step, node_id in zip(path.steps, path.node_ids[1:]):
            mark = TIER_MARK.get(step.evidence_tier, "??")
            arrow = "->" if step.to_node_id == node_id else "<-"
            print(
                f"        {mark}{arrow} {step.edge_type} [{step.evidence_tier}]"
                f" ({step.extractor})",
                file=out,
            )
            print(f"      {_label(path, node_id)}", file=out)
        print(file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dg-query", description="Why Engine / Impact Analysis over the graph"
    )
    parser.add_argument("query", help="entity title, #number, or sha to start from")
    parser.add_argument(
        "--mode", choices=[m.value for m in Mode], default=Mode.WHY.value
    )
    parser.add_argument("--depth", type=int, default=reasoning.DEFAULT_MAX_DEPTH)
    parser.add_argument(
        "--as-of",
        help="ISO timestamp for a point-in-time query; uses edge valid_from/valid_to",
    )
    parser.add_argument("--limit", type=int, default=3, help="candidate start nodes to try")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    conn = db.connect(dsn)

    candidates = retrieval.find_candidates(conn, args.query, limit=args.limit)
    if not candidates:
        print(f"no candidate start node matched {args.query!r}", file=sys.stderr)
        return 1

    print(f"candidates for {args.query!r}:")
    for c in candidates:
        print(f"  node:{c.node_id:<5} {c.node_type:<13} {c.match:<11} {(c.title or '')[:56]}")
    print()

    for c in candidates:
        answer = reasoning.reason(
            conn, c.node_id, Mode(args.mode), max_depth=args.depth, as_of=as_of
        )
        render(answer)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
