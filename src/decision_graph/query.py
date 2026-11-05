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

from . import attempts, conflicts, contract, db, diagram, reasoning, render, retrieval, trace
from .reasoning import Answer, Mode

# Not a reasoning.Mode: it walks no edges. It is a filtered retrieval over closure facts.
ATTEMPTS = "attempts"
# Nor is this: it only resolves a name to the artifacts it could mean, so an agent can pick one
# before asking why (#139). The same retrieval `why` starts from, shown instead of walked.
LOOKUP = "lookup"

# Kept as a module attribute because it was one, and `from .query import TIER_MARK` is the
# kind of import that exists in someone's script.
TIER_MARK = trace.TIER_MARK

# How a retrieval tier reads. `exact` and `fuzzy` are the system's words for how the start
# node was found, and a reader deciding whether the tool understood them needs to know
# which happened -- a fuzzy match on a paraphrase is a different claim from a title hit.
_MATCH_MEANING = {
    "exact": "exact title match",
    "identifier": "matched by identifier",
    "sha": "matched an abbreviated commit sha",
    "prefix": "title starts with your query",
    "fuzzy": "closest text match",
}


def render_answer(
    answer: Answer,
    *,
    annotations: dict[int, str] | None = None,
    statuses: dict[int, str] | None = None,
    links: dict[str, str] | None = None,
    bodies: dict[int, str] | None = None,
    reviewers: dict[int, list[str]] | None = None,
    as_of=None,
    closure=None,
    verbose: bool = False,
    out=None,
    conflicts=None,
) -> None:
    render.render(
        answer, annotations=annotations, statuses=statuses, links=links,
        bodies=bodies, reviewers=reviewers, as_of=as_of, closure=closure,
        verbose=verbose, out=out, conflicts=conflicts,
    )


def _decision_facts(
    conn, answer: Answer
) -> tuple[dict[int, str], dict[int, str], dict[str, str], dict[int, str],
           dict[int, list[str]]]:
    """What the graph credits each Decision in this answer to, plus artifact bodies (issues #19, #69).

    A Why-walk stops at the Decision and never traverses `implemented_by`, so without this
    lookup the only pull request a reader sees is the one inside the `thread_key` -- which
    names the cluster, and for 6 of 15 Decisions names a pull request that never merged.
    The evaluation report has printed this since #19; the command people actually run did
    not.
    """
    all_ids = [nid for path in answer.paths for nid in path.node_ids]
    bodies = trace.artifact_bodies(conn, all_ids) if all_ids else {}

    ids = [
        nid
        for path in answer.paths
        for nid in path.node_ids
        if path.ref(nid).node_type == "decision"
    ]
    if not ids:
        return {}, {}, {}, bodies, {}
    return (
        trace.decision_annotations(conn, ids),
        trace.decision_statuses(conn, ids),
        trace.decision_links(conn, ids),
        bodies,
        trace.decision_reviewers(conn, ids),
    )



def _wants_json(argv: list[str]) -> bool:
    """Whether the caller asked for JSON, decided before argparse can fail on anything."""
    for i, arg in enumerate(argv):
        if arg == "--format=json" or (arg == "--format" and argv[i + 1 : i + 2] == ["json"]):
            return True
    return False


def answer_document(conn, *, query: str, mode: str, candidates, as_of, max_depth: int,
                    answer_all: bool = False, quotes: str = "all",
                    as_recorded=None, max_paths: int | None = None) -> dict:
    """The JSON contract for one query (issue #135). Takes a connection so it can be tested
    inside a rolled-back fixture transaction, and so the MCP server (#139) can call it."""
    chosen = candidates if answer_all else candidates[:1]
    urls = contract.node_urls(conn, (c.node_id for c in candidates))
    results = []
    for c in chosen:
        answer = reasoning.reason(conn, c.node_id, Mode(mode), max_depth=max_depth, as_of=as_of,
                                  as_recorded=as_recorded, max_paths=max_paths)
        facts = contract.read_facts(conn, answer)
        # The same rule as the text path: a closure explains only a refusal, and only
        # for the node the walk started from.
        closure = None if answer.found else trace.closure_fact(conn, c.node_id)
        results.append(contract.result_doc(
            c, answer, facts, as_of=as_of, closure=closure, url=urls.get(c.node_id),
            conflicts=conflicts.detect(conn, answer),
        ))
    doc = contract.answer_document(
        query=query, mode=mode, as_of=as_of, max_depth=max_depth, results=results,
        other_candidates=candidates[len(chosen):], urls=urls,
    )
    return contract.finish_from_db(conn, doc, quotes, request={
        "command": "query", "query": query, "mode": mode, "as_of": as_of,
        "max_depth": max_depth, "all": answer_all, "quotes": quotes,
        "as_recorded": as_recorded, "max_paths": max_paths,
    })


def json_document(conn, *, query: str, mode: str, depth: int, as_of, as_recorded,
                  max_paths: int | None, limit: int, answer_all: bool, quotes: str,
                  repo_node_id: int | None) -> tuple[dict, int]:
    """The whole JSON answer to one query, and the exit code `dg query` gives it.

    `dg query --format json` and the MCP server (#139) both call this, so the adapter has no
    logic of its own: a refusal is a document with code 0, no match is a document with code 1,
    and bad arguments are the caller's to reject before getting here (`argument_error`).
    """
    if mode == ATTEMPTS:
        found = attempts.find_attempts(conn, query, repo_node_id=repo_node_id, limit=limit)
        # An empty list is a result, like a refusal: exit 0, and say what it does not mean.
        return contract.finish_from_db(conn, attempts.document(query, found), quotes, request={
            "command": "query", "query": query, "mode": "attempts", "limit": limit,
            "quotes": quotes,
        }), 0

    candidates = retrieval.find_candidates(conn, query, repo_node_id=repo_node_id, limit=limit)
    if not candidates:
        from . import coverage

        doc = contract.no_match_document(query)
        doc["coverage"] = coverage.compute(conn, start_node_ids=[], query=query,
                                           matched=False, repo_node_id=repo_node_id)
        return doc, 1

    if mode == LOOKUP:
        urls = contract.node_urls(conn, (c.node_id for c in candidates))
        return contract.finish_from_db(
            conn, contract.lookup_document(query, candidates, urls), quotes,
            request={"command": "query", "query": query, "mode": "lookup", "limit": limit,
                     "quotes": quotes}), 0

    return answer_document(
        conn, query=query, mode=mode, candidates=candidates, as_of=as_of,
        as_recorded=as_recorded, max_paths=max_paths, max_depth=depth,
        answer_all=answer_all, quotes=quotes,
    ), 0


def argument_error(*, depth: int, max_paths: int | None, limit: int) -> str | None:
    """The sentence for a number that would make the answer a lie, or None.

    A zero-hop walk, a zero-path cap or a zero-candidate search returns nothing, and the
    renderer would then say the graph holds no evidence: a false statement about the repository
    caused by the caller's own argument (issues #77, #82, #99). So these are rejected, not
    answered.
    """
    if depth < 1:
        return f"--depth {depth} cannot reach anything. Use 1 or more."
    if max_paths is not None and max_paths < 1:
        return (f"--max-paths {max_paths} would keep no paths, so every answer would look like a "
                "refusal. Use 1 or more.")
    if limit < 1:
        return f"--limit {limit} asks for no candidates, so nothing can match. Use 1 or more."
    return None


def parse_instant(name: str, text: str | None):
    """An ISO timestamp, or None when absent. Raises ValueError with the sentence to show."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{name} {text!r} is not a timestamp. Use YYYY-MM-DD, or a full ISO instant like "
            "2026-03-01T00:00:00Z."
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dg-query", description="Why Engine / Impact Analysis over the graph"
    )
    parser.add_argument("query", help="entity title, #number, or sha to start from")
    parser.add_argument(
        "--mode", choices=[*(m.value for m in Mode), ATTEMPTS, LOOKUP], default=Mode.WHY.value,
        help="why: what led to this; impact: what it affects; attempts: earlier issues and "
        "pull requests on this that ended without landing, and how each ended; lookup: which "
        "artifacts the query could mean, without walking from any of them",
    )
    parser.add_argument("--depth", type=int, default=reasoning.DEFAULT_MAX_DEPTH)
    parser.add_argument(
        "--as-of",
        help="ISO timestamp for a point-in-time query: what the repository said then, as "
        "TraceMind now understands it. Uses valid_from/valid_to and hides retracted edges at "
        "every time (ADR 0009)",
    )
    parser.add_argument(
        "--max-paths", type=int, default=None,
        help="keep at most N paths per answer (the first N in the walk's own order, shortest "
        "first) and say how many were left out. Off by default: flask's largest answer is 17. "
        "A tracking issue referenced by hundreds of PRs can return thousands (issue #130)",
    )
    parser.add_argument(
        "--as-recorded",
        help="ISO timestamp: what TraceMind's graph HELD at that moment, including an edge "
        "retracted since and an edit it had not yet seen (ADR 0009). With --as-of, asks what "
        "the repository said at that time as recorded then; alone, it uses the same instant",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="candidate start nodes to try (default 3), or attempts to list (default 10)",
    )
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
        "--quotes", choices=contract.QUOTE_MODES, default="all",
        help="with --format json: `maintainers` removes the text of anything not written by an "
        "OWNER, MEMBER or COLLABORATOR, and counts what it removed (issue #177)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", *sorted(diagram.FORMATS)],
        default="text",
        help="text (default); json, the versioned contract for programs and agents "
        "(docs/schema/answer.v1.json); or a diagram of the walk: mermaid, dot",
    )
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = _wants_json(argv)
    if as_json:
        # A program that asked for JSON must get JSON on every exit, including argparse's
        # own usage errors, which would otherwise print prose and exit 2 (#82's contract).
        def _json_error(message: str) -> None:
            print(contract.dumps(contract.error_document(message)))
            print(f"error: {message}", file=sys.stderr)
            raise SystemExit(2)

        parser.error = _json_error  # type: ignore[method-assign]
    args = parser.parse_args(argv)

    def fail(message: str, code: int = 2) -> int:
        if as_json:
            print(contract.dumps(contract.error_document(message)))
        print(f"error: {message}", file=sys.stderr)
        return code

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return fail("DATABASE_URL is not set")

    try:
        # Every bad argument here produces a sentence; a timestamp used to produce a
        # traceback out of the standard library (issue #82).
        as_of = parse_instant("--as-of", args.as_of)
        as_recorded = parse_instant("--as-recorded", args.as_recorded)
    except ValueError as exc:
        return fail(str(exc))

    if args.limit is None:
        args.limit = attempts.DEFAULT_LIMIT if args.mode == ATTEMPTS else 3
    # A refusal that reports your mistake as a fact is worse than an error, because it is
    # believable (issues #77, #82, #99).
    bad = argument_error(depth=args.depth, max_paths=args.max_paths, limit=args.limit)
    if bad:
        return fail(bad)

    if args.quotes != "all" and not as_json:
        return fail("--quotes applies to --format json; the text output is for a person reading "
                    "the repository anyway")

    if args.mode in (ATTEMPTS, LOOKUP) and args.format not in ("text", "json"):
        return fail(f"--mode {args.mode} lists artifacts; it has no {args.format} diagram")

    conn = db.connect(dsn)

    repo_node_id = None
    if args.repo:
        repo_node_id = retrieval.resolve_repo(conn, args.repo)
        if repo_node_id is None:
            return fail(f"repo {args.repo!r} has not been ingested")

    if as_json:
        doc, code = json_document(
            conn, query=args.query, mode=args.mode, depth=args.depth, as_of=as_of,
            as_recorded=as_recorded, max_paths=args.max_paths, limit=args.limit,
            answer_all=args.all, quotes=args.quotes, repo_node_id=repo_node_id,
        )
        print(contract.dumps(doc))
        conn.close()
        return code

    if args.mode == ATTEMPTS:
        found = attempts.find_attempts(
            conn, args.query, repo_node_id=repo_node_id, limit=args.limit
        )
        attempts.render(args.query, found, out=sys.stdout)
        conn.close()
        return 0

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

    if args.mode == LOOKUP:
        print(render.bold(f"Which artifact does {args.query!r} mean?"))
        print()
        for c in candidates:
            how = _MATCH_MEANING.get(c.match, c.match)
            print(f"  {render.bold(trace.ref(c.node_type, c.external_id))}  "
                  f"{render.safe(c.title)[:56]}  {render.dim(how)}")
        conn.close()
        return 0

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
                f"  {render.safe(c.title)[:56]}"
            )
            print(render.dim(f"                 {how}"))
            print()

        answer = reasoning.reason(
            conn, c.node_id, Mode(args.mode), max_depth=args.depth, as_of=as_of,
            as_recorded=as_recorded, max_paths=args.max_paths,
        )
        if graphical:
            # Raw, so it pipes into a .mmd or .dot file. A refusal produces nothing at all
            # rather than an empty diagram: an empty `graph LR` renders as a blank box,
            # which reads as a drawing failure and not as "the graph holds no answer".
            drawn = diagram.emit(answer, args.format)
            if drawn:
                if args.format == "mermaid":
                    print("# paste at https://mermaid.live  or into any GitHub comment/issue/PR",
                          file=sys.stderr)
                elif args.format == "dot":
                    print("# paste at https://dreampuf.github.io/GraphvizOnline or pipe to dot -Tpng",
                          file=sys.stderr)
                print(drawn)
            else:
                print(
                    f"# no answer to draw: {answer.explanation}",
                    file=sys.stderr,
                )
            continue


        annotations, statuses, links, bodies, reviewers = _decision_facts(conn, answer)
        # Only on a refusal, and only for the node the walk started from. A closure
        # explains why *this* artifact produced nothing; it says nothing about the graph,
        # so it has no business appearing beside an answer that was found.
        closure = None if answer.found else trace.closure_fact(conn, c.node_id)
        render_answer(
            answer, annotations=annotations, statuses=statuses, links=links,
            bodies=bodies, reviewers=reviewers, as_of=as_of, closure=closure,
            verbose=args.verbose, conflicts=conflicts.detect(conn, answer),
        )
        if not answer.found:
            # A refusal can be misread as "nothing happened" when the evidence was outside what
            # was ingested, so say what the window covered (#174).
            from . import coverage

            said = coverage.line(coverage.compute(conn, start_node_ids=[c.node_id]))
            if said:
                print(render.dim("  " + said))
                print()

    skipped = len(candidates) - len(chosen)
    if skipped and not graphical:
        print(render.dim(
            f"  {render.plural(skipped, 'other candidate')} matched this query — "
            "`--all` answers from each of them."
        ))
        for c in candidates[len(chosen):]:
            print(render.dim(f"    {trace.ref(c.node_type, c.external_id)}  {render.safe(c.title)[:52]}"))
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
