"""thread_key assignment — the v1 stand-in for §5.1's "bounded time window".

The reconstructed-Decision rubric requires that every edge it relies on references
"the same PR/issue thread". A thread here is one conversation cluster: a PR, the
issues it closes, its commits, and its reviews.

Why this is DB-backed union rather than an in-memory pass: linkage is discovered out
of order and across runs. A PR ingested in March can close an issue ingested in
January, and the January run had no way to know. So union has to be able to merge two
threads that already exist and are already persisted, which is exactly a single
UPDATE. The alternative — rebuilding a DSU over the whole graph each run — would
break the bounded-backfill property by requiring a full scan.

Canonical key selection is deterministic and PR-preferring, so merging threads A and
B always yields the same winner no matter which order the runs happened in. Without
that, the same repo ingested in two different orders could produce different
thread_keys, and the rubric's clause 3 would be order-dependent — a Decision that
passes on one machine and fails on another.
"""

from __future__ import annotations

import logging
import re

import psycopg

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^thread:(\d+):(pr|issue)-(\d+)$")


def pr_key(repo_node_id: int, number: int) -> str:
    return f"thread:{repo_node_id}:pr-{number}"


def issue_key(repo_node_id: int, number: int) -> str:
    return f"thread:{repo_node_id}:issue-{number}"


def _rank(key: str) -> tuple[int, int]:
    """Lower sorts first and wins. PR keys beat issue keys; then lowest number.

    A PR is preferred as the canonical anchor because it is where implementation and
    review converge, which is what clause 2 of the rubric looks for.
    """
    m = _KEY_RE.match(key)
    if not m:
        return (2, 0)
    _, kind, number = m.groups()
    return (0 if kind == "pr" else 1, int(number))


def assign(conn: psycopg.Connection, node_id: int, key: str) -> None:
    """Give a node a thread key if it has none. Never overwrites an existing key —
    that is union's job, and overwriting would silently split an existing thread."""
    conn.execute(
        "UPDATE node SET thread_key = %s WHERE id = %s AND thread_key IS NULL",
        (key, node_id),
    )


def union(conn: psycopg.Connection, node_a: int, node_b: int) -> str | None:
    """Merge the threads containing two nodes; returns the surviving key.

    Nodes without a key are pulled into the other's thread. If neither has one there
    is nothing to merge and no key is invented — an unthreaded node correctly fails
    rubric clause 3 rather than being given a plausible-looking home.
    """
    rows = conn.execute(
        "SELECT id, thread_key FROM node WHERE id IN (%s, %s)", (node_a, node_b)
    ).fetchall()
    keys = {r["id"]: r["thread_key"] for r in rows}

    key_a, key_b = keys.get(node_a), keys.get(node_b)

    if key_a is None and key_b is None:
        return None
    if key_a == key_b:
        return key_a
    if key_a is None:
        conn.execute("UPDATE node SET thread_key = %s WHERE id = %s", (key_b, node_a))
        return key_b
    if key_b is None:
        conn.execute("UPDATE node SET thread_key = %s WHERE id = %s", (key_a, node_b))
        return key_a

    winner, loser = sorted([key_a, key_b], key=_rank)
    conn.execute("UPDATE node SET thread_key = %s WHERE thread_key = %s", (winner, loser))
    log.debug("merged thread %s into %s", loser, winner)
    return winner
