"""Snapshots: the exact graph a published figure was measured on, as one file (issue #122).

Every §9 verdict and README figure was measured on one graph, and that graph lived only in
one machine's Docker volume. Re-ingesting pins a fresh window floor and produces a
different corpus, so losing the volume would make every published figure unreproducible.
On 2026-10-16 that nearly happened: Docker Desktop was found uninstalled, with only its
data disk left.

**Why not pg_dump.** The app image ships Debian's PostgreSQL 15 client, and pg_dump 15
refuses a 16 server. Pinning a matching client means a second apt repository. The schema
is also already fully defined by the migrations at a known checksum, so a snapshot only
has to carry the *data* and *which migrations* produced its schema. A snapshot is a zip
holding:

- `manifest.json`: format version, when, the code commit, the migration ledger
  (filename → checksum), and for every table its column list, row count and SHA-256;
- `data/<table>.copy`: that table's rows in PostgreSQL's COPY text format, which
  round-trips every type exactly.

**Import** goes only into an empty database, and in these steps:
1. Refuse a snapshot that names a migration this code doesn't have, or one whose
   checksum differs ("newer than the code").
2. Apply exactly the snapshot's migrations, then truncate whatever they seeded.
3. Load each table with triggers suspended (`session_replication_role = replica`). This
   restores the rows as they were, rather than re-deciding them under today's rules.
4. Reset every owned sequence.
5. Re-count every table and re-hash its COPY output against the manifest.

Anything that doesn't match fails the import, inside one transaction, so nothing is left
half-loaded. Newer migrations then run over the restored data through the ordinary
`dg init`.

Nothing from `.env` is written. The database holds no credentials, and export refuses if
the configured GitHub token appears anywhere in the data, so it would fail loudly rather
than leak if that ever changed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from . import migrate

FORMAT = 1
_TABLES_SQL = """
SELECT c.relname AS name
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
   AND c.relname <> 'schema_migration'
 ORDER BY c.relname
"""
_COLUMNS_SQL = """
SELECT column_name FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = %s AND is_generated = 'NEVER'
 ORDER BY ordinal_position
"""
_SEQUENCES_SQL = """
SELECT t.relname AS table_name, a.attname AS column_name, s.relname AS sequence_name
  FROM pg_class s
  JOIN pg_depend d    ON d.objid = s.oid AND d.deptype IN ('a', 'i')
  JOIN pg_class t     ON t.oid = d.refobjid
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
  JOIN pg_namespace n ON n.oid = s.relnamespace
 WHERE s.relkind = 'S' AND n.nspname = 'public'
"""


class SnapshotError(RuntimeError):
    pass


def _commit(project_dir: Path) -> str | None:
    """The checked-out commit, read from .git without needing a git binary (the image has none)."""
    git = project_dir / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        loose = git / ref
        if loose.exists():
            return loose.read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


def _tables(conn) -> list[str]:
    return [r["name"] for r in conn.execute(_TABLES_SQL).fetchall()]


def _columns(conn, table: str) -> list[str]:
    return [r["column_name"] for r in conn.execute(_COLUMNS_SQL, (table,)).fetchall()]


def _copy_out(conn, table: str, columns: list[str]) -> bytes:
    # Ordered by the whole row's text form, so two databases holding the same rows produce
    # byte-identical output whatever their physical order. That works for every column
    # type, including ones with no ordering operator.
    stmt = sql.SQL("COPY (SELECT {cols} FROM {t} ORDER BY {t}::text) TO STDOUT").format(
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)), t=sql.Identifier(table)
    )
    buf = io.BytesIO()
    with conn.cursor().copy(stmt) as copy:
        for chunk in copy:
            buf.write(bytes(chunk))
    return buf.getvalue()


def export(conn, path: Path, *, project_dir: Path | None = None) -> dict:
    """Write a snapshot of the connected database to `path`. Returns the manifest."""
    # One consistent view of every table, so a concurrent ingest can't produce a snapshot
    # that no single moment ever held.
    conn.rollback()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    token = os.environ.get("GITHUB_TOKEN") or ""
    tables = {}
    blobs = {}
    for table in _tables(conn):
        cols = _columns(conn, table)
        data = _copy_out(conn, table, cols)
        if token and token.encode() in data:
            raise SnapshotError(f"the GitHub token appears in table {table}; refusing to export it")
        blobs[table] = data
        tables[table] = {
            "columns": cols,
            "rows": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    counts = {
        r["t"]: r["n"]
        for r in conn.execute(
            "SELECT node_type::text AS t, count(*) AS n FROM node GROUP BY 1 ORDER BY 1"
        ).fetchall()
    } if "node" in tables else {}
    repos = [
        r["external_id"] for r in conn.execute(
            "SELECT external_id FROM node WHERE node_type = 'repository' ORDER BY 1"
        ).fetchall()
    ] if "node" in tables else []
    manifest = {
        "format": FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": _commit(project_dir or Path.cwd()),
        "server_version": conn.execute("SHOW server_version").fetchone()["server_version"],
        "migrations": migrate.ledger(conn),
        "repositories": repos,
        "node_counts": counts,
        "tables": tables,
    }
    conn.rollback()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
        for table, data in blobs.items():
            z.writestr(f"data/{table}.copy", data)
    tmp.replace(path)  # never leave a truncated file under the real name
    return manifest


def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("manifest.json"))


def check_compatible(manifest: dict, code: dict[str, str]) -> None:
    """Refuse a snapshot whose schema this code can't reproduce exactly."""
    if manifest.get("format") != FORMAT:
        raise SnapshotError(f"snapshot format {manifest.get('format')} is not {FORMAT}")
    unknown = sorted(set(manifest["migrations"]) - set(code))
    if unknown:
        raise SnapshotError(
            "the snapshot was taken with migrations this code doesn't have: "
            + ", ".join(unknown) + ". Check out a newer commit to import it."
        )
    changed = sorted(n for n, c in manifest["migrations"].items() if code[n] != c)
    if changed:
        raise SnapshotError(
            "these migrations differ from the ones the snapshot was taken with: "
            + ", ".join(changed)
        )


def import_(conn, path: Path, *, directory: Path | None = None) -> dict:
    """Restore `path` into the connected database, which must be empty. Returns the manifest."""
    manifest = read_manifest(path)
    check_compatible(manifest, migrate.checksums(directory))
    if migrate.ledger(conn):
        raise SnapshotError("the target database already has a schema; import needs an empty one")
    conn.commit()

    migrate.apply_all(conn, directory, only=set(manifest["migrations"]))
    present = set(_tables(conn))
    missing = sorted(set(manifest["tables"]) - present)
    if missing:
        raise SnapshotError(f"the replayed schema lacks tables: {', '.join(missing)}")

    with zipfile.ZipFile(path) as z, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in sorted(present))))
        for table, meta in manifest["tables"].items():
            stmt = sql.SQL("COPY {t} ({cols}) FROM STDIN").format(
                t=sql.Identifier(table),
                cols=sql.SQL(", ").join(map(sql.Identifier, meta["columns"])),
            )
            with conn.cursor().copy(stmt) as copy:
                copy.write(z.read(f"data/{table}.copy"))
        for r in conn.execute(_SEQUENCES_SQL).fetchall():
            conn.execute(sql.SQL(
                "SELECT setval({seq}, COALESCE((SELECT max({col}) FROM {t}), 1), "
                "(SELECT max({col}) FROM {t}) IS NOT NULL)"
            ).format(seq=sql.Literal(r["sequence_name"]), col=sql.Identifier(r["column_name"]),
                     t=sql.Identifier(r["table_name"])))
        for table, meta in manifest["tables"].items():
            data = _copy_out(conn, table, meta["columns"])
            if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                got = data.count(b"\n")
                raise SnapshotError(f"table {table} did not restore identically "
                                    f"({got} rows, expected {meta['rows']})")
    return manifest


def is_populated(conn) -> bool:
    """Whether the database holds ingested data, so a migration would change something real."""
    if conn.execute("SELECT to_regclass('public.node') AS t").fetchone()["t"] is None:
        return False
    return conn.execute("SELECT EXISTS (SELECT 1 FROM node) AS e").fetchone()["e"]


def default_path(project_dir: Path, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return project_dir / "snapshots" / f"{label}-{stamp}.dgsnap"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dg snapshot", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="action", required=True)
    e = sub.add_parser("export", help="write the whole graph to one file")
    e.add_argument("file", nargs="?", help="default: snapshots/graph-<UTC time>.dgsnap")
    i = sub.add_parser("import", help="restore a snapshot into an EMPTY database")
    i.add_argument("file")
    sub.add_parser("inspect", help="print a snapshot's manifest").add_argument("file")
    args = p.parse_args(argv)

    project = Path(os.environ.get("DG_PROJECT_DIR", "."))
    if args.action == "inspect":
        print(json.dumps(read_manifest(Path(args.file)), indent=1))
        return 0
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    from . import db

    conn = db.connect(dsn)
    try:
        if args.action == "export":
            path = Path(args.file) if args.file else default_path(project, "graph")
            m = export(conn, path, project_dir=project)
            rows = sum(t["rows"] for t in m["tables"].values())
            print(f"wrote {path}  ({path.stat().st_size:,} bytes, {rows:,} rows in "
                  f"{len(m['tables'])} tables, {len(m['migrations'])} migrations, "
                  f"commit {(m['code_commit'] or 'unknown')[:12]})")
        else:
            directory = project / "db" / "migrations"
            m = import_(conn, Path(args.file), directory=directory if directory.is_dir() else None)
            print(f"restored {args.file}: every table's rows match the manifest "
                  f"({sum(t['rows'] for t in m['tables'].values()):,} rows)")
    except (SnapshotError, psycopg.Error, OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
