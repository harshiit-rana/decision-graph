"""Engine 1.5 — Candidate Retrieval (PRD v3.1 §5.2).

A narrowing step between the graph and the reasoning engine: given a query string,
select a plausible set of start nodes before traversal begins, rather than traversing
the whole graph.

v1 is intentionally trivial — exact match, then prefix, then trigram similarity. It is
not ranked retrieval and not embedding-based. It exists as a named seam because
traversal stops working at any scale past a toy graph without one, and retrofitting a
narrowing step after the traversal engine assumes it can start anywhere is the kind of
change that touches every caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 10

# Trigram similarity floor for the fuzzy tier. Below this the matches stop resembling
# the query at all; an empty candidate set is a better answer than a wrong start node,
# for the same reason §5.1 prefers no inferred edge to a weak one.
FUZZY_FLOOR = 0.25


@dataclass(frozen=True)
class Candidate:
    node_id: int
    node_type: str
    external_id: str
    title: str | None
    match: str  # which tier matched: exact | identifier | prefix | fuzzy
    score: float


def resolve_repo(conn: psycopg.Connection, name: str) -> int | None:
    """Look up a repository node's id by its `owner/name`. None if not ingested."""
    row = conn.execute(
        "SELECT id FROM node WHERE node_type = 'repository' AND external_id = %s",
        (name,),
    ).fetchone()
    return row["id"] if row else None


def find_candidates(
    conn: psycopg.Connection,
    query: str,
    *,
    node_types: tuple[str, ...] | None = None,
    repo_node_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """Resolve a query string to plausible start nodes, best match first.

    Tiers are tried in order and the results concatenated, so an exact title match
    always outranks a fuzzy one regardless of trigram score. `#1234` and a bare number
    resolve as identifiers, which is how an impact query normally arrives.

    `repo_node_id`, when given, restricts matches to that repository (issue #28) --
    without it, a query against a database holding more than one repo can return
    candidates from any of them, indistinguishably.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    identifier = cleaned.lstrip("#")
    type_filter = "AND node_type = ANY(%(types)s)" if node_types else ""
    repo_filter = "AND repo_node_id = %(repo)s" if repo_node_id is not None else ""
    params = {
        "q": cleaned,
        "identifier": identifier,
        "prefix": f"{cleaned}%",
        "floor": FUZZY_FLOOR,
        "limit": limit,
        "types": list(node_types) if node_types else None,
        "repo": repo_node_id,
    }

    sql = f"""
        WITH matches AS (
            SELECT id, node_type, external_id, title, 'exact' AS match, 1.0 AS score
            FROM node
            WHERE lower(title) = lower(%(q)s) {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'identifier', 0.95
            FROM node
            WHERE external_id = %(identifier)s {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'prefix', 0.75
            FROM node
            WHERE title ILIKE %(prefix)s {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'fuzzy',
                   similarity(title, %(q)s)
            FROM node
            WHERE title IS NOT NULL
              AND similarity(title, %(q)s) >= %(floor)s {type_filter} {repo_filter}
        )
        SELECT DISTINCT ON (id) id, node_type, external_id, title, match, score
        FROM matches
        ORDER BY id, score DESC
    """

    rows = conn.execute(sql, params).fetchall()
    rows.sort(key=lambda r: (-r["score"], r["id"]))

    return [
        Candidate(
            node_id=r["id"],
            node_type=r["node_type"],
            external_id=r["external_id"],
            title=r["title"],
            match=r["match"],
            score=float(r["score"]),
        )
        for r in rows[:limit]
    ]
