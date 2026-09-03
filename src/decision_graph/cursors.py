"""Cursor management: bounded backfill and resume (PRD v3.1 §8).

Resume is the mechanism, not a recovery path bolted on. The same cursor drives the
first backfill and every steady-state poll thereafter; the only difference is where
it starts. That means the resume path is exercised on every single run, so it cannot
quietly rot the way a rarely-taken recovery branch would.

Three strategies, because the GitHub endpoints genuinely differ:

  UPDATED_ASC     issues, pull requests
                  `since` + sort=updated&direction=asc. Backfill is a forward walk
                  from the window floor, which is the same shape as a steady-state
                  poll from the watermark. No phase transition needed.

  COMMITTED_DESC  commits
                  `since`/`until` supported but results are always newest-first, so
                  a backfill must page *backwards* with `until`, checkpointing the
                  oldest row seen. Once the floor is reached it flips to `steady`
                  and polls forward. This is the only strategy that uses `phase`.

  FULL            releases, workflows, CODEOWNERS
                  Small, unwindowed, refreshed whole each run; ETag makes the
                  no-change case cost one request.

Because only one of the three has a phase to be in, `phase` is NULL for the other two
rather than parked on a default. It used to hold 'backfill' forever for issues and pulls,
which nothing could retract and which the recall audit reasonably misread as a stalled
ingestion (#47, migration 0012). A column that cannot change is worse than an absent one:
it invites exactly that inference.

The window floor is persisted on first contact rather than recomputed per run. If it
were recomputed, resuming a backfill a week later would move the floor forward and
leave an unfetched hole in the middle of the window that nothing would ever revisit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import psycopg

log = logging.getLogger(__name__)


class Strategy(Enum):
    UPDATED_ASC = "updated_asc"
    COMMITTED_DESC = "committed_desc"
    FULL = "full"


RESOURCE_STRATEGY: dict[str, Strategy] = {
    "issues": Strategy.UPDATED_ASC,
    "pulls": Strategy.UPDATED_ASC,
    "commits": Strategy.COMMITTED_DESC,
    "releases": Strategy.FULL,
    "workflows": Strategy.FULL,
    "codeowners": Strategy.FULL,
    # Reviews have no independent cursor: they are fetched per-PR, driven by whichever
    # PRs the pulls cursor surfaced this run.
}


@dataclass
class Cursor:
    repo_node_id: int
    resource: str
    phase: str | None
    window_floor: datetime | None
    backfill_cursor: datetime | None
    steady_watermark: datetime | None
    last_etag: str | None

    @property
    def strategy(self) -> Strategy:
        return RESOURCE_STRATEGY[self.resource]

    @property
    def is_backfilling(self) -> bool:
        return self.phase == "backfill"


def initial_phase(resource: str) -> str | None:
    """The phase a cursor is born in, or None where the field does not apply.

    Only COMMITTED_DESC has a backfill to be in the middle of: it pages backwards and has a
    floor to reach, so 'backfill' -> 'steady' is a transition it genuinely makes. UPDATED_ASC
    arrives in steady state and never leaves, because its backfill and its steady poll are
    the same forward walk seeded differently; FULL has no window at all.

    Storing 'backfill' for those was a claim nothing could ever retract — the audit read it
    as a stalled ingestion (#47). NULL is the only value that declines to make the claim;
    'steady' would assert a floor had been reached, which is equally untrue.
    """
    return "backfill" if RESOURCE_STRATEGY[resource] is Strategy.COMMITTED_DESC else None


def load(
    conn: psycopg.Connection, repo_node_id: int, resource: str, backfill_months: int
) -> Cursor:
    """Fetch the cursor, creating it (and pinning the window floor) on first contact."""
    floor = datetime.now(timezone.utc) - timedelta(days=30 * backfill_months)

    row = conn.execute(
        """
        INSERT INTO ingestion_cursor (repo_node_id, resource, window_floor, phase)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (repo_node_id, resource) DO UPDATE
            SET updated_at = now()
        RETURNING phase, window_floor, backfill_cursor, steady_watermark, last_etag
        """,
        (
            repo_node_id,
            resource,
            floor,
            initial_phase(resource),
        ),
    ).fetchone()
    assert row is not None

    return Cursor(
        repo_node_id=repo_node_id,
        resource=resource,
        phase=row["phase"],
        window_floor=row["window_floor"],
        backfill_cursor=row["backfill_cursor"],
        steady_watermark=row["steady_watermark"],
        last_etag=row["last_etag"],
    )


def query_params(cursor: Cursor) -> dict[str, Any]:
    """Translate cursor state into GitHub query parameters."""
    if cursor.strategy is Strategy.UPDATED_ASC:
        # Resume point, or the window floor on a first run. Ascending order is what
        # makes a partial run safe to checkpoint: everything before the last row
        # processed is known-complete.
        since = cursor.steady_watermark or cursor.window_floor
        params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "asc"}
        if since:
            params["since"] = _iso(since)
        return params

    if cursor.strategy is Strategy.COMMITTED_DESC:
        if cursor.is_backfilling:
            params = {}
            if cursor.window_floor:
                params["since"] = _iso(cursor.window_floor)
            # Walk backwards from wherever the last run stopped.
            if cursor.backfill_cursor:
                params["until"] = _iso(cursor.backfill_cursor)
            return params
        return {"since": _iso(cursor.steady_watermark)} if cursor.steady_watermark else {}

    return {}


def advance_backfill(
    conn: psycopg.Connection, cursor: Cursor, oldest_seen: datetime, newest_seen: datetime
) -> None:
    """Checkpoint a backfill page. Called after each page, hence resumable per page."""
    cursor.backfill_cursor = oldest_seen
    # Track the high-water mark during backfill too, so the eventual flip to steady
    # does not re-walk everything the backfill already covered.
    if cursor.steady_watermark is None or newest_seen > cursor.steady_watermark:
        cursor.steady_watermark = newest_seen

    conn.execute(
        """
        UPDATE ingestion_cursor
        SET backfill_cursor = %s, steady_watermark = %s, updated_at = now()
        WHERE repo_node_id = %s AND resource = %s
        """,
        (cursor.backfill_cursor, cursor.steady_watermark, cursor.repo_node_id, cursor.resource),
    )


def advance_steady(
    conn: psycopg.Connection, cursor: Cursor, watermark: datetime, etag: str | None = None
) -> None:
    if cursor.steady_watermark is not None and watermark <= cursor.steady_watermark:
        return
    cursor.steady_watermark = watermark
    conn.execute(
        """
        UPDATE ingestion_cursor
        SET steady_watermark = %s, last_etag = COALESCE(%s, last_etag), updated_at = now()
        WHERE repo_node_id = %s AND resource = %s
        """,
        (watermark, etag, cursor.repo_node_id, cursor.resource),
    )


def complete_backfill(conn: psycopg.Connection, cursor: Cursor) -> None:
    """Window floor reached: switch to forward polling for good."""
    if not cursor.is_backfilling:
        return
    cursor.phase = "steady"
    conn.execute(
        """
        UPDATE ingestion_cursor
        SET phase = 'steady', updated_at = now()
        WHERE repo_node_id = %s AND resource = %s
        """,
        (cursor.repo_node_id, cursor.resource),
    )
    log.info("%s: backfill complete, now polling forward", cursor.resource)


def set_etag(conn: psycopg.Connection, cursor: Cursor, etag: str | None) -> None:
    if not etag:
        return
    cursor.last_etag = etag
    conn.execute(
        "UPDATE ingestion_cursor SET last_etag = %s, updated_at = now() "
        "WHERE repo_node_id = %s AND resource = %s",
        (etag, cursor.repo_node_id, cursor.resource),
    )


def bind_run(conn: psycopg.Connection, cursor: Cursor, run_id: int) -> None:
    conn.execute(
        "UPDATE ingestion_cursor SET last_run_id = %s WHERE repo_node_id = %s AND resource = %s",
        (run_id, cursor.repo_node_id, cursor.resource),
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
