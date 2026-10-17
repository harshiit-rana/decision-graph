"""Context pack: everything the graph evidences about one artifact, plus its gaps (issue #138).

An agent starting work on an issue used to make several queries (why, impact, prior
attempts) and merge them itself. Nothing told it what TraceMind *doesn't* know, and that is
the half that prevents a confident wrong assumption. `dg context <#N | sha | title>` returns
one document with:
- the resolved artifact;
- its Why answer or refusal;
- its impact answer or refusal;
- the prior attempts that match it;
- the Decision facts;
- an explicit `gaps` list.

It composes existing engine calls only (PRD §6: features are thin views). The Why and
impact results are the very `contract.result_doc` objects `dg query --format json` returns,
so the two can't disagree. `gaps` is mechanical: each entry is a fixed code with a fixed
sentence, computed from fields already in the document. It is never written by a model.

The budget options cut honestly. Whatever is dropped is counted in `truncated`, under the
same rule #130 sets for traversal bounds: a limit that hides what it cut is a wrong answer
that looks complete.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from . import attempts, contract, reasoning, retrieval, trace
from .reasoning import Mode

ATTEMPT_LIMIT = 5

# The fixed gap vocabulary. A gap is something the graph does not record, stated so that an
# agent can't mistake it for a fact about the repository.
GAPS = {
    # Neutral on purpose. Which case it is (closed as not planned, no rationale recorded,
    # and so on) is `refusal_kind`, and a fixed sentence here must not contradict it.
    "why_refused": "No Why answer: the graph holds no path from this artifact to a recorded "
    "reason. `refusal_kind` says which case this is.",
    "impact_refused": "No impact answer: no recorded link leads downstream from this artifact. "
    "`refusal_kind` says which case this is.",
    "inferred_fallback_used": "No explicit path existed, so at least one answer uses inferred "
    "edges: a lead, not a record.",
    "no_decision_reached": "The Why answer reaches no Decision, so nothing here says what was "
    "decided.",
    "no_review_recorded": "No review is recorded on the work any Decision here is credited to.",
    "match_not_exact": "The artifact was found by a prefix or closest-text match, not by its "
    "title or identifier. Check `start` is the artifact you meant.",
    "no_prior_attempt_found": "No ended attempt in the ingested window matches this artifact's "
    "title. Matching is lexical, so that is not evidence nobody tried.",
}


def gaps_for(doc: dict[str, Any]) -> list[dict[str, str]]:
    """What the pack doesn't contain, from the pack itself. Order is the order of GAPS."""
    why, impact = doc["why"], doc["impact"]
    found_codes: set[str] = set()
    if not why["found"]:
        found_codes.add("why_refused")
    if not impact["found"]:
        found_codes.add("impact_refused")
    if why["used_inferred_fallback"] or impact["used_inferred_fallback"]:
        found_codes.add("inferred_fallback_used")
    if why["found"] and not why["decisions"]:
        found_codes.add("no_decision_reached")
    decisions = why["decisions"] + impact["decisions"]
    if decisions and not any(d["reviewers"] for d in decisions):
        found_codes.add("no_review_recorded")
    if doc["start"]["match"] in ("prefix", "fuzzy"):
        found_codes.add("match_not_exact")
    if not doc["attempts"]:
        found_codes.add("no_prior_attempt_found")
    out = []
    for code, detail in GAPS.items():
        if code in found_codes:
            entry = {"code": code, "detail": detail}
            refusal = (why if code == "why_refused" else impact if code == "impact_refused"
                       else None)
            if refusal is not None:
                entry["refusal_kind"] = refusal["refusal"]["kind"]
            out.append(entry)
    return out


def _cut(text: str | None, limit: int | None) -> tuple[str | None, int]:
    if text is None or limit is None or len(text) <= limit:
        return text, 0
    return text[:limit].rstrip() + "...", len(text) - limit


def apply_budget(doc: dict[str, Any], *, max_paths: int | None,
                 max_quote_chars: int | None) -> dict[str, Any]:
    """Trim paths and quoted text to the budget, counting everything removed."""
    paths_omitted = 0
    chars_cut = 0
    for key in ("why", "impact"):
        result = doc[key]
        if max_paths is not None and len(result["paths"]) > max_paths:
            paths_omitted += len(result["paths"]) - max_paths
            result["paths"] = result["paths"][:max_paths]
        if result.get("context"):
            result["context"]["excerpt"], n = _cut(result["context"]["excerpt"], max_quote_chars)
            chars_cut += n
        closure = (result.get("refusal") or {}).get("closure") or {}
        if closure.get("last_word"):
            closure["last_word"]["body"], n = _cut(closure["last_word"]["body"], max_quote_chars)
            chars_cut += n
    for a in doc["attempts"]:
        if a.get("last_word"):
            a["last_word"]["body"], n = _cut(a["last_word"]["body"], max_quote_chars)
            chars_cut += n
    doc["budget"] = {"max_paths": max_paths, "max_quote_chars": max_quote_chars}
    doc["truncated"] = (
        {"paths_omitted": paths_omitted, "quote_chars_cut": chars_cut}
        if paths_omitted or chars_cut else None
    )
    return doc


def assemble(query: str, why: dict, impact: dict, attempt_docs: list[dict], *,
             max_paths: int | None = None, max_quote_chars: int | None = None) -> dict:
    """The pack from already-built parts. No database, so the shape is testable alone."""
    doc = {
        "schema_version": contract.SCHEMA_VERSION,
        "kind": "context",
        "query": query,
        "repository_text_fields": list(contract.REPOSITORY_TEXT_FIELDS),
        "start": why["start"],
        "why": why,
        "impact": impact,
        "attempts": attempt_docs,
    }
    # Gaps are read before the budget trims anything, so a cut can't hide a gap.
    doc["gaps"] = gaps_for(doc)
    return apply_budget(doc, max_paths=max_paths, max_quote_chars=max_quote_chars)


def build(conn, query: str, *, repo_node_id: int | None = None,
          max_depth: int = reasoning.DEFAULT_MAX_DEPTH, max_paths: int | None = None,
          max_quote_chars: int | None = None, quotes: str = "all") -> dict:
    candidates = retrieval.find_candidates(conn, query, repo_node_id=repo_node_id, limit=1)
    if not candidates:
        return contract.no_match_document(query)
    c = candidates[0]
    url = contract.node_urls(conn, [c.node_id]).get(c.node_id)

    def result(mode: Mode) -> dict:
        answer = reasoning.reason(conn, c.node_id, mode, max_depth=max_depth)
        facts = contract.read_facts(conn, answer)
        closure = None if answer.found else trace.closure_fact(conn, c.node_id)
        return contract.result_doc(c, answer, facts, closure=closure, url=url)

    found = [
        a for a in attempts.find_attempts(conn, c.title or query, repo_node_id=repo_node_id,
                                          limit=ATTEMPT_LIMIT + 1)
        if a.candidate.node_id != c.node_id
    ][:ATTEMPT_LIMIT]
    attempt_docs = attempts.document(query, found)["attempts"]
    doc = assemble(query, result(Mode.WHY), result(Mode.IMPACT), attempt_docs,
                   max_paths=max_paths, max_quote_chars=max_quote_chars)
    return contract.finish_from_db(conn, doc, quotes, request={
        "command": "context", "query": query, "max_depth": max_depth, "max_paths": max_paths,
        "max_quote_chars": max_quote_chars, "quotes": quotes,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dg context",
        description="Everything the graph evidences about one artifact, and what it doesn't "
        "record, as one JSON document (schema docs/schema/answer.v1.json, kind \"context\").",
    )
    parser.add_argument("query", help="#number, commit sha, or title")
    parser.add_argument("--repo", help="owner/name, when more than one repo is ingested")
    parser.add_argument("--depth", type=int, default=reasoning.DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-paths", type=int, default=None,
                        help="keep at most N paths per answer; the rest are counted in `truncated`")
    parser.add_argument("--quotes", choices=contract.QUOTE_MODES, default="all",
                        help="`maintainers` removes the text of anything not written by an OWNER, "
                        "MEMBER or COLLABORATOR, and counts what it removed (issue #177)")
    parser.add_argument("--max-quote-chars", type=int, default=None,
                        help="cut each quoted text to N characters; the cut is counted")

    def _json_error(message: str) -> None:
        print(contract.dumps(contract.error_document(message)))
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)

    parser.error = _json_error  # type: ignore[method-assign]
    args = parser.parse_args(argv)

    def fail(message: str) -> int:
        print(contract.dumps(contract.error_document(message)))
        print(f"error: {message}", file=sys.stderr)
        return 2

    for name, value in (("--depth", args.depth), ("--max-paths", args.max_paths),
                        ("--max-quote-chars", args.max_quote_chars)):
        if value is not None and value < 1:
            return fail(f"{name} {value} must be 1 or more")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return fail("DATABASE_URL is not set")

    from . import db

    conn = db.connect(dsn)
    try:
        repo_node_id = None
        if args.repo:
            repo_node_id = retrieval.resolve_repo(conn, args.repo)
            if repo_node_id is None:
                return fail(f"repo {args.repo!r} has not been ingested")
        doc = build(conn, args.query, repo_node_id=repo_node_id, max_depth=args.depth,
                    max_paths=args.max_paths, max_quote_chars=args.max_quote_chars,
                    quotes=args.quotes)
    finally:
        conn.close()
    print(contract.dumps(doc))
    return 1 if doc["kind"] == "no_match" else 0


if __name__ == "__main__":
    raise SystemExit(main())
