"""Graph writes: node/edge upserts and run bookkeeping.

Every write here is idempotent, because resume means the same page can legitimately
be processed twice — once by the run that died and once by the run that resumed it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

# Explicit-signal extractors only. The inferred path lives in inference.py and is
# gated by the database trigger installed in migration 0001.
TAG_EXPLICIT = "explicit"
TIER_EXPLICIT = "explicit"


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute("SET application_name = 'decision-graph-ingest'")
    return conn


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def upsert_node(
    conn: psycopg.Connection,
    *,
    node_type: str,
    external_id: str,
    repo_node_id: int | None = None,
    github_node_id: str | None = None,
    title: str | None = None,
    url: str | None = None,
    thread_key: str | None = None,
    source_created_at: datetime | None = None,
    source_updated_at: datetime | None = None,
    raw: Any = None,
) -> int:
    """Insert or refresh a node, returning its id.

    Conflict target mirrors ``node_identity_uidx``, including its COALESCE, so the
    expression index is actually inferred rather than silently missed.

    thread_key is deliberately COALESCEd rather than overwritten: a later union may
    already have promoted this node into a merged thread, and a plain refresh of the
    artifact must not undo that.
    """
    row = conn.execute(
        """
        INSERT INTO node (node_type, repo_node_id, external_id, github_node_id,
                          title, url, thread_key,
                          source_created_at, source_updated_at, raw)
        VALUES (%(node_type)s, %(repo_node_id)s, %(external_id)s, %(github_node_id)s,
                %(title)s, %(url)s, %(thread_key)s,
                %(source_created_at)s, %(source_updated_at)s, %(raw)s)
        ON CONFLICT (node_type, COALESCE(repo_node_id, 0), external_id)
        DO UPDATE SET
            github_node_id    = COALESCE(EXCLUDED.github_node_id, node.github_node_id),
            title             = COALESCE(EXCLUDED.title, node.title),
            url               = COALESCE(EXCLUDED.url, node.url),
            thread_key        = COALESCE(node.thread_key, EXCLUDED.thread_key),
            source_updated_at = GREATEST(
                COALESCE(EXCLUDED.source_updated_at, node.source_updated_at),
                COALESCE(node.source_updated_at, EXCLUDED.source_updated_at)),
            raw               = COALESCE(EXCLUDED.raw, node.raw),
            last_polled_at    = now()
        RETURNING id
        """,
        {
            "node_type": node_type,
            "repo_node_id": repo_node_id,
            "external_id": external_id,
            "github_node_id": github_node_id,
            "title": title,
            "url": url,
            "thread_key": thread_key,
            "source_created_at": source_created_at,
            "source_updated_at": source_updated_at,
            "raw": psycopg.types.json.Jsonb(raw) if raw is not None else None,
        },
    ).fetchone()
    assert row is not None
    return row["id"]


def upsert_detail(conn: psycopg.Connection, table: str, node_id: int, **columns: Any) -> None:
    """Upsert the per-type detail row for a node.

    Table names come only from this module's extractors, never from API payloads.
    """
    cols = ["node_id", *columns.keys()]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (node_id) DO UPDATE SET {updates}",
        {"node_id": node_id, **columns},
    )


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def upsert_explicit_edge(
    conn: psycopg.Connection,
    *,
    src: int,
    dst: int,
    edge_type: str,
    extractor: str,
    source_ref: str | None = None,
    observed_at: datetime | None = None,
) -> bool:
    """Create (or refresh) an explicit edge. Returns True if a new edge was created.

    Self-loops are dropped here rather than left to hit the CHECK constraint: a PR
    body containing its own number is common and is not an error worth aborting on.

    Conflict target matches the partial unique index ``edge_current_uidx``, so only
    currently-valid edges collide. Superseded edges keep their history via valid_to.
    """
    if src == dst:
        return False

    row = conn.execute(
        """
        INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                          extractor, source_ref, observed_at, valid_from)
        VALUES (%(src)s, %(dst)s, %(edge_type)s, %(tag)s, %(tier)s,
                %(extractor)s, %(source_ref)s, %(observed_at)s,
                COALESCE(%(observed_at)s, now()))
        ON CONFLICT (src_node_id, dst_node_id, edge_type) WHERE valid_to IS NULL
        DO UPDATE SET
            source_ref  = COALESCE(EXCLUDED.source_ref, edge.source_ref),
            observed_at = COALESCE(EXCLUDED.observed_at, edge.observed_at)
        RETURNING (xmax = 0) AS inserted
        """,
        {
            "src": src,
            "dst": dst,
            "edge_type": edge_type,
            "tag": TAG_EXPLICIT,
            "tier": TIER_EXPLICIT,
            "extractor": extractor,
            "source_ref": source_ref,
            "observed_at": observed_at,
        },
    ).fetchone()
    return bool(row and row["inserted"])


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


def start_run(conn: psycopg.Connection, repo_node_id: int) -> int:
    row = conn.execute(
        "INSERT INTO ingestion_run (repo_node_id) VALUES (%s) RETURNING id",
        (repo_node_id,),
    ).fetchone()
    assert row is not None
    return row["id"]


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    status: str,
    nodes: int,
    edges: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_run
        SET finished_at = now(), status = %s, nodes_upserted = %s,
            edges_upserted = %s, error = %s
        WHERE id = %s
        """,
        (status, nodes, edges, error, run_id),
    )
