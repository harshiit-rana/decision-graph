"""Conflicting evidence, returned and never resolved (issue #175).

Memory and context products resolve contradictions silently:
- Unblocked: contradictions are "resolved automatically using recency, authority, and
  proximity signals" (https://getunblocked.com/);
- Mem0 deletes the contradicted memory and prompts the model to prefer the most recent
  (https://arxiv.org/pdf/2504.19413);
- Letta's memory blocks are last-write-wins;
- Graphiti "prioritizes new information" (https://arxiv.org/pdf/2501.13956).

An agent then acts on the winner without knowing there was a conflict. TraceMind does the
opposite, as a contract guarantee: **when the graph holds evidence pointing two ways, the
answer lists both sides, with their tiers, sources and timestamps, and names no winner.**

Detection is mechanical, from rows already in the graph. No model is involved, and a conflict
is never *concluded*: "this PR was later reverted" is not asserted, only that a second source
disagrees with the first.

The types, each a fixed sentence:

- `multiple_implementers`: more than one current `implemented_by` edge on one Decision.
- `multiple_closing_prs`: more than one merged pull request states it closes one issue.
- `superseded`: a current `supersedes` / `superseded_by` link touches a node on the trail.
- `reopened`: GitHub records an issue on the trail as reopened, while the trail reaches a
  Decision that treats it as resolved.
- `implementer_not_merged`: the pull request credited with a Decision isn't merged. The rubric
  requires a merge, so this shouldn't happen; it is a canary, shown rather than hidden.

A revert type arrives with #156 (reading `This reverts commit <sha>` as supersession).

**Sides are ordered chronologically** (earliest recorded first, then edge id). That order
says nothing about preference, and no field anywhere in a conflict names a preferred side.
`tests/test_conflicts.py` asserts that over every conflict type.
"""

from __future__ import annotations

from typing import Any

from . import contract, trace
from .reasoning import Answer

DETAILS = {
    "multiple_implementers": (
        "More than one pull request or commit is currently credited with implementing this "
        "Decision. All are shown; none is preferred."
    ),
    "multiple_closing_prs": (
        "More than one merged pull request states that it closes this issue. All are shown; "
        "none is preferred."
    ),
    "superseded": (
        "An explicit link records one of these as superseding the other. Both are shown, with "
        "the link's direction; the older evidence has not been removed."
    ),
    "reopened": (
        "GitHub records this issue as reopened after it was closed, while the trail reaches a "
        "Decision that treats it as resolved. Both facts are shown."
    ),
    "implementer_not_merged": (
        "The pull request credited with this Decision is not merged. The rubric requires a "
        "merge, so the Decision is suspect. This should not happen, and is shown rather than "
        "hidden."
    ),
}

TYPES = tuple(DETAILS)

# Words that would name a preferred side. A test checks no conflict key contains one.
FORBIDDEN_KEYS = ("winner", "preferred", "chosen", "best", "current_side", "resolved_to", "verdict")

_NODE_SQL = """
SELECT n.id AS node_id, n.node_type::text AS type, n.external_id, n.title, n.url
  FROM node n WHERE n.id = ANY(%s)
"""


def _node(row: dict[str, Any]) -> dict[str, Any]:
    return contract.node_doc(row["node_id"], row["type"], row["external_id"], row["title"], row["url"])


def _edge(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": row["edge_id"],
        "edge_type": row["edge_type"],
        "evidence_tier": row["evidence_tier"],
        "extractor": row["extractor"],
        "source_ref": row["source_ref"],
        "observed_at": row["observed_at"],
    }


def _side(label: str, node: dict, edge: dict | None = None, facts: dict | None = None) -> dict:
    return {"label": label, "node": node, "edge": edge, "facts": facts or {}}


def _chronological(sides: list[dict]) -> list[dict]:
    """Earliest recorded first, then edge id. A neutral order, not a ranking."""
    def key(s: dict):
        e = s["edge"] or {}
        return (e.get("observed_at") is None, e.get("observed_at"), e.get("edge_id") or 0,
                s["node"]["node_id"])
    return sorted(sides, key=key)


def _conflict(kind: str, subject: dict, sides: list[dict]) -> dict[str, Any]:
    return {"type": kind, "detail": DETAILS[kind], "subject": subject,
            "sides": _chronological(sides)}


def detect(conn, answer: Answer) -> list[dict[str, Any]]:
    """Every conflict the graph shows for the nodes on `answer`'s paths."""
    node_ids = sorted({nid for p in answer.paths for nid in p.node_ids})
    if not node_ids:
        return []
    types = {nid: next((p.ref(nid).node_type for p in answer.paths if nid in p.node_ids), None)
             for nid in node_ids}
    decisions = [n for n, t in types.items() if t == "decision"]
    issues = [n for n, t in types.items() if t == "issue"]
    nodes = {r["node_id"]: r for r in conn.execute(_NODE_SQL, (node_ids,)).fetchall()}
    out: list[dict[str, Any]] = []

    # -- implementers -------------------------------------------------------------------
    impl: dict[int, list[dict]] = {}
    if decisions:
        for r in conn.execute(
            """
            SELECT e.src_node_id AS decision_id, e.id AS edge_id, e.edge_type::text AS edge_type,
                   e.evidence_tier::text AS evidence_tier, e.extractor, e.source_ref, e.observed_at,
                   n.id AS node_id, n.node_type::text AS type, n.external_id, n.title, n.url,
                   p.merged_at
              FROM edge e JOIN node n ON n.id = e.dst_node_id
              LEFT JOIN pull_request p ON p.node_id = n.id
             WHERE e.src_node_id = ANY(%s) AND e.edge_type = 'implemented_by' AND e.valid_to IS NULL AND e.retracted_at IS NULL
            """, (decisions,)).fetchall():
            impl.setdefault(r["decision_id"], []).append(r)
    for decision_id, rows in impl.items():
        subject = _node(nodes[decision_id]) if decision_id in nodes else {"node_id": decision_id}
        if len(rows) > 1:
            out.append(_conflict("multiple_implementers", subject, [
                _side("credited implementer", _node(r), _edge(r), _merge_facts(r)) for r in rows]))
        for r in rows:
            if r["type"] == "pull_request" and r["merged_at"] is None:
                out.append(_conflict("implementer_not_merged", subject, [
                    _side("credited implementer, not merged", _node(r), _edge(r), _merge_facts(r))]))

    # -- closing PRs and reopened issues ------------------------------------------------
    if issues:
        closers: dict[int, list[dict]] = {}
        for r in conn.execute(
            """
            SELECT e.dst_node_id AS issue_id, e.id AS edge_id, e.edge_type::text AS edge_type,
                   e.evidence_tier::text AS evidence_tier, e.extractor, e.source_ref, e.observed_at,
                   n.id AS node_id, n.node_type::text AS type, n.external_id, n.title, n.url,
                   p.merged_at
              FROM edge e JOIN node n ON n.id = e.src_node_id
              JOIN pull_request p ON p.node_id = n.id
             WHERE e.dst_node_id = ANY(%s) AND e.edge_type = 'closes' AND e.valid_to IS NULL AND e.retracted_at IS NULL
               AND p.merged_at IS NOT NULL
            """, (issues,)).fetchall():
            closers.setdefault(r["issue_id"], []).append(r)
        for issue_id, rows in closers.items():
            if len({r["node_id"] for r in rows}) > 1:
                out.append(_conflict("multiple_closing_prs", _node(nodes[issue_id]), [
                    _side("states it closes this issue", _node(r), _edge(r), _merge_facts(r))
                    for r in rows]))
        reopened = conn.execute(
            "SELECT node_id, state, state_reason::text AS state_reason, closed_at FROM issue "
            "WHERE node_id = ANY(%s) AND state_reason = 'reopened'", (issues,)).fetchall()
        for r in reopened:
            if decisions:  # reopened matters when the trail treats the issue as resolved
                out.append(_conflict("reopened", _node(nodes[r["node_id"]]), [
                    _side("recorded by GitHub as reopened", _node(nodes[r["node_id"]]), None,
                          {"state": r["state"], "state_reason": r["state_reason"],
                           "closed_at": r["closed_at"]}),
                    *[_side("Decision reached on this trail", _node(nodes[d]), None, {})
                      for d in decisions],
                ]))

    # -- supersession -------------------------------------------------------------------
    for r in conn.execute(
        """
        SELECT e.id AS edge_id, e.edge_type::text AS edge_type, e.evidence_tier::text AS evidence_tier,
               e.extractor, e.source_ref, e.observed_at, e.src_node_id, e.dst_node_id
          FROM edge e
         WHERE e.edge_type IN ('supersedes', 'superseded_by') AND e.valid_to IS NULL AND e.retracted_at IS NULL
           AND (e.src_node_id = ANY(%(ids)s) OR e.dst_node_id = ANY(%(ids)s))
        """, {"ids": node_ids}).fetchall():
        pair = [r["src_node_id"], r["dst_node_id"]]
        extra = {n: row for n, row in
                 ((x["node_id"], x) for x in conn.execute(_NODE_SQL, (pair,)).fetchall())}
        if len(extra) < 2:
            continue
        a, b = r["src_node_id"], r["dst_node_id"]
        verb = "supersedes" if r["edge_type"] == "supersedes" else "is superseded by"
        out.append(_conflict("superseded", _node(extra[a]), [
            _side(f"this {verb} the other", _node(extra[a]), _edge(r), {}),
            _side("the other side of the link", _node(extra[b]), None, {}),
        ]))
    return out


def _merge_facts(row: dict[str, Any]) -> dict[str, Any]:
    if row["type"] != "pull_request":
        return {}
    return {"merged": row["merged_at"] is not None, "merged_at": row["merged_at"]}


def line_for(side: dict[str, Any]) -> str:
    """One side as a line of text, for the renderer."""
    node = side["node"]
    bits = [side["label"] + ":", trace.ref(node.get("type"), node.get("external_id"))]
    facts = side.get("facts") or {}
    if facts.get("merged_at"):
        bits.append(f"merged {facts['merged_at']:%Y-%m-%d}")
    elif facts.get("merged") is False:
        bits.append("NOT MERGED")
    edge = side.get("edge")
    if edge:
        bits.append(f"[{edge['evidence_tier']}]")
    return " ".join(bits)
