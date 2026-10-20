"""The database is reachable only from this machine, and not on a published password (#118).

The compose file is parsed as text rather than through `docker compose config`, so these
checks run in CI and on a machine without Docker. A regression here is silent otherwise.
Nothing fails when a port is published on every interface; it is just open.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from decision_graph import cli

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
from tests.superuser import requires_superuser  # noqa: E402

DSN = os.environ.get("DATABASE_URL")


class ComposeExposureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = COMPOSE.read_text(encoding="utf-8")

    def _port_lines(self) -> list[str]:
        return re.findall(r'^\s*-\s*"([^"]*:5432)"\s*$', self.text, flags=re.M)

    def test_every_published_port_names_a_host_ip(self) -> None:
        # "55434:5432" means 0.0.0.0. A mapping needs three parts to be bound anywhere.
        ports = self._port_lines()
        self.assertTrue(ports, "no published database port found; update this test")
        for mapping in ports:
            with self.subTest(mapping=mapping):
                parts = re.split(r":(?![^{]*\})", mapping)
                self.assertEqual(len(parts), 3, f"{mapping!r} publishes on every interface")

    def test_default_binding_is_loopback(self) -> None:
        for mapping in self._port_lines():
            with self.subTest(mapping=mapping):
                self.assertTrue(
                    mapping.startswith("${DG_DB_BIND:-127.0.0.1}:"),
                    f"{mapping!r} does not default to 127.0.0.1",
                )

    def test_no_literal_password_remains(self) -> None:
        self.assertNotRegex(self.text, r"POSTGRES_PASSWORD:\s*[\"']?dg[\"']?\s*$")
        self.assertNotIn("postgres:dg@", self.text)

    def test_server_and_client_read_the_same_variable_and_fallback(self) -> None:
        # If these drifted apart, a fresh volume would be created with one password and the
        # app would connect with another.
        server = re.search(r"POSTGRES_PASSWORD:\s*(\$\{[^}]*\})", self.text)
        client = re.search(r"postgresql://postgres:(\$\{[^}]*\})@db:", self.text)
        self.assertIsNotNone(server)
        self.assertIsNotNone(client)
        self.assertEqual(server.group(1), client.group(1))
        self.assertEqual(server.group(1), "${DG_DB_PASSWORD:-%s}" % cli.DEFAULT_DB_PASSWORD)


class DefaultPasswordDetectionTest(unittest.TestCase):
    def test_compose_database_on_the_default_is_flagged(self) -> None:
        self.assertTrue(cli.uses_default_db_password("postgresql://postgres:dg@db:5432/dg"))

    def test_compose_database_on_a_random_password_is_not(self) -> None:
        self.assertFalse(
            cli.uses_default_db_password("postgresql://postgres:Zq3_x-9@db:5432/dg")
        )

    def test_a_database_we_do_not_manage_is_never_ours_to_rotate(self) -> None:
        # CI and host-side runs point at their own Postgres. Same password, not our server.
        self.assertFalse(
            cli.uses_default_db_password("postgresql://postgres:dg@localhost:5432/dg")
        )


class _FakeConn:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.statements: list[object] = []
        self.committed = False
        self.rolled_back = 0
        self.fail_commit = fail_commit

    def rollback(self) -> None:
        self.rolled_back += 1

    def execute(self, statement: object) -> None:
        self.statements.append(statement)

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("server closed the connection")
        self.committed = True


class RotationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._saved = cli.ENV_FILE
        cli.ENV_FILE = Path(self._tmp.name) / ".env"
        cli.ENV_FILE.write_text(
            "GITHUB_TOKEN=t\nNVIDIA_API_KEY=keep-me\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        cli.ENV_FILE = self._saved
        self._tmp.cleanup()

    def test_new_password_is_written_and_committed(self) -> None:
        conn = _FakeConn()
        new = cli.rotate_db_password(conn)
        env = cli.read_env()
        self.assertEqual(env["DG_DB_PASSWORD"], new)
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.statements), 1)

    def test_password_is_long_and_url_safe(self) -> None:
        new = cli.rotate_db_password(_FakeConn())
        self.assertGreaterEqual(len(new), 32)
        self.assertRegex(new, r"^[A-Za-z0-9_-]+$")
        self.assertNotEqual(new, cli.DEFAULT_DB_PASSWORD)

    def test_rotation_keeps_unrelated_keys(self) -> None:
        cli.rotate_db_password(_FakeConn())
        self.assertEqual(cli.read_env()["NVIDIA_API_KEY"], "keep-me")

    def test_failed_commit_restores_env(self) -> None:
        before = cli.ENV_FILE.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            cli.rotate_db_password(_FakeConn(fail_commit=True))
        self.assertEqual(cli.ENV_FILE.read_text(encoding="utf-8"), before)

    def test_failed_commit_with_no_prior_env_leaves_none(self) -> None:
        cli.ENV_FILE.unlink()
        with self.assertRaises(RuntimeError):
            cli.rotate_db_password(_FakeConn(fail_commit=True))
        self.assertFalse(cli.ENV_FILE.exists())


class WriteEnvPreservesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._saved = cli.ENV_FILE
        cli.ENV_FILE = Path(self._tmp.name) / ".env"

    def tearDown(self) -> None:
        cli.ENV_FILE = self._saved
        self._tmp.cleanup()

    def test_keys_not_passed_survive(self) -> None:
        # `dg init --reconfigure` writes token, repo and months. It used to delete the rest.
        cli.ENV_FILE.write_text(
            "NVIDIA_API_KEY=nv\nDG_NLP_MODEL=m\nDG_DB_PASSWORD=p\n", encoding="utf-8"
        )
        cli.write_env({"GITHUB_TOKEN": "t", "TARGET_REPO": "o/n", "BACKFILL_MONTHS": "12"})
        env = cli.read_env()
        self.assertEqual(
            (env["NVIDIA_API_KEY"], env["DG_NLP_MODEL"], env["DG_DB_PASSWORD"]), ("nv", "m", "p")
        )
        self.assertEqual(env["TARGET_REPO"], "o/n")

    def test_passed_value_wins(self) -> None:
        cli.ENV_FILE.write_text("TARGET_REPO=old/repo\n", encoding="utf-8")
        cli.write_env({"TARGET_REPO": "new/repo"})
        self.assertEqual(cli.read_env()["TARGET_REPO"], "new/repo")


class AuthFailureAdviceTest(unittest.TestCase):
    def test_never_advises_deleting_the_volume(self) -> None:
        why, fix = cli.explain_db_error(
            Exception('password authentication failed for user "postgres"')
        )
        self.assertNotIn("down -v", fix)
        self.assertIn("ALTER ROLE", fix)
        self.assertIn("no data is lost", why)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class RotationAgainstPostgresTest(unittest.TestCase):
    """The SQL really changes a password. Run on a throwaway role, never the one in DSN."""

    ROLE = "dg_rotation_probe"

    def setUp(self) -> None:
        import psycopg
        from psycopg import sql

        self.psycopg, self.sql = psycopg, sql
        self.admin = psycopg.connect(DSN, autocommit=True)
        self.admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(self.ROLE)))
        self.admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'dg'").format(sql.Identifier(self.ROLE))
        )
        self._tmp = TemporaryDirectory()
        self._saved = cli.ENV_FILE
        cli.ENV_FILE = Path(self._tmp.name) / ".env"

    def tearDown(self) -> None:
        cli.ENV_FILE = self._saved
        self._tmp.cleanup()
        self.admin.execute(
            self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(self.ROLE))
        )
        self.admin.close()

    def _dsn_as(self, password: str) -> str:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(DSN)
        host = parts.hostname or "localhost"
        netloc = f"{self.ROLE}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def test_new_password_logs_in_and_old_one_does_not(self) -> None:
        with self.psycopg.connect(self._dsn_as("dg")) as conn:
            new = cli.rotate_db_password(conn)
        with self.psycopg.connect(self._dsn_as(new)) as conn:
            self.assertEqual(conn.execute("SELECT current_user").fetchone()[0], self.ROLE)
        with self.assertRaises(self.psycopg.OperationalError):
            self.psycopg.connect(self._dsn_as("dg")).close()


if __name__ == "__main__":
    unittest.main()
