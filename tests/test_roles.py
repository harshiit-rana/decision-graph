"""Least-privilege database roles (issue #172).

What's checked, in the order the issue's acceptance criteria give it:

- DSN selection: each command asks for one role, a URL stays a URL, and with no role passwords
  nothing changes, which is how an existing install stays as it was;
- every migration applies from empty as `dg_owner`, a role that is not a superuser, and
  everything it creates is owned by it;
- `dg_reader` is refused INSERT, UPDATE, DELETE and TRUNCATE on every table, and can't create
  anything;
- `dg_app` can do DML but can't ALTER a table, DROP or DISABLE a trigger or constraint, DROP the
  rubric, TRUNCATE, or run a program through COPY;
- role setup is idempotent, and keeps the passwords it was not given;
- an install that predates the roles is moved under `dg_owner` without a row changing, and is
  never moved by `dg init` on its own;
- `dg doctor`'s surface report says who each surface connects as, superuser or not.

The DB-gated tests each build a throwaway database next to `DATABASE_URL` (which must be a
superuser's, as CI's and the scratch server's are) and drop it afterwards. Roles are cluster-wide,
so they persist between tests; setup is idempotent for exactly that reason.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from decision_graph import cli, migrate, roles
from tests.superuser import requires_superuser

DSN = os.environ.get("DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
ENV = {"DG_APP_PASSWORD": "app-pw", "DG_READER_PASSWORD": "reader-pw"}


class DsnSelectionTest(unittest.TestCase):
    ADMIN = "postgresql://postgres:supersecret@db:5432/dg"

    def test_each_command_asks_for_one_role(self) -> None:
        wanted = {"ingest": roles.APP, "query": roles.READER, "context": roles.READER,
                  "report": roles.READER, "status": roles.READER, "ask": roles.READER,
                  "init": None, "doctor": None, "roles": None}
        for command, role in wanted.items():
            with self.subTest(command=command):
                self.assertEqual(roles.role_for(command), role)

    def test_snapshot_export_reads_and_import_needs_the_superuser(self) -> None:
        self.assertEqual(roles.role_for("snapshot", "export"), roles.READER)
        self.assertEqual(roles.role_for("snapshot", "inspect"), roles.READER)
        self.assertIsNone(roles.role_for("snapshot", "import"))

    def test_an_unlisted_command_stays_on_the_admin_dsn(self) -> None:
        self.assertIsNone(roles.role_for("something-new"))
        self.assertIsNone(roles.role_for(None))

    def test_a_url_stays_a_url_with_the_role_and_its_password(self) -> None:
        out = roles.dsn_for(self.ADMIN, roles.READER, ENV)
        parts = urlsplit(out)
        self.assertEqual((parts.scheme, parts.username, parts.password, parts.hostname, parts.port, parts.path),
                         ("postgresql", "dg_reader", "reader-pw", "db", 5432, "/dg"))

    def test_without_passwords_nothing_changes(self) -> None:
        for role in (roles.APP, roles.READER, None):
            with self.subTest(role=role):
                self.assertEqual(roles.dsn_for(self.ADMIN, role, {}), self.ADMIN)

    def test_the_admin_role_never_gets_rewritten(self) -> None:
        self.assertEqual(roles.dsn_for(self.ADMIN, None, ENV), self.ADMIN)

    def test_a_password_with_reserved_characters_is_quoted(self) -> None:
        out = roles.dsn_for(self.ADMIN, roles.APP, {"DG_APP_PASSWORD": "a/b@c:d"})
        self.assertEqual(urlsplit(out).password, "a%2Fb%40c%3Ad")
        self.assertEqual(urlsplit(out).hostname, "db")

    def test_an_ipv6_host_keeps_its_brackets_and_query(self) -> None:
        out = roles.dsn_for("postgresql://postgres:x@[::1]:5432/dg?sslmode=disable", roles.APP, ENV)
        self.assertIn("@[::1]:5432/dg?sslmode=disable", out)

    def test_a_keyword_dsn_is_handled_too(self) -> None:
        out = roles.dsn_for("host=db dbname=dg user=postgres password=x", roles.READER, ENV)
        self.assertEqual(psycopg.conninfo.conninfo_to_dict(out)["user"], "dg_reader")

    def test_configured_needs_both_passwords(self) -> None:
        self.assertTrue(roles.configured(ENV))
        self.assertFalse(roles.configured({"DG_APP_PASSWORD": "x"}))
        self.assertFalse(roles.configured({}))


class UseRoleDsnTest(unittest.TestCase):
    def run_as(self, command, env, action=None):
        with mock.patch.dict(os.environ, env, clear=True):
            cli.use_role_dsn(command, action)
            return dict(os.environ)

    def test_it_keeps_the_original_as_the_admin_dsn_and_rewrites_the_working_one(self) -> None:
        out = self.run_as("query", {"DATABASE_URL": DsnSelectionTest.ADMIN, **ENV})
        self.assertEqual(out[roles.ADMIN_ENV], DsnSelectionTest.ADMIN)
        self.assertEqual(urlsplit(out["DATABASE_URL"]).username, "dg_reader")

    def test_a_second_call_derives_from_the_admin_dsn_and_does_not_compound(self) -> None:
        first = self.run_as("ingest", {"DATABASE_URL": DsnSelectionTest.ADMIN, **ENV})
        second = self.run_as("query", dict(first))
        self.assertEqual(urlsplit(second["DATABASE_URL"]).username, "dg_reader")
        self.assertEqual(second[roles.ADMIN_ENV], DsnSelectionTest.ADMIN)

    def test_an_install_without_role_passwords_is_unchanged(self) -> None:
        out = self.run_as("ingest", {"DATABASE_URL": DsnSelectionTest.ADMIN})
        self.assertEqual(out["DATABASE_URL"], DsnSelectionTest.ADMIN)

    def test_admin_commands_keep_the_superuser(self) -> None:
        for command in ("init", "doctor", "roles"):
            with self.subTest(command=command):
                out = self.run_as(command, {"DATABASE_URL": DsnSelectionTest.ADMIN, **ENV})
                self.assertEqual(out["DATABASE_URL"], DsnSelectionTest.ADMIN)

    def test_no_database_url_is_left_alone(self) -> None:
        self.assertNotIn("DATABASE_URL", self.run_as("query", dict(ENV)))


class SurfaceReportTest(unittest.TestCase):
    class Conn:
        def __init__(self, user, superuser):
            self.user, self.superuser = user, superuser

        def execute(self, q, *a):
            outer = self

            class R:
                def fetchone(self_inner):
                    return {"rolsuper": outer.superuser} if "rolsuper" in q else {"u": outer.user}

            return R()

        def close(self):
            pass

    def test_it_says_who_each_surface_connects_as(self) -> None:
        def connect(dsn):
            user = urlsplit(dsn).username
            return self.Conn(user, user == "postgres")

        rows = roles.surface_report(DsnSelectionTest.ADMIN, ENV, connect)
        self.assertEqual([(r["user"], r["superuser"]) for r in rows], [("dg_app", False), ("dg_reader", False)])

    def test_an_install_without_roles_reports_the_superuser(self) -> None:
        rows = roles.surface_report(DsnSelectionTest.ADMIN, {}, lambda dsn: self.Conn("postgres", True))
        self.assertTrue(all(r["superuser"] for r in rows))

    def test_a_connection_failure_is_reported_not_raised(self) -> None:
        def boom(dsn):
            raise psycopg.OperationalError("password authentication failed for user")

        rows = roles.surface_report(DsnSelectionTest.ADMIN, ENV, boom)
        self.assertTrue(all("password authentication failed" in r["error"] for r in rows))


# ---------------------------------------------------------------------------
# Against Postgres
# ---------------------------------------------------------------------------


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


class ScratchDatabase:
    """A database created next to DATABASE_URL's, dropped on exit."""

    def __init__(self) -> None:
        self.name = "dg_roles_" + uuid.uuid4().hex[:10]
        self.admin = psycopg.connect(_with_database(DSN, "postgres"), autocommit=True)
        self.admin.execute(f'CREATE DATABASE "{self.name}"')
        self.dsn = _with_database(DSN, self.name)

    def connect(self, dsn: str | None = None):
        return psycopg.connect(dsn or self.dsn, row_factory=dict_row)

    def close(self) -> None:
        self.admin.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        self.admin.close()


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class LeastPrivilegeTest(unittest.TestCase):
    """One database, built as the issue describes: roles first, then migrations as dg_owner."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.db = ScratchDatabase()
        cls.root = cls.db.connect()
        if not roles.is_superuser(cls.root):
            cls.db.close()
            raise unittest.SkipTest("DATABASE_URL must be a superuser's to create roles")
        roles.setup(cls.root, app_password=ENV["DG_APP_PASSWORD"], reader_password=ENV["DG_READER_PASSWORD"])
        cls.root.commit()
        cls.result = migrate.apply_all(cls.root, MIGRATIONS, role=roles.OWNER)
        roles.grant_existing(cls.root)
        cls.root.commit()
        cls.app_dsn = roles.dsn_for(cls.db.dsn, roles.APP, ENV)
        cls.reader_dsn = roles.dsn_for(cls.db.dsn, roles.READER, ENV)
        cls.tables = [r["tablename"] for r in cls.root.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1").fetchall()]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.close()
        cls.db.close()

    def setUp(self) -> None:
        self.root.rollback()  # the shared connection must not inherit another test's failure

    # -- migrations ------------------------------------------------------------------------

    def test_every_migration_applied_from_empty_as_the_owner(self) -> None:
        self.assertEqual(len(self.result.applied), len(list(MIGRATIONS.glob("*.sql"))))
        self.assertEqual(self.result.adopted, [])

    def test_the_connection_is_the_superuser_again_afterwards(self) -> None:
        self.assertEqual(self.root.execute("SELECT current_user AS u").fetchone()["u"], "postgres")

    def test_everything_in_public_is_owned_by_dg_owner_except_extension_objects(self) -> None:
        rows = self.root.execute(
            "SELECT 'relation' AS kind, c.relname AS name, pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
            "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r','p','v','m','S') "
            "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('e','a','i')) "
            "UNION ALL SELECT 'function', p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p "
            "WHERE p.pronamespace = 'public'::regnamespace "
            "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') "
            "UNION ALL SELECT 'type', t.typname, pg_get_userbyid(t.typowner) FROM pg_type t "
            "WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('e','d')").fetchall()
        self.assertGreater(len(rows), 20)
        self.assertEqual([r for r in rows if r["owner"] != roles.OWNER], [])

    def test_the_ledger_is_readable_by_the_reader(self) -> None:
        with self.db.connect(self.reader_dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM schema_migration").fetchone()["n"],
                             len(self.result.applied))

    def test_a_failing_migration_still_gives_the_connection_its_rights_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0099_bad.sql").write_text("BEGIN;\nSELECT 1/0;\nCOMMIT;\n", encoding="utf-8")
            with self.assertRaises(psycopg.errors.DivisionByZero):
                migrate.apply_all(self.root, Path(tmp), role=roles.OWNER)
        self.assertEqual(self.root.execute("SELECT current_user AS u").fetchone()["u"], "postgres")

    # -- the roles themselves ----------------------------------------------------------------

    def test_none_of_the_roles_has_a_dangerous_attribute(self) -> None:
        for r in self.root.execute(
                "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = ANY(%s)", (list(roles.ROLES),)).fetchall():
            with self.subTest(role=r["rolname"]):
                self.assertFalse(any(v for k, v in r.items() if k != "rolname"), r)

    def test_dg_owner_cannot_log_in(self) -> None:
        with self.assertRaises(psycopg.OperationalError):
            psycopg.connect(roles.dsn_for(self.db.dsn, roles.APP, {"DG_APP_PASSWORD": "x"}).replace("dg_app", "dg_owner"))

    def test_the_app_and_reader_log_in_with_their_passwords(self) -> None:
        for dsn, user in ((self.app_dsn, roles.APP), (self.reader_dsn, roles.READER)):
            with self.subTest(user=user), self.db.connect(dsn) as conn:
                self.assertEqual(conn.execute("SELECT current_user AS u").fetchone()["u"], user)

    # -- the reader -------------------------------------------------------------------------

    def test_the_reader_can_select_from_every_table(self) -> None:
        with self.db.connect(self.reader_dsn) as conn:
            for t in self.tables:
                with self.subTest(table=t):
                    conn.execute(psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(t)))

    def test_the_reader_holds_no_write_privilege_on_any_table(self) -> None:
        with self.db.connect(self.reader_dsn) as conn:
            for t in self.tables:
                for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    with self.subTest(table=t, privilege=priv):
                        self.assertFalse(conn.execute(
                            "SELECT has_table_privilege(current_user, %s, %s) AS p", (t, priv)).fetchone()["p"])

    def test_a_reader_write_is_refused_by_the_database(self) -> None:
        with self.db.connect(self.reader_dsn) as conn:
            for stmt in ("INSERT INTO node (node_type, external_id, title) VALUES ('issue', 'zz', 't')",
                         "UPDATE node SET title = 'x'", "DELETE FROM node", "TRUNCATE node"):
                with self.subTest(stmt=stmt):
                    with self.assertRaises(errors.InsufficientPrivilege):
                        conn.execute(stmt)
                    conn.rollback()

    def test_the_reader_cannot_create_anything_or_set_a_sequence(self) -> None:
        with self.db.connect(self.reader_dsn) as conn:
            for stmt in ("CREATE TABLE zz (a int)", "SELECT nextval('node_id_seq')"):
                with self.subTest(stmt=stmt):
                    with self.assertRaises(errors.InsufficientPrivilege):
                        conn.execute(stmt)
                    conn.rollback()

    # -- the app ----------------------------------------------------------------------------

    def test_the_app_can_write_and_read(self) -> None:
        with self.db.connect(self.app_dsn) as conn:
            conn.execute("SET CONSTRAINTS ALL DEFERRED")
            nid = conn.execute("INSERT INTO node (node_type, external_id, title) VALUES ('issue', 'zz1', 't') "
                               "RETURNING id").fetchone()["id"]
            conn.execute("UPDATE node SET title = 'u' WHERE id = %s", (nid,))
            self.assertEqual(conn.execute("SELECT title FROM node WHERE id = %s", (nid,)).fetchone()["title"], "u")
            conn.execute("DELETE FROM node WHERE id = %s", (nid,))
            conn.rollback()

    def test_the_app_cannot_change_the_schema_rules(self) -> None:
        trigger = self.root.execute(
            "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace ORDER BY 1 LIMIT 1").fetchone()
        constraint = self.root.execute(
            "SELECT conname, conrelid::regclass::text AS rel FROM pg_constraint "
            "WHERE contype = 'c' AND connamespace = 'public'::regnamespace ORDER BY 1 LIMIT 1").fetchone()
        self.assertIsNotNone(trigger)
        self.assertIsNotNone(constraint)
        statements = [
            "ALTER TABLE node ADD COLUMN zz int",
            "ALTER TABLE node DROP COLUMN title",
            "DROP TABLE node CASCADE",
            f'DROP TRIGGER "{trigger["tgname"]}" ON "{trigger["relname"]}"',
            f'ALTER TABLE "{trigger["relname"]}" DISABLE TRIGGER USER',
            f'ALTER TABLE {constraint["rel"]} DROP CONSTRAINT "{constraint["conname"]}"',
            "DROP FUNCTION decision_rubric",
            "CREATE OR REPLACE FUNCTION decision_rubric() RETURNS int LANGUAGE sql AS 'select 1'",
            "TRUNCATE node CASCADE",
            "CREATE TABLE zz (a int)",
            "CREATE EXTENSION IF NOT EXISTS hstore",
        ]
        with self.db.connect(self.app_dsn) as conn:
            for stmt in statements:
                with self.subTest(stmt=stmt):
                    with self.assertRaises((errors.InsufficientPrivilege, errors.UndefinedFunction)):
                        conn.execute(stmt)
                    conn.rollback()

    def test_the_app_cannot_run_a_program_through_copy(self) -> None:
        with self.db.connect(self.app_dsn) as conn:
            with self.assertRaises(errors.InsufficientPrivilege):
                conn.execute("COPY (SELECT 1) TO PROGRAM 'true'")

    def test_the_app_cannot_become_the_owner_or_the_superuser(self) -> None:
        with self.db.connect(self.app_dsn) as conn:
            for role in (roles.OWNER, "postgres"):
                with self.subTest(role=role):
                    with self.assertRaises(errors.InsufficientPrivilege):
                        conn.execute(f'SET ROLE "{role}"')
                    conn.rollback()

    # -- idempotence --------------------------------------------------------------------------

    def test_setup_twice_changes_nothing_and_keeps_the_passwords(self) -> None:
        before = self.root.execute(
            "SELECT rolname, rolpassword, rolcanlogin FROM pg_authid "
            "WHERE rolname = ANY(%s) ORDER BY 1", (list(roles.ROLES),)).fetchall()
        roles.setup(self.root)
        roles.setup(self.root)
        self.root.commit()
        after = self.root.execute(
            "SELECT rolname, rolpassword, rolcanlogin FROM pg_authid "
            "WHERE rolname = ANY(%s) ORDER BY 1", (list(roles.ROLES),)).fetchall()
        self.assertEqual(before, after)
        with self.db.connect(self.app_dsn):
            pass

    def test_a_failed_env_write_rolls_the_new_passwords_back(self) -> None:
        def broken_writer(values):
            raise OSError("disk full")

        with self.assertRaises(OSError):
            roles.provision(self.root, broken_writer, existing_env={})
        with self.db.connect(self.app_dsn):  # the old password still works
            pass

    def test_a_failed_commit_puts_the_env_back(self) -> None:
        undone = []

        class FailingCommit:
            def __init__(self, inner):
                self.inner = inner

            def rollback(self):
                self.inner.rollback()

            def commit(self):
                self.inner.rollback()
                raise RuntimeError("commit failed")

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with self.assertRaises(RuntimeError):
            roles.provision(FailingCommit(self.root), lambda v: (lambda: undone.append(v)), existing_env={})
        self.assertEqual(len(undone), 1)
        self.assertEqual(set(undone[0]), set(roles.PASSWORD_KEY.values()))


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class ExistingInstallTest(unittest.TestCase):
    """An install that predates the roles: migrated and populated as the superuser."""

    def setUp(self) -> None:
        self.db = ScratchDatabase()
        self.root = self.db.connect()
        if not roles.is_superuser(self.root):
            self.db.close()
            self.skipTest("DATABASE_URL must be a superuser's to create roles")
        migrate.apply_all(self.root, MIGRATIONS)
        self.root.execute("SET CONSTRAINTS ALL DEFERRED")
        for i in range(5):
            self.root.execute("INSERT INTO node (node_type, external_id, title) VALUES ('issue', %s, 'legacy')", (f"leg{i}",))
        self.root.commit()
        # No open transaction may linger on this connection: the commands under test open their own
        # and ALTER the same tables, and a lingering read lock would make them wait forever.
        self.root.autocommit = True
        self.tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.tmp.name) / ".env"
        self.patches = [mock.patch.object(cli, "ENV_FILE", self.env_file),
                        mock.patch.object(cli, "PROJECT_DIR", Path(self.tmp.name)),
                        mock.patch.dict(os.environ, {"DATABASE_URL": self.db.dsn}, clear=False)]
        for p in self.patches:
            p.start()
        os.environ.pop(roles.ADMIN_ENV, None)
        os.environ.pop("DG_APP_PASSWORD", None)
        os.environ.pop("DG_READER_PASSWORD", None)

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
        self.root.close()
        self.db.close()

    def counts(self) -> dict:
        return {t: self.root.execute(psycopg.sql.SQL("SELECT count(*) AS n FROM {}").format(psycopg.sql.Identifier(t))).fetchone()["n"]
                for t in ("node", "edge", "schema_migration")}

    def owners(self) -> set:
        return {r["o"] for r in self.root.execute(
            "SELECT pg_get_userbyid(relowner) AS o FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind IN ('r','v','S') AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = pg_class.oid "
            "AND d.deptype IN ('e','a','i'))").fetchall()}

    def test_init_does_not_switch_a_populated_install_on_its_own(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            role = cli._prepare_roles(self.root, populated=True)
        self.assertIsNone(role)
        self.assertIn("dg roles setup", out.getvalue())
        self.assertEqual(self.owners(), {"postgres"})
        self.assertFalse(self.env_file.exists())

    def test_setup_moves_ownership_without_changing_a_row_and_snapshots_first(self) -> None:
        before = self.counts()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=False))
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self.owners(), {roles.OWNER})
        self.assertEqual(self.counts(), before)
        snaps = list(Path(self.tmp.name, "snapshots").glob("pre-roles-*.dgsnap"))
        self.assertEqual(len(snaps), 1)
        env = cli.read_env()
        self.assertTrue(roles.configured(env))

    def test_setup_does_not_touch_the_extensions_objects(self) -> None:
        extension_before = self.root.execute(
            "SELECT count(*) AS n FROM pg_proc p WHERE EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid "
            "AND d.deptype = 'e') AND pg_get_userbyid(p.proowner) = 'postgres'").fetchone()["n"]
        self.assertGreater(extension_before, 0)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=True))
        after = self.root.execute(
            "SELECT count(*) AS n FROM pg_proc p WHERE EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid "
            "AND d.deptype = 'e') AND pg_get_userbyid(p.proowner) = 'postgres'").fetchone()["n"]
        self.assertEqual(after, extension_before)

    def test_a_second_adopt_moves_nothing(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=True))
        self.assertEqual(roles.adopt(self.root), {"relations": 0, "functions": 0, "types": 0})

    def test_after_setup_each_command_connects_as_its_role_and_the_data_is_there(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=True))
        for k, v in cli.read_env().items():
            os.environ[k] = v
        seen = {}
        for command, role in (("query", roles.READER), ("ingest", roles.APP), ("init", "postgres")):
            os.environ["DATABASE_URL"] = self.db.dsn
            os.environ.pop(roles.ADMIN_ENV, None)
            cli.use_role_dsn(command)
            with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
                seen[command] = (conn.execute("SELECT current_user AS u").fetchone()["u"],
                                 conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"])
        self.assertEqual(seen, {"query": (roles.READER, 5), "ingest": (roles.APP, 5), "init": ("postgres", 5)})

    def test_the_read_surfaces_work_as_the_reader_on_a_real_graph(self) -> None:
        from decision_graph import context, query, snapshot

        self.root.execute("SET CONSTRAINTS ALL DEFERRED")
        issue = self.root.execute("INSERT INTO node (node_type, external_id, title) VALUES ('issue', 'zzhub', 'zz hub') "
                                  "RETURNING id").fetchone()["id"]
        for i in range(3):
            pr = self.root.execute("INSERT INTO node (node_type, external_id, title) VALUES ('pull_request', %s, 'zz hub') "
                                   "RETURNING id", (f"zzp{i}",)).fetchone()["id"]
            self.root.execute(
                "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from) "
                "VALUES (%s, %s, 'closes', 'explicit', 'explicit', 'x', now() - interval '1 day')", (pr, issue))
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=True))
        reader = roles.dsn_for(self.db.dsn, roles.READER, cli.read_env())
        with mock.patch.dict(os.environ, {"DATABASE_URL": reader}):
            for name, call in (
                ("query", lambda: query.main(["zzhub", "--mode", "impact", "--format", "json"])),
                ("context", lambda: context.main(["zzhub"])),
            ):
                out, err = io.StringIO(), io.StringIO()
                with self.subTest(surface=name), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = call()
                    self.assertEqual(code, 0, err.getvalue())
                    self.assertNotIn("permission denied", err.getvalue() + out.getvalue())
                    self.assertTrue(out.getvalue().strip(), "the surface printed nothing")
            export = Path(self.tmp.name) / "as-reader.dgsnap"
            with psycopg.connect(reader, row_factory=dict_row) as conn:
                snapshot.export(conn, export, project_dir=Path(self.tmp.name))
            self.assertTrue(export.exists() and export.stat().st_size > 0)

    def test_undoing_it_is_deleting_the_two_lines(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_roles(mock.Mock(action="setup", skip_snapshot=True))
        kept = {k: v for k, v in cli.read_env().items() if k not in roles.PASSWORD_KEY.values()}
        self.assertFalse(roles.configured(kept))
        with mock.patch.dict(os.environ, {"DATABASE_URL": self.db.dsn, **kept}, clear=True):
            cli.use_role_dsn("ingest")
            self.assertEqual(os.environ["DATABASE_URL"], self.db.dsn)

    def test_status_describes_the_state(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_roles(mock.Mock(action="status", skip_snapshot=False))
        self.assertIn("schema ownership      : legacy", out.getvalue())


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class FreshInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = ScratchDatabase()
        self.root = self.db.connect()
        if not roles.is_superuser(self.root):
            self.db.close()
            self.skipTest("DATABASE_URL must be a superuser's to create roles")
        self.tmp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.tmp.name) / ".env"
        self.patch = mock.patch.object(cli, "ENV_FILE", self.env_file)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()
        self.root.close()
        self.db.close()

    def test_a_fresh_database_gets_the_roles_and_migrates_as_the_owner(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            role = cli._prepare_roles(self.root, populated=False)
        self.assertEqual(role, roles.OWNER)
        self.assertTrue(roles.configured(cli.read_env()))
        result = migrate.apply_all(self.root, MIGRATIONS, role=role)
        self.assertEqual(len(result.applied), len(list(MIGRATIONS.glob("*.sql"))))
        self.assertEqual(roles.schema_owner_state(self.root), "owned")

    def test_running_it_again_keeps_the_passwords(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            cli._prepare_roles(self.root, populated=False)
            first = dict(cli.read_env())
            migrate.apply_all(self.root, MIGRATIONS, role=roles.OWNER)
            role = cli._prepare_roles(self.root, populated=False)
        self.assertEqual(role, roles.OWNER)
        self.assertEqual({k: cli.read_env()[k] for k in roles.PASSWORD_KEY.values()},
                         {k: first[k] for k in roles.PASSWORD_KEY.values()})

    def test_a_graph_with_no_data_is_adopted_without_a_prompt(self) -> None:
        migrate.apply_all(self.root, MIGRATIONS)
        self.assertEqual(roles.schema_owner_state(self.root), "legacy")
        with contextlib.redirect_stdout(io.StringIO()):
            role = cli._prepare_roles(self.root, populated=False)
        self.assertEqual(role, roles.OWNER)
        self.assertEqual(roles.schema_owner_state(self.root), "owned")

    def test_a_non_superuser_connection_is_left_alone(self) -> None:
        with mock.patch.object(roles, "is_superuser", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(cli._prepare_roles(self.root, populated=False))
        self.assertFalse(self.env_file.exists())


if __name__ == "__main__":
    unittest.main()
