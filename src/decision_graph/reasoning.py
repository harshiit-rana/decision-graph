"""Engine 2 — Graph Reasoning (PRD v3.1 §5.3).

ONE traversal implementation, exposed through two directional modes. Only the starting
node and the edge set differ; `_walk` is shared. That sharing is a requirement rather
than an optimisation — two separate traversals would drift, and §5.3's fallback rule
would then have to be correct twice.

The fallback order is the delicate part, so it is written as an explicit algorithm here
rather than left as a principle for callers to honour:

  1. Walk using `explicit` edges only. Because of the tag/tier invariant in migration
     0001, `tag = 'explicit'` is exactly the "explicit and corroborated" set §5.3 names
     — `corroborated` is an upgraded explicit edge, never an upgraded inferred one.
  2. Only if that leaves the start node disconnected from any plausible answer does the
     walk re-run with `inferred` edges admitted.
  3. If an explicit path exists, inferred edges are excluded from the answer ENTIRELY.

Clause 3 is the one that is easy to get wrong invisibly. A single-pass traversal that
merely admits inferred edges produces answers that look fine and quietly blend tiers.
The two-pass structure below makes the blend impossible: the second pass runs only when
the first returned nothing, so no answer can ever contain both.

A path's tier is the WEAKEST tier along it. A chain is only as strong as its weakest
link, so a route that traverses one inferred edge is an inferred answer no matter how
many explicit edges surround it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Sequence

import psycopg

log = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 3  # §5.1: v1 traversals are shallow (2-3 hops)

TIER_ORDER = {"explicit": 0, "corroborated": 1, "inferred": 2}


class Mode(Enum):
    """Which question is being asked. Determines the edge set, not the algorithm."""

    WHY = "why"
    IMPACT = "impact"


# §5.3 backward: motivation -> discussion -> decision -> implementation -> follow-ups.
# implemented_by is included because the PRD's own description of the causal chain ends
# at implementation, even though the prose list omits it.
WHY_EDGES: tuple[str, ...] = (
    "motivated_by",
    "implemented_by",
    "discussed_in",
    "references",
    "supersedes",
    "superseded_by",
)

# §5.3 forward: what is affected. Traversed in both orientations so one edge set answers
# "what does this depend on" and "what would reverting this affect".
IMPACT_EDGES: tuple[str, ...] = (
    "depends_on",
    "implements",
    "closes",
    "deployed_by",
    "implemented_by",
)

MODE_EDGES = {Mode.WHY: WHY_EDGES, Mode.IMPACT: IMPACT_EDGES}


@dataclass(frozen=True)
class Step:
    edge_id: int
    edge_type: str
    tag: str
    evidence_tier: str
    from_node_id: int
    to_node_id: int
    extractor: str
    source_ref: str | None


@dataclass
class Path:
    """One route from the start node to a reached node, with its evidence trail."""

    node_ids: list[int]
    steps: list[Step]
    titles: dict[int, str] = field(default_factory=dict)
    types: dict[int, str] = field(default_factory=dict)

    @property
    def target_id(self) -> int:
        return self.node_ids[-1]

    @property
    def depth(self) -> int:
        return len(self.steps)

    @property
    def tier(self) -> str:
        """Weakest tier along the path. A chain is as strong as its weakest link."""
        if not self.steps:
            return "explicit"
        return max((s.evidence_tier for s in self.steps), key=lambda t: TIER_ORDER[t])


@dataclass
class Answer:
    mode: Mode
    start_node_id: int
    paths: list[Path]
    used_inferred_fallback: bool
    explanation: str

    @property
    def found(self) -> bool:
        return bool(self.paths)


# Recursive walk. Both orientations are followed in one CTE so a single implementation
# serves "what led to this" and "what does this affect"; the mode's edge set is what
# gives the walk its direction of meaning.
#
# "Now" is resolved by the DATABASE, not the client. `valid_from` is written by the server
# clock, so comparing it against a client-side timestamp makes the query correct only while
# the two clocks agree. They routinely do not -- a containerised Postgres drifting a second
# ahead of its host is enough to make every edge written in the last second invisible, which
# is exactly the state a query runs in immediately after an ingest. An explicit --as-of is
# still honoured; only the default is server-side.
_WALK_SQL = """
WITH RECURSIVE walk AS (
    SELECT %(start)s::bigint AS node_id,
           0                 AS depth,
           ARRAY[%(start)s::bigint] AS path_nodes,
           ARRAY[]::bigint[] AS path_edges

    UNION ALL

    SELECT nxt.next_id,
           w.depth + 1,
           w.path_nodes || nxt.next_id,
           w.path_edges || nxt.edge_id
    FROM walk w
    CROSS JOIN LATERAL (
        SELECT e.id AS edge_id, e.dst_node_id AS next_id
        FROM edge e
        WHERE e.src_node_id = w.node_id
          AND e.edge_type = ANY(%(types)s::edge_type[])
          AND e.valid_from <= COALESCE(%(as_of)s::timestamptz, now())
          AND (e.valid_to IS NULL OR e.valid_to > COALESCE(%(as_of)s::timestamptz, now()))
          {tag_clause}
        UNION ALL
        SELECT e.id, e.src_node_id
        FROM edge e
        WHERE e.dst_node_id = w.node_id
          AND e.edge_type = ANY(%(types)s::edge_type[])
          AND e.valid_from <= COALESCE(%(as_of)s::timestamptz, now())
          AND (e.valid_to IS NULL OR e.valid_to > COALESCE(%(as_of)s::timestamptz, now()))
          {tag_clause}
    ) nxt
    WHERE w.depth < %(max_depth)s
      AND NOT nxt.next_id = ANY(w.path_nodes)   -- cycle guard
)
SELECT depth, path_nodes, path_edges
FROM walk
WHERE depth > 0
ORDER BY depth, path_nodes
"""


def _walk(
    conn: psycopg.Connection,
    start: int,
    edge_types: Sequence[str],
    *,
    allow_inferred: bool,
    max_depth: int,
    as_of: datetime | None,
) -> list[Path]:
    """One pass of the traversal. `allow_inferred` is the ONLY difference between passes."""
    # Explicit-only is expressed as tag = 'explicit'. Per the 0001 invariant that is
    # exactly {explicit, corroborated} by tier, which is what §5.3 step 1 asks for.
    tag_clause = "" if allow_inferred else "AND e.tag = 'explicit'"
    sql = _WALK_SQL.format(tag_clause=tag_clause)

    rows = conn.execute(
        sql,
        {
            "start": start,
            "types": list(edge_types),
            "max_depth": max_depth,
            "as_of": as_of,
        },
    ).fetchall()

    if not rows:
        return []

    edge_ids = {eid for r in rows for eid in r["path_edges"]}
    node_ids = {nid for r in rows for nid in r["path_nodes"]}

    edges = {
        e["id"]: Step(
            edge_id=e["id"],
            edge_type=e["edge_type"],
            tag=e["tag"],
            evidence_tier=e["evidence_tier"],
            from_node_id=e["src_node_id"],
            to_node_id=e["dst_node_id"],
            extractor=e["extractor"],
            source_ref=e["source_ref"],
        )
        for e in conn.execute(
            "SELECT id, edge_type, tag, evidence_tier, src_node_id, dst_node_id, "
            "extractor, source_ref FROM edge WHERE id = ANY(%s)",
            (list(edge_ids),),
        ).fetchall()
    }
    nodes = {
        n["id"]: (n["node_type"], n["title"])
        for n in conn.execute(
            "SELECT id, node_type, title FROM node WHERE id = ANY(%s)", (list(node_ids),)
        ).fetchall()
    }

    return [
        Path(
            node_ids=list(r["path_nodes"]),
            steps=[edges[eid] for eid in r["path_edges"]],
            titles={nid: (nodes.get(nid) or (None, None))[1] for nid in r["path_nodes"]},
            types={nid: (nodes.get(nid) or (None, None))[0] for nid in r["path_nodes"]},
        )
        for r in rows
    ]


def _is_answer(mode: Mode, start_type: str | None, path: Path) -> bool:
    """Does this path reach a plausible answer, as §5.3 step 2 means it?

    Defined per mode rather than "reached anything", because the fallback decision hangs
    on it: too loose a definition means the inferred fallback never fires, and too tight
    means it fires when an explicit answer was available.
    """
    target_type = path.types.get(path.target_id)

    if mode is Mode.WHY:
        if start_type == "decision":
            # Asking why a decision was taken: motivation is the answer.
            return target_type in ("issue", "pull_request", "wiki_page")
        # Asking why an artifact exists: the decision that produced it is the answer.
        return target_type == "decision"

    # Impact: anything reachable is affected.
    return True


def reason(
    conn: psycopg.Connection,
    start_node_id: int,
    mode: Mode,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    as_of: datetime | None = None,
) -> Answer:
    """Answer a Why or Impact query, honouring the §5.3 explicit-first fallback order."""
    row = conn.execute(
        "SELECT node_type FROM node WHERE id = %s", (start_node_id,)
    ).fetchone()
    start_type = row["node_type"] if row else None

    edge_types = MODE_EDGES[mode]

    # Pass 1 — explicit (and corroborated) only.
    explicit_paths = _walk(
        conn,
        start_node_id,
        edge_types,
        allow_inferred=False,
        max_depth=max_depth,
        as_of=as_of,
    )
    answers = [p for p in explicit_paths if _is_answer(mode, start_type, p)]

    if answers:
        # §5.3 step 3: an explicit route exists, so inferred edges are excluded from
        # this answer entirely. Not filtered out afterwards -- never fetched.
        return Answer(
            mode=mode,
            start_node_id=start_node_id,
            paths=answers,
            used_inferred_fallback=False,
            explanation=(
                f"{len(answers)} explicit path(s) found; inferred edges not consulted"
            ),
        )

    # Pass 2 — the gap-bridging fallback. Reached only because pass 1 found nothing.
    log.debug("no explicit path from %s; expanding to inferred edges", start_node_id)
    fallback_paths = _walk(
        conn,
        start_node_id,
        edge_types,
        allow_inferred=True,
        max_depth=max_depth,
        as_of=as_of,
    )
    fallback_answers = [p for p in fallback_paths if _is_answer(mode, start_type, p)]

    if not fallback_answers:
        return Answer(
            mode=mode,
            start_node_id=start_node_id,
            paths=[],
            used_inferred_fallback=True,
            explanation="no path found, explicit or inferred",
        )

    return Answer(
        mode=mode,
        start_node_id=start_node_id,
        paths=fallback_answers,
        used_inferred_fallback=True,
        explanation=(
            f"no explicit path existed; {len(fallback_answers)} path(s) found only by "
            "admitting inferred edges"
        ),
    )
