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
         -- A MERGED pull request is the implementer. Ordering by node id first (as this
         -- did originally) picked whichever artifact happened to be written earliest,
         -- which named an unmerged PR in 5 threads that contained a merged one --
         -- including flagship F2, where PR 5794 (never merged) was credited over PR 5777
         -- (merged 2025-08-18). Attribution has to follow what landed, not insertion order.
         (pr.merged_at IS NOT NULL) DESC,
         (src.node_type = 'pull_request') DESC,  -- then a PR over a bare commit
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


def retract_unsupported(conn: psycopg.Connection) -> tuple[int, list[str]]:
    """Withdraw Decisions that no longer satisfy the rubric (issue #17).

    Must run BEFORE synthesis. The deferred guard only re-validates rows something
    touches, so a Decision asserted under an older, looser rule survives indefinitely
    unless it is explicitly revisited. A claim the graph no longer supports has to go.
    """
    row = conn.execute("SELECT * FROM retract_unsupported_decisions()").fetchone()
    count = int(row["retracted"]) if row else 0
    threads = list(row["thread_keys"]) if row and row["thread_keys"] else []
    if count:
        log.warning("retracted %s Decision(s) no longer supported by evidence", count)
        for t in threads:
            log.warning("  retracted %s", t)
    return count, threads


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


# A release note that itemises the issue a Decision was motivated by IS the formal
# artifact §5.1 requires for `explicit` status -- "release note" is named there
# alongside ADR and RFC.
#
# This UPGRADES an existing reconstructed Decision rather than creating a second one.
# The decision is the same decision; what changed is that a formal record of it has been
# found. Creating a parallel explicit Decision would assert that two decisions occurred
# where the evidence shows one.
#
# Note this only reaches Decisions whose motivating artifact was itemised in a release
# body. flask's release notes are mostly boilerplate pointing at CHANGES.rst, so
# coverage is thin -- see the §9 note in the README.
EXPLICIT_UPGRADE_QUERY = """
SELECT DISTINCT ON (d.node_id)
       d.node_id   AS decision_node_id,
       rel.id      AS release_node_id,
       rel.title   AS release_title,
       art.external_id AS via_artifact
FROM edge e
JOIN node art ON art.id = e.src_node_id
JOIN node rel ON rel.id = e.dst_node_id
JOIN node dn  ON dn.thread_key = art.thread_key AND dn.node_type = 'decision'
JOIN decision d ON d.node_id = dn.id
WHERE e.edge_type = 'deployed_by'
  AND e.tag       = 'explicit'
  AND e.valid_to IS NULL
  AND rel.node_type = 'release'
  AND art.thread_key IS NOT NULL
  AND d.status = 'reconstructed'
ORDER BY d.node_id, rel.id
"""


def upgrade_explicit(conn: psycopg.Connection, repo_node_id: int) -> int:
    """Promote reconstructed Decisions to `explicit` where a release note records them.

    Returns the number upgraded. Idempotent: the query only selects Decisions still in
    `reconstructed` status, so a second run finds nothing to do.
    """
    rows = conn.execute(EXPLICIT_UPGRADE_QUERY).fetchall()

    for row in rows:
        # status and source_artifact_node_id must move in ONE statement --
        # decision_explicit_needs_artifact (migration 0001) rejects an explicit
        # Decision without an artifact, so setting them separately would fail.
        conn.execute(
            """
            UPDATE decision
            SET status = 'explicit', source_artifact_node_id = %s
            WHERE node_id = %s
            """,
            (row["release_node_id"], row["decision_node_id"]),
        )
        # Keep the artifact reachable by traversal, not only by the FK column.
        db.upsert_explicit_edge(
            conn,
            src=row["decision_node_id"],
            dst=row["release_node_id"],
            edge_type="deployed_by",
            extractor="synthesis_release_note",
            source_ref=f"release:{row['release_title']} via #{row['via_artifact']}",
        )
        log.info(
            "decision %s upgraded to explicit via release %s",
            row["decision_node_id"],
            row["release_title"],
        )

    return len(rows)


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

    # Withdraw rubric edges this Decision used to claim but no longer selects.
    #
    # Upserting the correct edge is not enough: a DIFFERENT destination is a different
    # row, so the old edge simply survives alongside the new one. When the implementer
    # tie-break changed to prefer merged PRs, every re-pointed Decision ended up
    # asserting two implementers at once -- F2 credited both PR 5794 (never merged) and
    # PR 5777 (merged). Idempotent for an unchanged selection is not the same as
    # idempotent for a changed one.
    #
    # Superseded edges are time-versioned rather than deleted, so the graph keeps a
    # record of what it used to claim. valid_from < now() excludes edges created in this
    # same transaction, which cannot be closed (the 0001 CHECK requires valid_to >
    # valid_from, and now() is frozen per transaction).
    conn.execute(
        """
        UPDATE edge
        SET valid_to = now()
        WHERE src_node_id = %(decision)s
          AND edge_type IN ('motivated_by', 'implemented_by')
          AND valid_to IS NULL
          AND valid_from < now()
          AND dst_node_id NOT IN (%(motivator)s, %(implementer)s)
        """,
        {
            "decision": decision_node_id,
            "motivator": row["motivator_id"],
            "implementer": row["implementer_id"],
        },
    )

    return existing is None
