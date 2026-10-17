"""The versioned JSON contract: `dg query --format json` (issue #135, ADR 0006).

Every other output is for a person. This one is for a program, such as a coding agent, a
CI job or the MCP server (#139). Without it, a program has to scrape prose to learn whether
there was an answer, which tier it carries, and why it refused. The tier is the first thing
scraping loses, and losing it undoes the evidence grading at the last step.

Three rules hold it in place:

1. **Built from the same objects the text renderer reads, never from rendered text.** The
   facts come from the same `trace` queries, and the refusal classification and the quoted
   context come from the same `render` functions. The two outputs can't drift apart (#78).
2. **Repository text is never mixed into the system's own words.** Titles, body excerpts
   and quoted comments each sit in their own named field, listed in
   `repository_text_fields`, and no system-written string concatenates one. A consumer
   that passes this to a model can fence exactly those fields (ADR 0006, H3; #119).
3. **A refusal is a result.** `found: false` always comes with a `refusal` object that
   says which kind of refusal it is. There is never a bare `null`.

Breaking changes bump `SCHEMA_VERSION`. `tests/test_contract.py` pins the field set, so a
removed or renamed field fails the build unless the version moves with it. The schema is
published at `docs/schema/answer.v1.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from . import render, trace
from .reasoning import TIER_ORDER, Answer, Path

SCHEMA_VERSION = 1

# Fields whose value was written by someone in the repository, not by this system. Treat
# them as data, never as instructions.
REPOSITORY_TEXT_FIELDS = ("title", "excerpt", "body")

# The same caveat the text renderer prints beside a quoted comment (issue #108).
LAST_WORD_CAVEAT = (
    "This is what was said, not why it closed. The graph records no link between the "
    "comment and the closure."
)


@dataclass
class Facts:
    """What the graph credits the Decisions in one answer with, read once per answer."""

    statuses: dict[int, str] = field(default_factory=dict)
    implementers: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    reviewers: dict[int, list[str]] = field(default_factory=dict)
    bodies: dict[int, str] = field(default_factory=dict)


def read_facts(conn, answer: Answer) -> Facts:
    """The lookups `query._decision_facts` makes for the text renderer, as fields."""
    all_ids = [nid for path in answer.paths for nid in path.node_ids]
    decision_ids = [
        nid for path in answer.paths for nid in path.node_ids
        if path.ref(nid).node_type == "decision"
    ]
    return Facts(
        statuses=trace.decision_statuses(conn, decision_ids),
        implementers=trace.decision_implementers(conn, decision_ids),
        reviewers=trace.decision_reviewers(conn, decision_ids),
        bodies=trace.artifact_bodies(conn, all_ids) if all_ids else {},
    )


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def node_doc(node_id: int, node_type: str | None, external_id: str | None,
             title: str | None, url: str | None) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "type": node_type,
        "external_id": external_id,
        "ref": trace.ref(node_type, external_id),
        "url": url,
        "title": title,
    }


def _path_node(path: Path, node_id: int) -> dict[str, Any]:
    r = path.ref(node_id)
    return node_doc(node_id, r.node_type, r.external_id, r.title, r.url)


def path_doc(path: Path) -> dict[str, Any]:
    steps = []
    for prev, (step, node_id) in zip(path.node_ids, zip(path.steps, path.node_ids[1:])):
        forward = step.to_node_id == node_id
        steps.append(
            {
                "edge_id": step.edge_id,
                "edge_type": step.edge_type,
                "from_node_id": prev,
                "to_node_id": node_id,
                # The edge's stored direction against the direction of the walk. A Why-walk
                # crosses most edges dst -> src, and a consumer can't recover that from
                # the endpoints alone.
                "traversed": "src_to_dst" if forward else "dst_to_src",
                "phrase": render.phrase(step.edge_type, forward),
                "evidence_tier": step.evidence_tier,
                "tag": step.tag,
                "extractor": step.extractor,
                "source_ref": step.source_ref,
            }
        )
    return {
        "tier": path.tier,
        "hops": path.depth,
        "nodes": [_path_node(path, nid) for nid in path.node_ids],
        "steps": steps,
    }


def _ordered_decisions(answer: Answer) -> list[int]:
    seen: list[int] = []
    for path in _sorted_paths(answer):
        for nid in path.node_ids:
            if path.ref(nid).node_type == "decision" and nid not in seen:
                seen.append(nid)
    return seen


def _sorted_paths(answer: Answer) -> list[Path]:
    # The text renderer's order, so "[2]" in one is paths[1] in the other.
    return sorted(answer.paths, key=lambda p: (p.depth, p.target_id))


def decision_doc(node_id: int, facts: Facts) -> dict[str, Any]:
    implementers = [
        {
            "type": im["type"],
            "external_id": im["external_id"],
            "ref": im["ref"],
            "url": im["url"],
            "merged": im["merged_at"] is not None if im["type"] == "pull_request" else None,
            "merged_at": im["merged_at"],
        }
        for im in facts.implementers.get(node_id, [])
    ]
    return {
        "node_id": node_id,
        "status": facts.statuses.get(node_id),
        "implementers": implementers,
        "reviewers": list(facts.reviewers.get(node_id, [])),
    }


def _context_doc(answer: Answer, facts: Facts) -> dict[str, Any] | None:
    nid = render.context_node_id(answer, facts.bodies)
    if nid is None:
        return None
    excerpt = render.context_excerpt(facts.bodies[nid])
    if not excerpt:
        return None
    r = next((p.ref(nid) for p in answer.paths if nid in p.node_ids), None)
    ref_text = trace.ref(r.node_type, r.external_id) if r else None
    return {"node_id": nid, "ref": ref_text, "excerpt": excerpt}


def closure_doc(closure) -> dict[str, Any] | None:
    if closure is None:
        return None
    said = closure.last_word
    return {
        "ref": closure.ref,
        "kind": closure.kind,
        "closed_at": closure.closed_at,
        "last_word": None if said is None else {
            "body": said.body,
            "author_association": said.association,
            "at": said.at,
            "caveat": LAST_WORD_CAVEAT,
        },
    }


def refusal_doc(answer: Answer, *, as_of=None, closure=None) -> dict[str, Any]:
    return {
        "kind": render.refusal_kind(answer, as_of, closure),
        "engine_explanation": answer.explanation,
        "inferred_fallback_tried": answer.used_inferred_fallback,
        "closure": closure_doc(closure),
    }


def tier_legend(tiers: Iterable[str]) -> dict[str, dict[str, str]]:
    return {
        t: {"meaning": trace.TIER_MEANING[t], "strength": trace.TIER_STRENGTH[t]}
        for t in sorted(set(tiers), key=lambda t: TIER_ORDER.get(t, 9))
    }


def node_urls(conn, node_ids: Iterable[int]) -> dict[int, str]:
    """URLs for nodes no path passes through: a refused start node, other candidates."""
    ids = sorted(set(node_ids))
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, url FROM node WHERE id = ANY(%s) AND url IS NOT NULL", (ids,)
    ).fetchall()
    return {r["id"]: r["url"] for r in rows}


def result_doc(candidate, answer: Answer, facts: Facts, *, as_of=None,
               closure=None, url: str | None = None) -> dict[str, Any]:
    """One answered (or refused) start node."""
    start = node_doc(candidate.node_id, candidate.node_type, candidate.external_id,
                     candidate.title, url or _url_of(answer, candidate.node_id))
    start["match"] = candidate.match
    paths = _sorted_paths(answer)
    found = answer.found
    return {
        "start": start,
        "found": found,
        "used_inferred_fallback": answer.used_inferred_fallback,
        "tiers": tier_legend(p.tier for p in paths),
        "paths": [path_doc(p) for p in paths],
        "decisions": [decision_doc(nid, facts) for nid in _ordered_decisions(answer)],
        "context": _context_doc(answer, facts) if found else None,
        "refusal": None if found else refusal_doc(answer, as_of=as_of, closure=closure),
    }


def _url_of(answer: Answer, node_id: int) -> str | None:
    for p in answer.paths:
        if node_id in p.node_ids:
            return p.ref(node_id).url
    return None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _envelope(kind: str, **body: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": kind, **body}


def answer_document(*, query: str, mode: str, as_of, max_depth: int,
                    results: list[dict[str, Any]], other_candidates,
                    urls: dict[int, str] | None = None) -> dict[str, Any]:
    urls = urls or {}
    return _envelope(
        "answer",
        query=query,
        mode=mode,
        as_of=as_of,
        max_depth=max_depth,
        repository_text_fields=list(REPOSITORY_TEXT_FIELDS),
        results=results,
        other_candidates=[
            {**node_doc(c.node_id, c.node_type, c.external_id, c.title, urls.get(c.node_id)),
             "match": c.match}
            for c in other_candidates
        ],
    )


def no_match_document(query: str) -> dict[str, Any]:
    """No artifact resembles the query. Distinct from a refusal: there was no start node."""
    return _envelope(
        "no_match",
        query=query,
        message=(
            "Nothing in the graph matches. Retrieval tries exact title, then identifier, "
            "then prefix, then fuzzy, so a miss means no artifact in the ingested window "
            "resembles this."
        ),
    )


def error_document(message: str, code: str = "bad_argument") -> dict[str, Any]:
    return _envelope("error", error={"code": code, "message": message})


# ---------------------------------------------------------------------------
# Who wrote the text (issue #177)
# ---------------------------------------------------------------------------
#
# The contract confines repository text to named fields (REPOSITORY_TEXT_FIELDS), but a title
# from a drive-by account and one from a maintainer looked identical to an agent, and that is
# the channel prompt-injection attacks on agent tools use. So each object carrying such a
# field also carries `author_association` (GitHub's own word for how the author stands to the
# project) and `untrusted`.
#
# `untrusted` is true unless the association is OWNER, MEMBER or COLLABORATOR, the same set
# `trace._STANDING` uses to decide whose last word is quoted. Where the association isn't
# known it is null and the text is untrusted: GitHub records it for issues, pull requests and
# comments, but not for commits or releases, and a Decision's title is copied from the issue
# that motivated it. Unknown is never promoted to trusted.
#
# These labels are advisory. A client model can still follow injected text from a maintainer's
# account, or from a drive-by one it ignores the label on. SECURITY.md says so.

TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
QUOTE_MODES = ("all", "maintainers")


def is_untrusted(association: str | None) -> bool:
    return (association or "").upper() not in TRUSTED_ASSOCIATIONS


def read_associations(conn, node_ids: Iterable[int]) -> dict[int, str | None]:
    """author_association per node, from the comment column or the stored GitHub payload."""
    ids = sorted(set(node_ids))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT n.id, COALESCE(c.author_association, n.raw->>'author_association') AS a
          FROM node n LEFT JOIN comment c ON c.node_id = n.id
         WHERE n.id = ANY(%s)
        """,
        (ids,),
    ).fetchall()
    return {r["id"]: (r["a"] or None) for r in rows}


def _objects(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from _objects(v)


def _carries_text(obj: dict) -> bool:
    return any(k in obj for k in REPOSITORY_TEXT_FIELDS)


def node_ids_in(doc: dict) -> set[int]:
    """Every node id of an object that carries repository text."""
    return {o["node_id"] for o in _objects(doc)
            if "node_id" in o and _carries_text(o) and isinstance(o["node_id"], int)}


def label_authors(doc: dict, associations: dict[int, str | None]) -> None:
    """Add `author_association` and `untrusted` beside every repository-text field, in place."""
    for obj in _objects(doc):
        if "node_id" in obj and _carries_text(obj):
            assoc = associations.get(obj["node_id"])
            obj["author_association"] = assoc
            obj["untrusted"] = is_untrusted(assoc)
        elif "author_association" in obj and "body" in obj and "untrusted" not in obj:
            obj["untrusted"] = is_untrusted(obj["author_association"])  # a closing comment


def suppress_untrusted(doc: dict) -> int:
    """Remove the text of every untrusted object, in place; return how many fields went.

    The object stays, with `suppressed: true`, so a consumer sees that something was there
    and who wrote it rather than a silent gap.
    """
    removed = 0
    for obj in _objects(doc):
        if obj.get("untrusted") is True:
            for key in REPOSITORY_TEXT_FIELDS:
                if obj.get(key) is not None:
                    obj[key] = None
                    obj["suppressed"] = True
                    removed += 1
    return removed


def finish(doc: dict, associations: dict[int, str | None], quotes: str = "all") -> dict:
    """Label, and in `maintainers` mode suppress, then say what was done."""
    if quotes not in QUOTE_MODES:
        raise ValueError(f"quotes must be one of {QUOTE_MODES}, not {quotes!r}")
    label_authors(doc, associations)
    removed = suppress_untrusted(doc) if quotes == "maintainers" else 0
    doc["quotes"] = {"mode": quotes, "suppressed": removed}
    return doc


def finish_from_db(conn, doc: dict, quotes: str = "all", *, request: dict | None = None) -> dict:
    """Label authors, apply the quote mode, and record provenance (issues #177, #176)."""
    finish(doc, read_associations(conn, node_ids_in(doc)), quotes)
    doc["provenance"] = provenance(conn, doc, request or {})
    return doc


# ---------------------------------------------------------------------------
# Provenance (issue #176)
# ---------------------------------------------------------------------------
#
# An answer that can't be replayed can't be diffed: when it changes between two runs, nothing
# in it says whether the graph, the code or the question changed. So every document records
# what was asked, which engine and schema produced it, and the exact rows it read. That is
# the shape of PROV-O's Activity / Entity split and SLSA's externalParameters /
# resolvedDependencies, kept as plain fields. It deliberately holds no timestamp of its own,
# so two runs over an unchanged database produce identical provenance.
#
# It never holds the DSN, a token, or anything from .env: `request` is built by the caller
# from query arguments only, and `engine` is read from the migration ledger.


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("decision-graph")
    except Exception:  # not installed: running from a checkout, as the test suite does
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        try:
            return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        except Exception:
            return "unknown"


def engine_facts(conn) -> dict[str, Any]:
    """Which schema produced this answer, from the migration ledger."""
    import hashlib

    from . import migrate

    ledger = migrate.ledger(conn)
    last = max(ledger) if ledger else None
    digest = hashlib.sha256(
        "\n".join(f"{name}:{ledger[name]}" for name in sorted(ledger)).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "package_version": package_version(),
        "schema_version": SCHEMA_VERSION,
        "migrations": {
            "applied": len(ledger),
            "last": last,
            "last_checksum": ledger.get(last) if last else None,
            "ledger_sha256": digest,
        },
    }


def _step_edge_ids(doc: dict) -> list[int]:
    return sorted({o["edge_id"] for o in _objects(doc)
                   if "edge_id" in o and isinstance(o["edge_id"], int)})


def _all_node_ids(doc: dict) -> list[int]:
    return sorted({o["node_id"] for o in _objects(doc)
                   if "node_id" in o and isinstance(o["node_id"], int)})


def used_rows(conn, doc: dict) -> dict[str, Any]:
    """The edges and nodes the document was built from, with when each was recorded."""
    edge_ids = _step_edge_ids(doc)
    edges = []
    if edge_ids:
        edges = [
            {
                "edge_id": r["id"], "edge_type": r["edge_type"],
                "evidence_tier": r["evidence_tier"], "observed_at": r["observed_at"],
                "valid_from": r["valid_from"], "created_at": r["created_at"],
            }
            for r in conn.execute(
                "SELECT id, edge_type::text AS edge_type, evidence_tier::text AS evidence_tier, "
                "observed_at, valid_from, created_at FROM edge WHERE id = ANY(%s) ORDER BY id",
                (edge_ids,),
            ).fetchall()
        ]
    return {"nodes": _all_node_ids(doc), "edges": edges}


def attach_provenance(doc: dict, request: dict, engine: dict, edges: list[dict]) -> dict:
    """The pure half: assemble the block from parts already read. Tests use it without a database."""
    doc["provenance"] = {
        "request": request, "engine": engine,
        "used": {"nodes": _all_node_ids(doc), "edges": edges},
    }
    return doc


def provenance(conn, doc: dict, request: dict) -> dict[str, Any]:
    used = used_rows(conn, doc)
    return attach_provenance({**doc}, request, engine_facts(conn), used["edges"])["provenance"]


def _default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, default=_default)
