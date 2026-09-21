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
import re
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 10

# Trigram similarity floor for the fuzzy tier. Below this the matches stop resembling
# the query at all; an empty candidate set is a better answer than a wrong start node,
# for the same reason §5.1 prefers no inferred edge to a weak one.
FUZZY_FLOOR = 0.25


# An artifact reference that arrived inside a sentence. Two forms, both explicit:
# a `#` immediately before digits, or one of the artifact words followed by a number.
#
# Bare digits are deliberately absent. "change default redirect code to 303" is a real
# title in this graph, and treating its 303 as an identifier would answer a title search
# with whatever artifact happens to be numbered 303 -- trading this bug for its mirror
# image. A number only counts as a reference when the query says it is one.
_REFERENCE = re.compile(
    r"#(\d+)|\b(?:issue|issues|pr|prs|pull\s+request|pull)\s*#?(\d+)\b",
    re.IGNORECASE,
)


def identifiers_in(query: str) -> list[str]:
    """Every explicitly-marked artifact number in a query, in order, deduplicated.

    The whole query is included when it is itself an identifier, which is the case that
    already worked: `#6143` and `6143`. What did not work was any phrasing around it, and
    the tool's own output is such a phrasing -- `trace.ref` renders `issue #6143`, so
    pasting a reference out of one command into another returned a different artifact
    (issue #96). `dg ask` hit it hardest: a model asked for a search term writes
    "issue 6143", not "#6143".
    """
    found: list[str] = []
    whole = query.strip().lstrip("#")
    if whole.isdigit():
        found.append(whole)
    for hashed, worded in _REFERENCE.findall(query):
        number = hashed or worded
        if number not in found:
            found.append(number)
    return found


# An abbreviated commit sha. Seven is not arbitrary: it is git's default abbreviation and
# exactly what `trace.ref` prints, so this floor covers every commit the tool displays.
#
# The floor is also the safety. Every decimal string is valid hex, so a shorter one would
# read `6143` as a candidate sha prefix and offer whatever commit happens to start with
# those digits beside the issue the reader asked for. Issue numbers do not reach seven
# digits on this corpus, and the exact-identifier tier outranks this one regardless.
# Optionally introduced by the word, because `trace.ref` prints `commit eca5fd1` and that
# whole string is what a reader copies. Accepting only the bare sha fixed the identifier
# without fixing the rendering it came from -- the same half-fix `identifiers_in` exists to
# avoid for `issue #6143`.
_ABBREVIATED_SHA = re.compile(r"^(?:commit\s+)?([0-9a-f]{7,40})$", re.IGNORECASE)


def sha_prefix_of(query: str) -> str | None:
    """The query as a commit-sha prefix, or None when it cannot be one.

    `trace.ref` renders every commit as `commit eca5fd1` because a 40-character sha "is an
    identifier but not a readable one". Neither that nor the bare `eca5fd1` resolved
    anywhere: the identifier tier compares `external_id` for equality and the prefix tier
    matches titles, so the one form the tool prints could not be typed back in (#102).
    """
    found = _ABBREVIATED_SHA.match(query.strip())
    if not found:
        return None
    # Lowercased because shas are stored canonically lowercase and LIKE is case-sensitive.
    # `git show ECA5FD1` resolves, so typing it here must not fail -- and it would have
    # failed as "Nothing in the graph matches", which is the false-statement family again.
    return found.group(1).lower()


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
    resolve as identifiers, which is how an impact query normally arrives, and so does a
    number named inside a phrase -- `issue #1234`, `PR 1234` -- which is how the tool's own
    output and `dg ask` both write one (issue #96). An abbreviated commit sha resolves the
    way git resolves one, against `external_id`, because `commit eca5fd1` is the only form
    of a commit the tool ever shows (issue #102); an ambiguous prefix returns every commit
    it matches rather than choosing, which is also what git does.

    Exact title stays above the identifier tier on purpose: 35 titles here carry a `#N`,
    mostly squash-merge subjects like `Docs typo/markup fixes (#5829)`, and those must keep
    matching on their own text rather than on the pull request they mention.

    A node can match several tiers at once -- a title equal to the query scores 1.0 on
    `exact` and, because `similarity` of identical strings is also 1.0, on `fuzzy` too. The
    `tier` column breaks that tie so the label names the strongest tier the node matched
    rather than whichever row the planner happened to keep (issue #98). It is the LAST
    ordering key, after `score`, so ranking between different nodes is untouched: only the
    word a reader is shown changes.

    `repo_node_id`, when given, restricts matches to that repository (issue #28) --
    without it, a query against a database holding more than one repo can return
    candidates from any of them, indistinguishably.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    # A query is allowed to name an artifact the way the tool itself prints one.
    identifiers = identifiers_in(cleaned) or [cleaned.lstrip("#")]
    type_filter = "AND node_type = ANY(%(types)s)" if node_types else ""
    repo_filter = "AND repo_node_id = %(repo)s" if repo_node_id is not None else ""
    params = {
        "q": cleaned,
        "identifiers": identifiers,
        # Built here rather than in SQL so the pattern needs no `%` escaping inside an
        # f-string that psycopg is also reading `%(name)s` placeholders out of.
        "sha_like": (lambda pre: f"{pre}%" if pre else None)(sha_prefix_of(cleaned)),
        "prefix": f"{cleaned}%",
        "floor": FUZZY_FLOOR,
        "limit": limit,
        "types": list(node_types) if node_types else None,
        "repo": repo_node_id,
    }

    sql = f"""
        WITH matches AS (
            SELECT id, node_type, external_id, title, 'exact' AS match, 1.0 AS score, 1 AS tier
            FROM node
            WHERE lower(title) = lower(%(q)s) {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'identifier', 0.95, 2
            FROM node
            WHERE external_id = ANY(%(identifiers)s) {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'sha', 0.93, 3
            FROM node
            WHERE %(sha_like)s::text IS NOT NULL
              AND node_type = 'commit'
              AND external_id LIKE %(sha_like)s::text {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'prefix', 0.75, 4
            FROM node
            WHERE title ILIKE %(prefix)s {type_filter} {repo_filter}

            UNION ALL
            SELECT id, node_type, external_id, title, 'fuzzy',
                   similarity(title, %(q)s), 5
            FROM node
            WHERE title IS NOT NULL
              AND similarity(title, %(q)s) >= %(floor)s {type_filter} {repo_filter}
        )
        SELECT DISTINCT ON (id) id, node_type, external_id, title, match, score
        FROM matches
        ORDER BY id, score DESC, tier
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
