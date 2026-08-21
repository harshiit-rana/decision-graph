"""Migration runner with a ledger, so `dg init` is safe to re-run.

The migrations were written to be applied once, by hand, in filename order. That was fine
while one person ran them against one database; it is not fine for a setup command anyone
can run twice. `CREATE TABLE node (...)` fails the second time, and the failure reads like a
bug rather than "already done".

Two constraints shaped this:

**Each migration opens its own transaction.** Every file begins `BEGIN;` and ends `COMMIT;`,
so they must run with autocommit on and be left to manage themselves. The runner cannot wrap
a file in a transaction of its own and cannot record the ledger row inside the file's
transaction.

**So the ledger can never be perfectly atomic with the migration.** Rather than pretend
otherwise, every migration declares a *sentinel* — an object that exists iff it has run —
and the runner probes it before applying. That single mechanism covers three cases at once:
a database migrated by hand before this runner existed, a crash between a file committing
and its ledger row landing, and an ordinary fresh apply. Nothing is ever re-applied blindly,
and nothing is ever marked done on trust.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path("/work/db/migrations")

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    adopted     boolean NOT NULL DEFAULT false
)
"""

# One probe per migration: true iff that migration has already run. Keyed by the numeric
# prefix so renaming a file's descriptive suffix does not silently break detection.
#
# A migration missing from this map can never be adopted — it will simply be applied. That
# is the safe direction to fail: a redundant apply errors loudly, whereas a wrong adoption
# skips real schema changes forever.
SENTINELS: dict[str, str] = {
    "0001": "to_regclass('public.node') IS NOT NULL",
    "0002": "to_regproc('public.decision_rubric') IS NOT NULL",
    "0003": """EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ingestion_cursor' AND column_name = 'phase')""",
    "0004": """EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'node_repository_owns_itself')""",
    "0005": "to_regclass('public.pending_reference') IS NOT NULL",
    "0006": "to_regproc('public.apply_corroboration') IS NOT NULL",
    "0007": "to_regproc('public.thread_landed') IS NOT NULL",
    "0008": "to_regproc('public.retract_unsupported_decisions') IS NOT NULL",
    "0009": "to_regclass('public.decision_thread_uidx') IS NOT NULL",
    "0011": """EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'decision_thread_key_matches_repo')""",
}


@dataclass
class MigrationResult:
    applied: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        return not self.applied and not self.adopted


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def discover(directory: Path | None = None) -> list[Path]:
    d = Path(directory) if directory else MIGRATIONS_DIR
    if not d.is_dir():
        raise FileNotFoundError(f"no migrations directory at {d}")
    files = sorted(d.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no .sql migrations in {d}")
    return files


def _probe(conn: psycopg.Connection, filename: str) -> bool:
    expr = SENTINELS.get(filename[:4])
    if expr is None:
        return False
    row = conn.execute(f"SELECT ({expr}) AS present").fetchone()
    return bool(row and row["present"])


def _ledger(conn: psycopg.Connection) -> dict[str, str]:
    return {
        r["filename"]: r["checksum"]
        for r in conn.execute("SELECT filename, checksum FROM schema_migration").fetchall()
    }


def pending(conn: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Migrations not yet recorded as applied. Read-only — used by `dg doctor`."""
    conn.execute(LEDGER_DDL)
    conn.commit()
    done = set(_ledger(conn))
    return [p.name for p in discover(directory) if p.name not in done]


def apply_all(conn: psycopg.Connection, directory: Path | None = None) -> MigrationResult:
    """Bring the database up to date. Safe to run repeatedly."""
    files = discover(directory)
    result = MigrationResult()

    was_autocommit = conn.autocommit
    conn.execute(LEDGER_DDL)
    conn.commit()
    # Each migration file opens and closes its own transaction, so the connection must not
    # already be inside one.
    conn.autocommit = True

    try:
        ledger = _ledger(conn)

        for path in files:
            sql = path.read_text(encoding="utf-8")
            digest = _checksum(sql)

            if path.name in ledger:
                if ledger[path.name] != digest:
                    # The file changed after it was applied, so the database and the
                    # repository no longer agree. Nothing here can reconcile that; report
                    # it rather than guessing which one is right.
                    result.changed.append(path.name)
                result.skipped.append(path.name)
                continue

            if _probe(conn, path.name):
                # Already in the database but not in the ledger: either applied by hand
                # before this runner existed, or recorded-but-not-committed after a crash.
                conn.execute(
                    "INSERT INTO schema_migration (filename, checksum, adopted) "
                    "VALUES (%s, %s, true) ON CONFLICT (filename) DO NOTHING",
                    (path.name, digest),
                )
                result.adopted.append(path.name)
                continue

            log.info("applying %s", path.name)
            try:
                conn.execute(sql)
            except Exception:
                log.error(
                    "migration %s failed; it manages its own transaction, so the database "
                    "is unchanged and nothing was recorded",
                    path.name,
                )
                raise
            conn.execute(
                "INSERT INTO schema_migration (filename, checksum) VALUES (%s, %s)",
                (path.name, digest),
            )
            result.applied.append(path.name)
    finally:
        conn.autocommit = was_autocommit

    return result
