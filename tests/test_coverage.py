"""Coverage: what the ingested window could and could not have contained (#174).

The fixture tests seed their own repository, cursors and runs, and roll back. CI's database
is empty, so nothing here depends on ingested data. They cover every status and reason, plus
the unit-level rule and schema shape without a database.

The point of the feature: "no record" must be distinguishable from "not looked at". So the
tests are mostly about the *refusing* cases, and about `unknown` never being upgraded to a
claim.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from decision_graph import context, contract, coverage, query, retrieval

from tests.test_contract import SCHEMA, roundtrip, validate

DSN = os.environ.get("DATABASE_URL")
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
FLOOR = NOW - timedelta(days=360)


class VocabularyTest(unittest.TestCase):
    def test_every_reason_maps_to_a_known_status_and_is_in_the_schema(self) -> None:
        schema_reasons = set(SCHEMA["$defs"]["coverage"]["properties"]["checks"]["items"]
                             ["properties"]["reason"]["enum"])
        self.assertEqual(set(coverage.REASONS), schema_reasons)
        for reason, (status, sentence) in coverage.REASONS.items():
            with self.subTest(reason=reason):
                self.assertIn(status, coverage.STATUSES)
                self.assertTrue(sentence.endswith("."))

    def test_there_is_no_numeric_completeness_score(self) -> None:
        # ADR 0002: nothing could calibrate one. A score would be a claim without a basis.
        names: list[str] = []

        def walk(node) -> None:
            # Property names and enum values only: the description may say "no score".
            if isinstance(node, dict):
                names.extend(node.get("properties", {}))
                names.extend(str(v) for v in node.get("enum", []))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(SCHEMA["$defs"]["coverage"])
        self.assertTrue(names)
        for name in names:
            for word in ("score", "percent", "ratio", "confidence"):
                self.assertNotIn(word, name.lower())
        for numeric in ("completeness", "coverage_pct"):
            self.assertNotIn(numeric, names)

    def test_the_window_is_three_hundred_sixty_days_not_twelve_calendar_months(self) -> None:
        # cursors.load pins now - 30 * months. Coverage reports the pinned floor, and the
        # urllib3 pre-registration was wrong about this once (annotation, 2026-10-17).
        import inspect

        from decision_graph import cursors

        self.assertIn("30 * backfill_months", inspect.getsource(cursors.load))

    def test_the_text_line_is_empty_without_a_window(self) -> None:
        self.assertIsNone(coverage.line({"repository": None, "checks": []}))

    def test_the_text_line_names_the_window_and_the_non_equivalence(self) -> None:
        said = coverage.line({
            "repository": {"window_floor": FLOOR, "ingested_through": NOW, "last_run_status": "succeeded"},
            "checks": [{"status": "partial", "reason": "last_run_stopped_early"}],
        })
        self.assertIn("window began 2025-10-22", said)
        self.assertIn("ingested through 2026-10-17", said)
        self.assertIn("partial: last run stopped early", said)
        self.assertIn("not the same as nothing happened", said)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class AgainstTheDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        # Other rows may exist (an ingested graph), so every test uses its own repository and
        # names it, never relying on `single_repository`.
        self.repo = self._node("repository", f"zz-cov-{id(self)}/r")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _node(self, node_type, ext, *, repo=None, created=None):
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title, repo_node_id, source_created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (node_type, ext, f"zz coverage {ext}", repo, created),
        ).fetchone()["id"]

    def _cursor(self, resource, *, phase=None, through=NOW - timedelta(hours=1)):
        self.conn.execute(
            "INSERT INTO ingestion_cursor (repo_node_id, resource, phase, window_floor, steady_watermark) "
            "VALUES (%s, %s, %s, %s, %s)", (self.repo, resource, phase, FLOOR, through))

    def _run(self, status="succeeded", error=None):
        self.conn.execute(
            "INSERT INTO ingestion_run (repo_node_id, status, finished_at, error) "
            "VALUES (%s, %s, now(), %s)", (self.repo, status, error))

    def _issue(self, number, *, created=None):
        nid = self._node("issue", str(number), repo=self.repo, created=created or NOW)
        self.conn.execute("INSERT INTO issue (node_id, number, state) VALUES (%s, %s, 'closed')", (nid, number))
        return nid

    def _status(self, node_id):
        facts = coverage.repository_facts(self.conn, self.repo)
        return coverage.node_check(self.conn, node_id, facts)

    # -- the statuses ---------------------------------------------------------------------

    def test_complete_when_cursors_are_steady_and_the_last_run_finished(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._run()
        check = self._status(self._issue(1))
        self.assertEqual((check["status"], check["reason"]), ("complete", "ingestion_complete"))

    def test_partial_when_the_backfill_never_produced_a_watermark(self) -> None:
        self._cursor("issues", through=None)
        self._cursor("pulls")
        self._run()
        check = self._status(self._issue(1))
        self.assertEqual((check["status"], check["reason"]), ("partial", "backfill_incomplete"))
        self.assertEqual(check["resource"], "issues")

    def test_partial_when_a_commit_backfill_is_still_walking_back(self) -> None:
        self._cursor("commits", phase="backfill")
        self._run()
        commit = self._node("commit", "ab" * 20, repo=self.repo)
        self.assertEqual(self._status(commit)["reason"], "backfill_incomplete")

    def test_partial_when_the_last_run_failed(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._run("failed", "RuntimeError: boom")
        self.assertEqual(self._status(self._issue(1))["reason"], "last_run_failed")

    def test_partial_when_the_last_run_stopped_early_though_recorded_as_succeeded(self) -> None:
        # run.py stores the rate-limit stop and the interrupt as `succeeded` with a note.
        for note in ("stopped early: rate limit floor reached", "interrupted by user; cursors preserved"):
            with self.subTest(note=note):
                self.conn.execute("DELETE FROM ingestion_run WHERE repo_node_id = %s", (self.repo,))
                self.conn.execute("DELETE FROM ingestion_cursor WHERE repo_node_id = %s", (self.repo,))
                for r in ("issues", "pulls"):
                    self._cursor(r)
                self._run("succeeded", note)
                self.assertEqual(self._status(self._issue(hash(note) % 100000 + 10))["reason"],
                                 "last_run_stopped_early")

    def test_only_the_latest_run_counts(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._run("failed", "old failure")
        self._run("succeeded")
        self.assertEqual(self._status(self._issue(1))["status"], "complete")

    def test_unknown_when_the_repository_has_no_cursor(self) -> None:
        check = self._status(self._issue(1))
        self.assertEqual((check["status"], check["reason"]), ("unknown", "no_ingestion_record"))

    # -- queries that match nothing -------------------------------------------------------

    def test_a_number_below_everything_created_in_the_window_is_outside_it(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._run()
        self._issue(500, created=FLOOR + timedelta(days=3))
        facts = coverage.repository_facts(self.conn, self.repo)
        check = coverage.query_check(self.conn, "#12", facts)
        self.assertEqual((check["status"], check["reason"]), ("outside_window", "older_than_window"))
        self.assertEqual(check["lowest_number_in_window"], 500)

    def test_a_number_inside_the_range_is_unknown_not_outside(self) -> None:
        # It might exist and have been missed. Saying "outside the window" would be a claim.
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._issue(500, created=FLOOR + timedelta(days=3))
        self._issue(900, created=NOW)
        facts = coverage.repository_facts(self.conn, self.repo)
        self.assertEqual(coverage.query_check(self.conn, "#700", facts)["status"], "unknown")

    def test_an_old_issue_updated_inside_the_window_does_not_lower_the_bar(self) -> None:
        # Created long ago, so its low number must not make #12 look like it was ever in range.
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._issue(20, created=FLOOR - timedelta(days=900))
        self._issue(500, created=FLOOR + timedelta(days=3))
        facts = coverage.repository_facts(self.conn, self.repo)
        self.assertEqual(coverage.query_check(self.conn, "#12", facts)["status"], "outside_window")

    def test_a_non_numeric_miss_is_unknown(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        facts = coverage.repository_facts(self.conn, self.repo)
        self.assertEqual(coverage.query_check(self.conn, "websocket multiplexing", facts)["reason"],
                         "not_in_graph")

    # -- the repository block and the documents ------------------------------------------

    def test_repository_facts_report_the_pinned_window_and_the_older_watermark(self) -> None:
        self._cursor("issues", through=NOW - timedelta(days=2))
        self._cursor("pulls", through=NOW - timedelta(days=5))
        self._run()
        facts = coverage.repository_facts(self.conn, self.repo)
        self.assertEqual(facts["window_floor"], FLOOR)
        self.assertEqual(facts["ingested_through"], NOW - timedelta(days=5), "the older of the two")
        self.assertEqual(facts["last_run_status"], "succeeded")
        self.assertIsNotNone(facts["last_ingested_at"])

    def test_an_answer_document_carries_valid_coverage(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._run()
        issue = self._issue(991500)
        c = retrieval.find_candidates(self.conn, "#991500", repo_node_id=self.repo, limit=1)
        doc = roundtrip(query.answer_document(self.conn, query="#991500", mode="why",
                                              candidates=c, as_of=None, max_depth=3))
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertEqual(doc["coverage"]["checks"][0]["subject"], f"node:{issue}")
        self.assertEqual(doc["coverage"]["checks"][0]["status"], "complete")
        self.assertEqual(doc["coverage"]["repository"]["name"], f"zz-cov-{id(self)}/r")

    def test_a_context_no_match_says_what_the_window_covered(self) -> None:
        for r in ("issues", "pulls"):
            self._cursor(r)
        self._issue(500, created=FLOOR + timedelta(days=3))
        doc = roundtrip(context.build(self.conn, "#12", repo_node_id=self.repo))
        self.assertEqual(doc["kind"], "no_match")
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertEqual(doc["coverage"]["checks"][0]["reason"], "older_than_window")

    def test_unknown_is_never_upgraded_when_nothing_is_recorded(self) -> None:
        issue = self._issue(1)
        c = retrieval.find_candidates(self.conn, "#1", repo_node_id=self.repo, limit=1)
        doc = roundtrip(query.answer_document(self.conn, query="#1", mode="why", candidates=c,
                                              as_of=None, max_depth=3))
        self.assertEqual(doc["coverage"]["checks"][0]["status"], "unknown")


class SchemaTest(unittest.TestCase):
    def test_coverage_is_required_on_every_kind_that_can_refuse_or_miss(self) -> None:
        for name in ("answer_document", "attempts_document", "context_document", "no_match_document"):
            with self.subTest(document=name):
                self.assertIn("coverage", SCHEMA["$defs"][name]["required"])

    def test_a_document_without_coverage_fails_validation(self) -> None:
        from tests.test_contract import CASES

        doc = roundtrip(CASES["no_match"]())
        del doc["coverage"]
        self.assertTrue(validate(doc, SCHEMA))

    def test_goldens_carry_it(self) -> None:
        from tests.test_contract import CASES

        for name in ("answer_explicit", "refusal_not_planned", "no_match"):
            with self.subTest(case=name):
                self.assertIn("coverage", roundtrip(CASES[name]()))


if __name__ == "__main__":
    unittest.main()
