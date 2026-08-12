"""Decision synthesis — promote qualifying thread clusters to Decision nodes (issue #6).

This is the step where the system stops recording artifacts and starts asserting that a
decision occurred, so it is the step the PRD's top risk row is about. Two properties keep
it honest:

**No model judgment.** Selection is a SQL query over explicit edge types. There is no
significance filter — a one-line fix that closes an issue is promoted exactly like a
context-model rewrite. Filtering for "important" decisions would be precisely the
judgment call §5.1 exists to exclude; evidence tier is how quality is conveyed, not
creation eligibility.

**The guard, not this module, is the authority.** The selection query is written to
satisfy the rubric, but it is not trusted to. Each Decision is committed against the
deferred constraint trigger from migration 0002, so a cluster that does not actually
meet the bar is refused by the database even if the query here is wrong. That is the
difference between a rule and a rule that is enforced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from . import db

log = logging.getLogger(__name__)


# One row per promotable cluster.
#
# A thread qualifies when it contains a `closes` edge pointing at an issue, with both
# endpoints in that same thread. The issue answers "why" (Motivation, rubric clause 1);
# the closing PR or commit answers "what was done" (Implementation, clause 2); and both
# sitting in one thread satisfies clause 3 by construction.
#
# DISTINCT ON collapses threads where several PRs close the same issue -- flask has
# issues closed by up to five sources -- to one Decision per thread. Tie-breaks are
# deterministic so that re-running, or ingesting in a different order, promotes the
# same cluster to the same node.
CANDIDATE_QUERY = """
SELECT DISTINCT ON (src.thread_key)
       src.thread_key                       AS thread_key,
       e.src_node_id                        AS implementer_id,
       e.dst_node_id                        AS motivator_id,
       dst.title                            AS motivator_title,
       COALESCE(pr.merged_at, pr.closed_at, iss.closed_at) AS decided_at
FROM edge e
JOIN node src ON src.id = e.src_node_id
JOIN node dst ON dst.id = e.dst_node_id
LEFT JOIN pull_request pr ON pr.node_id = src.id
LEFT JOIN issue iss       ON iss.node_id = dst.id
WHERE e.edge_type = 'closes'
  AND e.tag       = 'explicit'
  AND e.valid_to IS NULL
  AND dst.node_type = 'issue'
  AND src.thread_key IS NOT NULL
  AND src.thread_key = dst.thread_key
ORDER BY src.thread_key,
         (src.node_type = 'pull_request') DESC,  -- a PR is a better implementer than a bare commit
         dst.id,
         src.id
"""


@dataclass
class SynthesisResult:
    created: int = 0
    refreshed: int = 0
    refused: int = 0
    refusals: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.refusals is None:
            self.refusals = []


def synthesize(conn: psycopg.Connection, repo_node_id: int) -> SynthesisResult:
    """Promote every qualifying cluster. Idempotent."""
    candidates = conn.execute(CANDIDATE_QUERY).fetchall()
    log.info("promotable clusters: %s", len(candidates))

    result = SynthesisResult()

    for row in candidates:
        # Deferred by default so the node can be written before the edges that justify
        # it; forced immediate below, inside the savepoint, so a refusal is catchable
        # here instead of aborting the whole batch at COMMIT.
        conn.execute("SET CONSTRAINTS ALL DEFERRED")
        try:
            with conn.transaction():
                created = _promote(conn, repo_node_id, row)
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            if created:
                result.created += 1
            else:
                result.refreshed += 1
        except psycopg.errors.CheckViolation as exc:
            # The guard rejected the cluster. Recorded, not raised: one bad cluster must
            # not stop the rest, and the message names which rubric clause failed.
            result.refused += 1
            result.refusals.append(f"{row['thread_key']}: {str(exc).strip()[:160]}")
            log.warning("refused %s: %s", row["thread_key"], str(exc).strip()[:160])

    return result


def _promote(conn: psycopg.Connection, repo_node_id: int, row: dict) -> bool:
    """Create or refresh one Decision plus its two rubric edges. Returns True if new."""
    thread_key = row["thread_key"]

    existing = conn.execute(
        "SELECT id FROM node WHERE node_type = 'decision' AND repo_node_id = %s "
        "AND external_id = %s",
        (repo_node_id, thread_key),
    ).fetchone()

    # external_id is the thread_key, so a cluster always promotes to the same node.
    decision_node_id = db.upsert_node(
        conn,
        node_type="decision",
        repo_node_id=repo_node_id,
        external_id=thread_key,
        title=row["motivator_title"],
        thread_key=thread_key,
        source_created_at=row["decided_at"],
    )

    conn.execute(
        """
        INSERT INTO decision (node_id, status, summary, decided_at)
        VALUES (%s, 'reconstructed', %s, %s)
        ON CONFLICT (node_id) DO UPDATE
            SET summary = EXCLUDED.summary, decided_at = EXCLUDED.decided_at
        """,
        (decision_node_id, row["motivator_title"], row["decided_at"]),
    )

    db.upsert_explicit_edge(
        conn,
        src=decision_node_id,
        dst=row["motivator_id"],
        edge_type="motivated_by",
        extractor="synthesis_closes_cluster",
        source_ref=thread_key,
        observed_at=row["decided_at"],
    )
    db.upsert_explicit_edge(
        conn,
        src=decision_node_id,
        dst=row["implementer_id"],
        edge_type="implemented_by",
        extractor="synthesis_closes_cluster",
        source_ref=thread_key,
        observed_at=row["decided_at"],
    )

    return existing is None
