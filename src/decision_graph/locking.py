"""One writer at a time (#163, ADR 0008).

Two `dg ingest` runs, or a poll and the webhook receiver's post-processing, must not write at once:
thread unions and Decision synthesis were not designed for concurrent writers. The mechanism is a
Postgres session-level advisory lock, one per repository, so it needs no table, no queue and no
cleanup. It is released when the connection drops, so a process that is killed frees it; there is
no stale lock to find and remove.

It is a lock for the *poll and the post-processing pass*. Webhook extraction is not behind it: those
writes are per-artifact and ordered by `updated_at` (#120), which is what makes them safe to
interleave with a poll.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg

# The first of the lock's two integer keys: "decision-graph writer" in ASCII, so it cannot collide
# with another application's use of single-key advisory locks. The second is a hash of the repository.
LOCK_NAMESPACE = 0x44474957  # "DGIW"


def try_writer_lock(conn: psycopg.Connection, repo: str) -> bool:
    """Take the lock for `repo` if nobody holds it. Never waits."""
    row = conn.execute(
        "SELECT pg_try_advisory_lock(%s, hashtext(%s)) AS got", (LOCK_NAMESPACE, repo)
    ).fetchone()
    return bool(row["got"])


def release_writer_lock(conn: psycopg.Connection, repo: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", (LOCK_NAMESPACE, repo))


@contextmanager
def writer_lock(conn: psycopg.Connection, repo: str) -> Iterator[bool]:
    """`with writer_lock(conn, repo) as got:`. Releases on exit only if it was taken."""
    got = try_writer_lock(conn, repo)
    try:
        yield got
    finally:
        if got:
            try:
                conn.rollback()  # an aborted transaction would refuse the unlock
                release_writer_lock(conn, repo)
                conn.commit()
            except Exception:  # noqa: BLE001 - the connection is closing; the server frees it
                pass
