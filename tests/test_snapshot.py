"""Snapshots round-trip the graph exactly (issue #122).

The DB-gated test creates two throwaway databases. It migrates and seeds one, exports it,
imports into the other, and compares every table's COPY output byte for byte. The seeded
values are chosen to break a careless format: tabs, newlines, backslashes, Unicode, JSONB,
NULLs and timestamps. It also checks that a restored database can take new rows (the
sequences were reset), that import refuses a non-empty target, and that export refuses
data containing the configured token.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from decision_graph import migrate, snapshot

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
from tests.superuser import requires_superuser  # noqa: E402

DSN = os.environ.get("DATABASE_URL")


class CompatibilityTest(unittest.TestCase):
    CODE = {"0001_a.sql": "aaa", "0002_b.sql": "bbb"}

    def manifest(self, migrations, fmt=snapshot.FORMAT):
        return {"format": fmt, "migrations": migrations}

    def test_an_older_snapshot_is_accepted(self) -> None:
        snapshot.check_compatible(self.manifest({"0001_a.sql": "aaa"}), self.CODE)

    def test_a_snapshot_newer_than_the_code_is_refused(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotError, "doesn't have: 0003_c.sql"):
            snapshot.check_compatible(
                self.manifest({**self.CODE, "0003_c.sql": "ccc"}), self.CODE)

    def test_a_changed_migration_is_refused(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotError, "differ.*0002_b.sql"):
            snapshot.check_compatible(self.manifest({"0002_b.sql": "XXX"}), self.CODE)

    def test_an_unknown_format_is_refused(self) -> None:
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.check_compatible(self.manifest({}, fmt=99), self.CODE)

    def test_the_shipped_migrations_have_checksums(self) -> None:
        self.assertEqual(len(migrate.checksums(MIGRATIONS)), len(migrate.discover(MIGRATIONS)))


class CommitTest(unittest.TestCase):
    def test_reads_a_branch_ref_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git = Path(d, ".git")
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (git / "refs" / "heads" / "main").write_text("abc123\n")
            self.assertEqual(snapshot._commit(Path(d)), "abc123")

    def test_reads_a_packed_ref(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git = Path(d, ".git")
            git.mkdir()
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (git / "packed-refs").write_text("# pack\ndef456 refs/heads/main\n")
            self.assertEqual(snapshot._commit(Path(d)), "def456")

    def test_no_repository_is_none_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(snapshot._commit(Path(d)))


def _dsn_for(database: str) -> str:
    parts = urlsplit(DSN)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class RoundTripTest(unittest.TestCase):
    SRC, DST = "dg_snapshot_test_src", "dg_snapshot_test_dst"

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        cls.admin = psycopg.connect(DSN, autocommit=True)
        for name in (cls.SRC, cls.DST):
            cls.admin.execute(f"DROP DATABASE IF EXISTS {name}")
            cls.admin.execute(f"CREATE DATABASE {name}")

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (cls.SRC, cls.DST):
            cls.admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        cls.admin.close()

    def setUp(self) -> None:
        from decision_graph import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name, "graph.dgsnap")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn) -> None:
        repo = conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES ('repository', 'o/r', 'o/r') "
            "RETURNING id").fetchone()["id"]
        awkward = "tab\there\nnewline \\backslash é中 \"quotes\" 'single'"
        issue = conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id, title, raw, source_updated_at) "
            "VALUES ('issue', '1', %s, %s, %s, now()) RETURNING id",
            (repo, awkward, json.dumps({"nested": {"k": [1, None, awkward]}})),
        ).fetchone()["id"]
        conn.execute("INSERT INTO issue (node_id, number, state, body) VALUES (%s, 1, 'closed', %s)",
                     (issue, awkward))
        pr = conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id, title) "
            "VALUES ('pull_request', '2', %s, NULL) RETURNING id", (repo,)).fetchone()["id"]
        conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) "
                     "VALUES (%s, 2, 'closed', now())", (pr,))
        conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, "
            "source_ref) VALUES (%s, %s, 'closes', 'explicit', 'explicit', 'pr_body_closing_keyword', %s)",
            (pr, issue, awkward))
        conn.commit()

    def test_export_then_import_is_byte_identical(self) -> None:
        src = self.db.connect(_dsn_for(self.SRC))
        migrate.apply_all(src, MIGRATIONS)
        self._seed(src)
        manifest = snapshot.export(src, self.file, project_dir=ROOT)
        self.assertEqual(manifest["node_counts"], {"issue": 1, "pull_request": 1, "repository": 1})
        self.assertEqual(manifest["repositories"], ["o/r"])
        self.assertEqual(set(manifest["migrations"]), {p.name for p in migrate.discover(MIGRATIONS)})

        dst = self.db.connect(_dsn_for(self.DST))
        snapshot.import_(dst, self.file, directory=MIGRATIONS)

        for table, meta in manifest["tables"].items():
            with self.subTest(table=table):
                self.assertEqual(snapshot._copy_out(dst, table, meta["columns"]),
                                 snapshot._copy_out(src, table, meta["columns"]))

        # Sequences were reset, so the restored graph can keep growing.
        dst.execute("INSERT INTO node (node_type, external_id, title) VALUES ('person', 'x', 'x')")
        dst.rollback()

        with self.assertRaisesRegex(snapshot.SnapshotError, "empty one"):
            snapshot.import_(dst, self.file, directory=MIGRATIONS)
        src.close()
        dst.close()

    def test_the_archive_holds_a_manifest_and_one_file_per_table(self) -> None:
        src = self.db.connect(_dsn_for(self.SRC))
        migrate.apply_all(src, MIGRATIONS)
        manifest = snapshot.export(src, self.file, project_dir=ROOT)
        src.close()
        with zipfile.ZipFile(self.file) as z:
            names = set(z.namelist())
        self.assertIn("manifest.json", names)
        self.assertEqual(names - {"manifest.json"}, {f"data/{t}.copy" for t in manifest["tables"]})
        self.assertNotIn("data/schema_migration.copy", names, "the ledger is the manifest's job")

    def test_export_refuses_data_containing_the_token(self) -> None:
        src = self.db.connect(_dsn_for(self.SRC))
        migrate.apply_all(src, MIGRATIONS)
        src.execute("INSERT INTO node (node_type, external_id, title) "
                    "VALUES ('person', 'leak', 'ghp_FAKE_TOKEN_FOR_TEST')")
        src.commit()
        saved = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "ghp_FAKE_TOKEN_FOR_TEST"
        try:
            with self.assertRaisesRegex(snapshot.SnapshotError, "token"):
                snapshot.export(src, self.file, project_dir=ROOT)
            self.assertFalse(self.file.exists(), "a refused export left a file behind")
        finally:
            if saved is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = saved
            src.execute("DELETE FROM node WHERE external_id = 'leak'")
            src.commit()
            src.close()


if __name__ == "__main__":
    unittest.main()
