"""CLI for the Why Engine and Impact Analysis (§6 feature surface).

A thin view over Retrieval + Reasoning. It holds no logic of its own — per §6, no
feature may introduce engine-level behaviour, and formatting a path is presentation.
Rendering lives in `render.py`, shared with `dg ask` so the two cannot disagree about
what an answer looks like.

By default this answers the question once, from the best-matching artifact, and says how
many other candidates it declined. It used to run every candidate and print three
separately-headed traversals of what is usually one finding seen from three nodes, which
left the reader to work out that they were the same (issue #69). `--all` restores that.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from . import db, diagram, reasoning, render, retrieval, trace
from .reasoning import Answer, Mode

# Kept as a module attribute because it was one, and `from .query import TIER_MARK` is the
# kind of import that exists in someone's script.
TIER_MARK = trace.TIER_MARK

# How a retrieval tier reads. `exact` and `fuzzy` are the system's words for how the start
# node was found, and a reader deciding whether the tool understood them needs to know
# which happened -- a fuzzy match on a paraphrase is a different claim from a title hit.
_MATCH_MEANING = {
    "exact": "exact title match",
    "identifier": "matched by identifier",
    "prefix": "title starts with your query",
    "fuzzy": "closest text match",
}


def render_answer(
    answer: Answer,
    *,
    annotations: dict[int, str] | None = None,
    statuses: dict[int, str] | None = None,
    links: dict[str, str] | None = None,
    verbose: bool = False,
    out=None,
) -> None:
    render.render(
        answer, annotations=annotations, statuses=statuses, links=links,
        verbose=verbose, out=out,
    )


def _decision_facts(
    conn, answer: Answer
) -> tuple[dict[int, str], dict[int, str], dict[str, str]]:
    """What the graph credits each Decision in this answer to (issues #19, #69).

    A Why-walk stops at the Decision and never traverses `implemented_by`, so without this
    lookup the only pull request a reader sees is the one inside the `thread_key` -- which
    names the cluster, and for 6 of 15 Decisions names a pull request that never merged.
    The evaluation report has printed this since #19; the command people actually run did
    not.
    """
    ids = [
        nid
        for path in answer.paths
        for nid in path.node_ids
        if path.ref(nid).node_type == "decision"
    ]
    if not ids:
        return {}, {}, {}
    return (
        trace.decision_annotations(conn, ids),
        trace.decision_statuses(conn, ids),
        trace.decision_links(conn, ids),
    )


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
    parser.add_argument(
        "--all",
        action="store_true",
        help="answer from every candidate, not just the best match",
    )
    parser.add_argument(
        "--repo", help="owner/name — restrict candidates to this repo (needed once more "
        "than one repo is ingested into the same database)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="show node ids and the extractor behind each edge",
    )
    parser.add_argument(
        "--format",
        choices=["text", *sorted(diagram.FORMATS)],
        default="text",
        help="text (default), or a diagram of the walk: mermaid, dot",
    )
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

    repo_node_id = None
    if args.repo:
        repo_node_id = retrieval.resolve_repo(conn, args.repo)
        if repo_node_id is None:
            print(f"error: repo {args.repo!r} has not been ingested", file=sys.stderr)
            return 2

    candidates = retrieval.find_candidates(
        conn, args.query, repo_node_id=repo_node_id, limit=args.limit
    )
    if not candidates:
        print(f"Nothing in the graph matches {args.query!r}.", file=sys.stderr)
        print(file=sys.stderr)
        print("  Retrieval tries exact title, then identifier, then prefix, then fuzzy —", file=sys.stderr)
        print("  so a miss means no artifact in the ingested window resembles this.", file=sys.stderr)
        print("  Try `#5898` or a commit sha, or check `dg status` for what is ingested.", file=sys.stderr)
        return 1

    graphical = args.format != "text"
    question = "Why did this happen?" if args.mode == Mode.WHY.value else "What does this affect?"
    if not graphical:
        print(render.bold(f"{question}   {render.dim(repr(args.query))}"))
        print()

    chosen = candidates if args.all else candidates[:1]
    for c in chosen:
        if not graphical:
            how = _MATCH_MEANING.get(c.match, c.match)
            print(
                f"  starting from  {render.bold(trace.ref(c.node_type, c.external_id))}"
                f"  {(c.title or '')[:56]}"
            )
            print(render.dim(f"                 {how}"))
            print()

        answer = reasoning.reason(
            conn, c.node_id, Mode(args.mode), max_depth=args.depth, as_of=as_of
        )
        if graphical:
            # Raw, so it pipes into a .mmd or .dot file. A refusal produces nothing at all
            # rather than an empty diagram: an empty `graph LR` renders as a blank box,
            # which reads as a drawing failure and not as "the graph holds no answer".
            drawn = diagram.emit(answer, args.format)
            if drawn:
                print(drawn)
            else:
                print(
                    f"# no answer to draw: {answer.explanation}",
                    file=sys.stderr,
                )
            continue

        annotations, statuses, links = _decision_facts(conn, answer)
        render_answer(
            answer, annotations=annotations, statuses=statuses, links=links,
            verbose=args.verbose,
        )

    skipped = len(candidates) - len(chosen)
    if skipped and not graphical:
        print(render.dim(
            f"  {render.plural(skipped, 'other candidate')} matched this query — "
            "`--all` answers from each of them."
        ))
        for c in candidates[len(chosen):]:
            print(render.dim(f"    {trace.ref(c.node_type, c.external_id)}  {(c.title or '')[:52]}"))
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
