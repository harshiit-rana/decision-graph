"""Unit tests for the `dg` CLI and the migration ledger.

These run without Docker and without a database — they cover the parts where a mistake is
silent rather than loud: an .env parser that drops a value, a migration ledger that adopts
something it shouldn't, an error translator that stops translating.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from decision_graph import cli, migrate


class EnvParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._saved = cli.ENV_FILE
        cli.ENV_FILE = self.dir / ".env"

    def tearDown(self) -> None:
        cli.ENV_FILE = self._saved
        self._tmp.cleanup()

    def test_missing_file_is_empty_not_an_error(self) -> None:
        self.assertEqual(cli.read_env(), {})

    def test_parses_comments_blanks_and_quotes(self) -> None:
        cli.ENV_FILE.write_text(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "GITHUB_TOKEN=ghp_abc123",
                    'TARGET_REPO="pallets/flask"',
                    "BACKFILL_MONTHS = 6 ",
                    "   # indented comment",
                ]
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            cli.read_env(),
            {
                "GITHUB_TOKEN": "ghp_abc123",
                "TARGET_REPO": "pallets/flask",
                "BACKFILL_MONTHS": "6",
            },
        )

    def test_value_containing_equals_is_preserved(self) -> None:
        # Tokens and DSNs contain '='. Splitting on every '=' would silently truncate them.
        cli.ENV_FILE.write_text("GITHUB_TOKEN=abc=def==\n", encoding="utf-8")
        self.assertEqual(cli.read_env()["GITHUB_TOKEN"], "abc=def==")

    def test_write_then_read_round_trips(self) -> None:
        cli.write_env({"GITHUB_TOKEN": "t", "TARGET_REPO": "o/n", "BACKFILL_MONTHS": "12"})
        self.assertEqual(cli.read_env()["TARGET_REPO"], "o/n")

    def test_written_file_documents_why_database_url_is_absent(self) -> None:
        # The single most likely support question is "where do I put DATABASE_URL".
        cli.write_env({"GITHUB_TOKEN": "t"})
        self.assertIn("DATABASE_URL", cli.ENV_FILE.read_text(encoding="utf-8"))


class DbErrorTranslationTest(unittest.TestCase):
    def test_unresolvable_host_is_reported_as_the_container_being_down(self) -> None:
        why, fix = cli.explain_db_error(
            Exception("failed to resolve host 'db': [Errno -3] Temporary failure in name resolution")
        )
        self.assertIn("not running", why)
        self.assertIn("dg init", fix)
        self.assertNotIn("Errno", why)

    def test_connection_refused_is_distinguished_from_absent(self) -> None:
        why, _ = cli.explain_db_error(Exception("connection refused"))
        self.assertIn("starting", why)

    def test_unknown_errors_fall_through_with_a_next_step(self) -> None:
        why, fix = cli.explain_db_error(Exception("something entirely new\nsecond line"))
        self.assertEqual(why, "something entirely new")
        self.assertIn("doctor", fix)


class MigrationLedgerTest(unittest.TestCase):
    def test_every_migration_declares_a_sentinel(self) -> None:
        # A migration with no sentinel can never be adopted, so an already-migrated
        # database would try to re-apply it and fail. Adding a migration without adding
        # its probe should break here, not in someone's terminal.
        repo_root = Path(__file__).resolve().parents[1]
        files = migrate.discover(repo_root / "db" / "migrations")
        missing = [p.name for p in files if p.name[:4] not in migrate.SENTINELS]
        self.assertEqual(missing, [], f"migrations without a sentinel probe: {missing}")

    def test_sentinels_do_not_outnumber_migrations(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        prefixes = {p.name[:4] for p in migrate.discover(repo_root / "db" / "migrations")}
        stale = sorted(set(migrate.SENTINELS) - prefixes)
        self.assertEqual(stale, [], f"sentinels for migrations that no longer exist: {stale}")

    def test_checksum_is_stable_and_sensitive(self) -> None:
        self.assertEqual(migrate._checksum("abc"), migrate._checksum("abc"))
        self.assertNotEqual(migrate._checksum("abc"), migrate._checksum("abd"))

    def test_discover_orders_by_filename(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        names = [p.name for p in migrate.discover(repo_root / "db" / "migrations")]
        self.assertEqual(names, sorted(names))
        self.assertTrue(names[0].startswith("0001"))

    def test_discover_rejects_a_missing_directory(self) -> None:
        with self.assertRaises(FileNotFoundError):
            migrate.discover(Path("/nonexistent/migrations"))


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = cli.build_parser()

    def test_all_five_commands_are_registered(self) -> None:
        for command in ("init", "doctor", "status", "ingest", "query"):
            with self.subTest(command=command):
                args, _ = self.parser.parse_known_args([command] if command != "query" else [command, "x"])
                self.assertEqual(args.command, command)

    def test_ingest_and_query_forward_unknown_flags(self) -> None:
        # These deliberately pass their own flags through rather than re-declaring every
        # option of dg-ingest and dg-query in two places.
        args, extra = self.parser.parse_known_args(["ingest", "--months", "6"])
        self.assertTrue(args.passthrough)
        self.assertEqual(extra, ["--months", "6"])

        args, extra = self.parser.parse_known_args(["query", "text", "--mode", "impact"])
        self.assertTrue(args.passthrough)
        self.assertEqual(extra, ["--mode", "impact"])

    def test_our_own_commands_do_not_forward(self) -> None:
        args, extra = self.parser.parse_known_args(["status", "--bogus"])
        self.assertFalse(getattr(args, "passthrough", False))
        self.assertEqual(extra, ["--bogus"])


def _row(repo, resource, phase="backfill", watermark=None):
    return {"repo": repo, "resource": resource, "phase": phase, "steady_watermark": watermark}


class CursorLinesTest(unittest.TestCase):
    """`dg status` cursor rendering (issue #47).

    Both defects were invisible to a reader of the output: a second repository appeared as
    a duplicate row with no label, and `issues` reported a phase that is structurally
    incapable of ever changing. The audit was misled by the second one, so it is worth a
    test rather than a careful eye.
    """

    def test_single_repo_prints_no_repo_header(self) -> None:
        lines = cli._cursor_lines([_row("pallets/flask", "issues"), _row("pallets/flask", "commits")])
        self.assertFalse(any(line.strip() == "pallets/flask" for line in lines))
        self.assertTrue(all(not line.startswith("    ") for line in lines if "up to" in line))

    def test_two_repos_are_labelled_and_grouped(self) -> None:
        rows = [
            _row("a/one", "commits", phase="steady"),
            _row("a/one", "issues"),
            _row("b/two", "commits", phase="steady"),
            _row("b/two", "issues"),
        ]
        lines = cli._cursor_lines(rows)
        headers = [line.strip() for line in lines if line.strip() in {"a/one", "b/two"}]
        self.assertEqual(headers, ["a/one", "b/two"])
        # Every resource row sits under a header, indented, so no two rows read alike.
        self.assertEqual(len([line for line in lines if "up to" in line]), 4)
        self.assertTrue(all(line.startswith("    ") for line in lines if "up to" in line))

    def test_repo_header_is_not_repeated_per_row(self) -> None:
        rows = [_row("a/one", "commits"), _row("a/one", "issues"), _row("b/two", "issues")]
        lines = cli._cursor_lines(rows)
        self.assertEqual(len([line for line in lines if line.strip() == "a/one"]), 1)

    def test_phase_is_shown_for_commits_only(self) -> None:
        """COMMITTED_DESC owns `phase`; the forward-walking strategies never set it, so the
        column would read 'backfill' forever for issues no matter what the truth was."""
        lines = cli._cursor_lines([_row("a/one", "commits", phase="steady")])
        self.assertIn("steady", lines[0])

        lines = cli._cursor_lines([_row("a/one", "issues", phase="backfill")])
        self.assertNotIn("backfill", lines[0])

    def test_a_stuck_backfill_phase_on_issues_is_never_displayed(self) -> None:
        """The exact state that misled the recall audit: phase says backfill, the watermark
        says today. Only the watermark should reach the screen."""
        rows = [_row("a/one", "issues", phase="backfill", watermark=datetime(2026, 8, 22))]
        line = cli._cursor_lines(rows)[0]
        self.assertIn("2026-08-22", line)
        self.assertNotIn("backfill", line)

    def test_missing_watermark_renders_as_a_dash_not_none(self) -> None:
        line = cli._cursor_lines([_row("a/one", "releases", watermark=None)])[0]
        self.assertIn("—", line)
        self.assertNotIn("None", line)

    def test_the_single_repo_hint_does_not_mention_target_repo_scoping(self) -> None:
        """That caveat only makes sense when a repo the user can see will not advance."""
        one = "\n".join(cli._cursor_lines([_row("a/one", "issues")]))
        two = "\n".join(cli._cursor_lines([_row("a/one", "issues"), _row("b/two", "issues")]))
        self.assertNotIn("TARGET_REPO", one)
        self.assertIn("TARGET_REPO", two)

    def test_the_phase_footnote_appears_only_when_a_phase_was_printed(self) -> None:
        without = "\n".join(cli._cursor_lines([_row("a/one", "issues")]))
        with_commits = "\n".join(cli._cursor_lines([_row("a/one", "commits", phase="steady")]))
        self.assertNotIn("backfill/steady applies", without)
        self.assertIn("backfill/steady applies", with_commits)

    def test_a_null_phase_prints_nothing_and_raises_no_footnote(self) -> None:
        """What migration 0012 actually stores for a forward walker. The row must still
        render its watermark, and `None` must never reach the screen."""
        rows = [_row("a/one", "issues", phase=None, watermark=datetime(2026, 8, 22))]
        lines = cli._cursor_lines(rows)
        self.assertIn("2026-08-22", lines[0])
        self.assertNotIn("None", lines[0])
        self.assertNotIn("backfill/steady applies", "\n".join(lines))

    def test_a_null_phase_on_commits_is_not_printed_as_none(self) -> None:
        """Belt and braces: a commits row whose phase is somehow NULL — a database short of
        0012's repair, or a hand-edited row — must degrade to blank, not to the string
        'None' padded into the column."""
        line = cli._cursor_lines([_row("a/one", "commits", phase=None)])[0]
        self.assertNotIn("None", line)


class ChooseReportRepoTest(unittest.TestCase):
    def test_empty_ingested_returns_empty(self) -> None:
        outcome, repo = cli.choose_report_repo("pallets/flask", [])
        self.assertEqual(outcome, "empty")
        self.assertIsNone(repo)

    def test_requested_repo_matching_ingested_returns_use(self) -> None:
        outcome, repo = cli.choose_report_repo("pallets/flask", ["pallets/flask"])
        self.assertEqual(outcome, "use")
        self.assertEqual(repo, "pallets/flask")

    def test_requested_repo_not_in_ingested_returns_unknown(self) -> None:
        outcome, repo = cli.choose_report_repo("foo/bar", ["pallets/flask"])
        self.assertEqual(outcome, "unknown")
        self.assertIsNone(repo)

    def test_unspecified_repo_with_single_ingested_returns_use(self) -> None:
        outcome, repo = cli.choose_report_repo("", ["pallets/flask"])
        self.assertEqual(outcome, "use")
        self.assertEqual(repo, "pallets/flask")

    def test_unspecified_repo_with_multiple_ingested_returns_ambiguous(self) -> None:
        outcome, repo = cli.choose_report_repo("", ["a/one", "b/two"])
        self.assertEqual(outcome, "ambiguous")
        self.assertIsNone(repo)


if __name__ == "__main__":
    unittest.main()

