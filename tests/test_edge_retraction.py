"""Edge retraction, as ADR 0009 defines it (issues #121 and #124's prerequisite).

An edge has two different ends: `valid_to` (the repository stopped saying it, event time) and
`retracted_at` (TraceMind stopped believing it, system time). What's checked, in the order the
ADR's tables state it:

- **Current queries** hide a retracted edge and an ended one.
- **`--as-of T`** answers "what the repository said at T, as we now understand it": an ended
  edge is visible at a T before its end; a RETRACTED edge is hidden at EVERY T, before or after
  the retraction, because we now believe the repository never said it.
- **`--as-recorded R`** answers "what our graph held at R": a retracted edge is visible at an R
  before the retraction and hidden after; an edit we had not yet seen is invisible to an R
  before we learned of it.
- **Audit guarantees**: no deletes, one-way explained retraction, append-only validity,
  re-assertion as a new row, an audit view.
- **A Decision** supported only by a withdrawn edge is withdrawn with its reason recorded, and
  one with another closing edge is not.
- **Extractors**: an edited body ends validity at the payload's `updated_at`; an unchanged body
  the extractor no longer reads retracts.
- **No "current edge" predicate anywhere lacks the retraction filter.**
- **The upgrade**: a populated schema-0015 database keeps every row through 0016.

DATABASE_URL-gated except the static predicate scan. The fixtures roll back.
"""

from __future__ import annotations

import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DSN = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime.now(UTC)
LONG_AGO = NOW - timedelta(days=60)
JAN = NOW - timedelta(days=40)   # the repository says it
FEB = NOW - timedelta(days=30)   # the repository edits it out
MAR = NOW - timedelta(days=20)   # TraceMind learns of the edit / retracts


class NoPredicateIsMissingTest(unittest.TestCase):
    """Every place that decides what a "current edge" is carries the retraction filter."""

    WINDOW = 420

    def _scan(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        bad = []
        for m in re.finditer(r"valid_to IS NULL", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            if line.lstrip().startswith(("--", "#")):
                continue  # a comment
            # Not predicates over edge rows: the guard trigger reasoning about OLD, and the CHECK
            # that pairs valid_to with the moment it was recorded.
            if "OLD.valid_to" in line or "valid_to_recorded_at IS NULL" in line:
                continue
            # The filter may sit before or after: the as-recorded clause leads with it.
            around = text[max(0, m.start() - self.WINDOW):m.start() + self.WINDOW]
            if "retracted_at IS NULL" not in around:
                bad.append(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}")
        return bad

    def test_python_and_eval_sources(self) -> None:
        bad = []
        for pattern in ("src/decision_graph/*.py", "eval/*.py", "eval/*.sql"):
            for path in sorted(ROOT.glob(pattern)):
                bad += self._scan(path)
        self.assertEqual(bad, [], "a `valid_to IS NULL` without the retraction filter beside it")

    def test_the_migration_that_adds_it_carries_the_filter_everywhere_it_redefines(self) -> None:
        self.assertEqual(self._scan(ROOT / "db/migrations/0016_edge_retraction.sql"), [])


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class EffectiveSchemaTest(unittest.TestCase):
    """The definitions Postgres actually holds, not the files that created them."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)

    def tearDown(self) -> None:
        self.conn.close()

    def test_no_function_view_or_index_defines_current_without_the_filter(self) -> None:
        defs = {}
        for r in self.conn.execute(
            "SELECT p.proname AS n, pg_get_functiondef(p.oid) AS d FROM pg_proc p "
            "JOIN pg_namespace s ON s.oid = p.pronamespace WHERE s.nspname = 'public' AND p.prokind = 'f'"
        ).fetchall():
            defs[f"function {r['n']}"] = r["d"]
        for r in self.conn.execute(
            "SELECT viewname AS n, pg_get_viewdef(format('%I', viewname)::regclass) AS d "
            "FROM pg_views WHERE schemaname = 'public'"
        ).fetchall():
            defs[f"view {r['n']}"] = r["d"]
        for r in self.conn.execute(
            "SELECT indexname AS n, indexdef AS d FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'edge'"
        ).fetchall():
            defs[f"index {r['n']}"] = r["d"]
        offenders = [
            name for name, d in defs.items()
            if "valid_to IS NULL" in d and "retracted_at IS NULL" not in d
            and name != "function edge_history_guard"
        ]
        self.assertEqual(offenders, [])
        self.assertGreater(sum("retracted_at IS NULL" in d for d in defs.values()), 6)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class BehaviourTest(unittest.TestCase):
    """A fixture graph per test: issue <- decision -> pull request, plus one extra edge."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._n = 994000

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    # -- fixtures -------------------------------------------------------------------------

    def node(self, node_type: str, *, thread: str | None = None) -> int:
        self._n += 1
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title, thread_key) VALUES (%s, %s, %s, %s) "
            "RETURNING id", (node_type, str(self._n), f"zz retraction {self._n}", thread),
        ).fetchone()["id"]

    def edge(self, src, dst, edge_type="references", *, created=LONG_AGO, valid_from=JAN,
             valid_to=None, extractor="test_fixture", recorded=None) -> int:
        # `recorded` is when TraceMind LEARNED valid_to. The guard fills it with now() when a
        # row is inserted already ended and none is given; a fixture can state it.
        return self.conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, "
            "created_at, valid_from, valid_to, valid_to_recorded_at, observed_at) VALUES "
            "(%s, %s, %s, 'explicit', 'explicit', %s, %s, %s, %s, %s, %s) RETURNING id",
            (src, dst, edge_type, extractor, created, valid_from, valid_to,
             recorded if valid_to else None, valid_from),
        ).fetchone()["id"]

    def walk(self, start: int, **kw):
        from decision_graph import reasoning

        edge_types = ("references",)
        rows = self.conn.execute(
            reasoning._WALK_SQL.format(
                tag_clause="",
                time_clause=reasoning._CURRENT_CLAUSE if kw.get("as_recorded") is None
                else reasoning._RECORDED_CLAUSE),
            {"start": start, "types": list(edge_types), "max_depth": 1,
             "as_of": kw.get("as_of"), "recorded": kw.get("as_recorded")},
        ).fetchall()
        return {r["path_edges"][0] for r in rows}

    def retract(self, edge_id: int, reason="test retraction") -> datetime:
        self.conn.execute("SELECT retract_edge(%s, %s)", (edge_id, reason))
        return self.conn.execute("SELECT retracted_at FROM edge WHERE id = %s",
                                 (edge_id,)).fetchone()["retracted_at"]

    # -- current, --as-of, --as-recorded --------------------------------------------------

    def test_a_retracted_edge_is_hidden_from_current_queries(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.assertEqual(self.walk(a), {e})
        self.retract(e)
        self.assertEqual(self.walk(a), set())

    def test_as_of_hides_a_retracted_edge_at_every_time_before_and_after_it(self) -> None:
        # We now believe the repository never said it, so no `--as-of` shows it. This is
        # the property that reusing valid_to would have broken.
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, valid_from=JAN)
        retracted_at = self.retract(e)
        for label, t in (("before the edge existed", LONG_AGO), ("after it began", FEB),
                         ("just before the retraction", retracted_at - timedelta(seconds=1)),
                         ("after the retraction", retracted_at + timedelta(days=1))):
            with self.subTest(as_of=label):
                self.assertEqual(self.walk(a, as_of=t), set())

    def test_as_of_shows_an_ended_edge_only_before_its_end(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, valid_from=JAN, valid_to=FEB)
        self.assertEqual(self.walk(a, as_of=JAN + timedelta(days=1)), {e}, "true while it was said")
        self.assertEqual(self.walk(a, as_of=FEB + timedelta(days=1)), set(), "gone once edited out")
        self.assertEqual(self.walk(a), set(), "and not current")

    def test_as_recorded_before_a_retraction_still_sees_the_edge_and_after_does_not(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, created=LONG_AGO, valid_from=JAN)
        retracted_at = self.retract(e)
        self.assertEqual(self.walk(a, as_recorded=retracted_at - timedelta(seconds=1)), {e},
                         "we believed it then")
        self.assertEqual(self.walk(a, as_recorded=retracted_at + timedelta(seconds=1)), set())

    def test_as_recorded_before_the_edge_was_created_sees_nothing(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        self.edge(a, b, created=FEB, valid_from=JAN)
        self.assertEqual(self.walk(a, as_recorded=JAN), set(), "we had not recorded it yet")

    def test_an_edit_we_had_not_yet_seen_is_invisible_to_as_recorded_before_we_learned_it(self) -> None:
        # valid_to = FEB (the repository edited it out), but we only learned of it at MAR.
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, valid_from=JAN, valid_to=FEB, recorded=MAR)
        between = FEB + timedelta(days=3)
        self.assertEqual(self.walk(a, as_recorded=between), {e}, "TraceMind had not seen the edit")
        self.assertEqual(self.walk(a, as_recorded=MAR + timedelta(days=1)), set())

    def test_as_recorded_with_as_of_asks_what_was_said_at_T_as_recorded_at_R(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, created=LONG_AGO, valid_from=JAN, valid_to=FEB)
        # The edit was recorded a moment ago, so R must be later than that to have seen it.
        seen_it = NOW + timedelta(hours=1)
        self.assertEqual(self.walk(a, as_recorded=seen_it, as_of=JAN + timedelta(days=2)), {e})
        self.assertEqual(self.walk(a, as_recorded=seen_it, as_of=FEB + timedelta(days=2)), set())

    # -- audit guarantees -----------------------------------------------------------------

    def test_a_direct_delete_of_an_edge_is_refused(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        import psycopg

        with self.assertRaisesRegex(psycopg.errors.IntegrityConstraintViolation, "never deleted"):
            self.conn.execute("DELETE FROM edge WHERE id = %s", (e,))

    def test_deleting_a_node_still_removes_its_edges(self) -> None:
        # Withdrawing a Decision deletes its node, and the edges go with it.
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.conn.execute("DELETE FROM node WHERE id = %s", (a,))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM edge WHERE id = %s", (e,)).fetchone())

    def test_the_fixture_escape_hatch_permits_a_delete(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.conn.execute("SET LOCAL dg.allow_edge_delete = 'on'")
        self.conn.execute("DELETE FROM edge WHERE id = %s", (e,))

    def test_retraction_needs_a_reason(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        import psycopg

        for bad in ("", "   ", None):
            with self.subTest(reason=bad):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.transaction():
                        self.conn.execute("SELECT retract_edge(%s, %s)", (e, bad))

    def test_the_database_clock_sets_retracted_at_not_the_caller(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.conn.execute("UPDATE edge SET retracted_at = %s, retraction_reason = 'x' WHERE id = %s",
                          (LONG_AGO, e))
        got = self.conn.execute("SELECT retracted_at FROM edge WHERE id = %s", (e,)).fetchone()["retracted_at"]
        self.assertGreater(got, NOW - timedelta(minutes=5), "the caller's past timestamp was kept")

    def test_a_retracted_edge_is_frozen(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.retract(e)
        import psycopg

        for sql in ("UPDATE edge SET extractor = 'other' WHERE id = %s",
                    "UPDATE edge SET retracted_at = NULL, retraction_reason = NULL WHERE id = %s",
                    "UPDATE edge SET valid_to = now() WHERE id = %s"):
            with self.subTest(sql=sql):
                with self.assertRaisesRegex(psycopg.errors.IntegrityConstraintViolation, "frozen"):
                    with self.conn.transaction():
                        self.conn.execute(sql, (e,))

    def test_retracting_twice_is_a_no_op_that_reports_it(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b)
        self.assertTrue(self.conn.execute("SELECT retract_edge(%s, 'once')", (e,)).fetchone()["retract_edge"])
        self.assertFalse(self.conn.execute("SELECT retract_edge(%s, 'twice')", (e,)).fetchone()["retract_edge"])

    def test_validity_is_append_only(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, valid_from=JAN, valid_to=FEB)
        import psycopg

        for sql in ("UPDATE edge SET valid_to = valid_to + interval '1 day' WHERE id = %s",
                    "UPDATE edge SET valid_to = NULL WHERE id = %s",
                    "UPDATE edge SET valid_from = valid_from - interval '1 day' WHERE id = %s"):
            with self.subTest(sql=sql):
                with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
                    with self.conn.transaction():
                        self.conn.execute(sql, (e,))

    def test_ending_validity_records_when_we_learned_it(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, valid_from=JAN)
        self.conn.execute("UPDATE edge SET valid_to = %s WHERE id = %s", (FEB, e))
        row = self.conn.execute("SELECT valid_to, valid_to_recorded_at FROM edge WHERE id = %s", (e,)).fetchone()
        self.assertEqual(row["valid_to"], FEB, "event time kept as given")
        self.assertGreater(row["valid_to_recorded_at"], NOW - timedelta(minutes=5), "system time is now")

    def test_reasserting_a_retracted_edge_is_a_new_row_and_the_old_one_stays(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        first = self.edge(a, b)
        self.retract(first)
        second = self.edge(a, b)  # would have violated the unique index before 0016
        self.assertNotEqual(first, second)
        self.assertEqual(self.walk(a), {second})
        row = self.conn.execute("SELECT retracted_at FROM edge WHERE id = %s", (first,)).fetchone()
        self.assertIsNotNone(row["retracted_at"], "the history survived")

    def test_the_audit_view_lists_every_retraction_with_its_reason(self) -> None:
        a, b = self.node("issue"), self.node("issue")
        e = self.edge(a, b, extractor="pr_body_closing_keyword")
        self.retract(e, "the extractor stopped reading it")
        [row] = self.conn.execute("SELECT * FROM v_edge_retraction_audit WHERE edge_id = %s", (e,)).fetchall()
        self.assertEqual(row["retraction_reason"], "the extractor stopped reading it")
        self.assertEqual(row["extractor"], "pr_body_closing_keyword")
        self.assertIsNotNone(row["retracted_at"])

    # -- a Decision supported only by a withdrawn edge -----------------------------------------

    def _decision_thread(self, closers: int = 1):
        thread = f"thread:994:{self._n}"
        issue, pr = self.node("issue", thread=thread), self.node("pull_request", thread=thread)
        self.conn.execute("INSERT INTO issue (node_id, number, state) VALUES (%s, %s, 'closed')", (issue, self._n))
        self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, %s, "
                          "'closed', now())", (pr, self._n))
        closes = [self.edge(pr, issue, "closes", extractor="pr_body_closing_keyword")]
        extra = self.node("pull_request", thread=thread)
        if closers > 1:
            self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, %s, "
                              "'closed', now())", (extra, self._n))
            closes.append(self.edge(extra, issue, "closes", extractor="pr_body_closing_keyword"))
        decision = self.node("decision", thread=thread)
        self.conn.execute("UPDATE node SET external_id = %s WHERE id = %s", (thread, decision))
        self.conn.execute("INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 'f')", (decision,))
        self.edge(decision, issue, "motivated_by", extractor="synthesis_closes_cluster")
        self.edge(decision, pr, "implemented_by", extractor="synthesis_closes_cluster")
        return decision, thread, closes

    def _withdraw(self):
        self.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        return self.conn.execute("SELECT * FROM retract_unsupported_decisions()").fetchone()

    def test_a_decision_supported_only_by_a_retracted_edge_is_withdrawn_with_its_reason(self) -> None:
        decision, thread, closes = self._decision_thread()
        self.retract(closes[0], "no longer yielded")
        out = self._withdraw()
        self.assertEqual(out["retracted"], 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM decision WHERE node_id = %s", (decision,)).fetchone())
        [w] = self.conn.execute("SELECT * FROM decision_withdrawal WHERE thread_key = %s", (thread,)).fetchall()
        self.assertIn("no longer holds a current closing-keyword edge", w["reason"])
        self.assertEqual(w["status"], "reconstructed")

    def test_the_same_when_validity_ends_because_the_body_was_edited(self) -> None:
        decision, thread, closes = self._decision_thread()
        self.conn.execute("UPDATE edge SET valid_to = now() WHERE id = %s", (closes[0],))
        self.assertEqual(self._withdraw()["retracted"], 1)

    def test_a_decision_with_a_second_closing_edge_stands(self) -> None:
        decision, thread, closes = self._decision_thread(closers=2)
        self.retract(closes[0])
        self.assertEqual(self._withdraw()["retracted"], 0)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM decision WHERE node_id = %s", (decision,)).fetchone())

    def test_an_untouched_decision_is_not_withdrawn(self) -> None:
        _, thread, _ = self._decision_thread()
        self.assertEqual(self._withdraw()["retracted"], 0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM decision_withdrawal WHERE thread_key = %s", (thread,)).fetchone())


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ExtractorTest(unittest.TestCase):
    """Re-reading a body: an edit ends validity, an unchanged body the extractor drops retracts."""

    REPO = "pallets/flask"
    PR = 994500

    def setUp(self) -> None:
        from decision_graph import db, extractors
        from decision_graph.config import Settings

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        repo = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('repository', %s) RETURNING id",
            (f"zz-retract-{id(self)}",)).fetchone()["id"]
        self.ctx = extractors.Context(conn=self.conn, client=None,  # type: ignore[arg-type]
                                      settings=Settings(database_url=DSN, github_token="x", target_repo=self.REPO),
                                      repo_node_id=repo)
        self.extractors = extractors
        self.issue = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id, title) VALUES ('issue', '994501', %s, 't') "
            "RETURNING id", (repo,)).fetchone()["id"]
        self.conn.execute("INSERT INTO issue (node_id, number, state) VALUES (%s, 994501, 'open')", (self.issue,))

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def payload(self, body: str, updated: datetime) -> dict:
        return {"number": self.PR, "node_id": "PR_x", "title": "t", "html_url": "https://x/pull/1",
                "state": "closed", "body": body, "created_at": JAN.isoformat(),
                "updated_at": updated.isoformat(), "closed_at": updated.isoformat(), "user": None,
                "pull_request": {"merged_at": updated.isoformat()}}

    def closes_edges(self, *, include_ended=True):
        sql = ("SELECT id, valid_from, valid_to, retracted_at, retraction_reason FROM edge "
               "WHERE edge_type = 'closes' AND dst_node_id = %s")
        return self.conn.execute(sql, (self.issue,)).fetchall()

    def test_editing_the_keyword_out_ends_validity_at_the_payload_time_and_keeps_history(self) -> None:
        pr = self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        [edge] = self.closes_edges()
        self.assertIsNone(edge["valid_to"])
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("no keyword now", FEB))
        [edge] = self.closes_edges()
        self.assertEqual(edge["valid_to"], FEB, "validity ends at the payload's updated_at")
        self.assertIsNone(edge["retracted_at"], "an edit is the repository's change, not a retraction")
        # ...and the edge is still there for a point in time before the edit.
        from decision_graph import reasoning

        rows = self.conn.execute(
            reasoning._WALK_SQL.format(tag_clause="", time_clause=reasoning._CURRENT_CLAUSE),
            {"start": pr, "types": ["closes"], "max_depth": 1, "as_of": JAN + timedelta(days=1),
             "recorded": None}).fetchall()
        self.assertEqual(len(rows), 1, "--as-of before the edit still returns the edge")
        rows = self.conn.execute(
            reasoning._WALK_SQL.format(tag_clause="", time_clause=reasoning._CURRENT_CLAUSE),
            {"start": pr, "types": ["closes"], "max_depth": 1, "as_of": FEB + timedelta(days=1),
             "recorded": None}).fetchall()
        self.assertEqual(rows, [], "and after it, it is gone")

    def test_re_extracting_an_unchanged_body_changes_nothing(self) -> None:
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        before = [dict(e) for e in self.closes_edges()]
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        self.assertEqual([dict(e) for e in self.closes_edges()], before)

    def test_a_stale_delivery_cannot_end_an_edge_the_current_body_states(self) -> None:
        # #120: an older payload without the keyword arrives after a newer one with it.
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", FEB))
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("older, no keyword", JAN))
        [edge] = self.closes_edges()
        self.assertIsNone(edge["valid_to"])
        self.assertIsNone(edge["retracted_at"])

    def test_an_unchanged_body_the_extractor_no_longer_reads_is_retracted_not_ended(self) -> None:
        pr = self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        # The parser "changes its mind": the stored text still says it, but we now treat the
        # edge as one the current rules don't yield. Simulate by storing different text
        # under the same updated_at, as a reconcile pass over stored bodies would see it.
        self.conn.execute("UPDATE pull_request SET body = 'no keyword now' WHERE node_id = %s", (pr,))
        self.assertEqual(self.extractors.retract_unsupported_edges(self.ctx), 1)
        [edge] = self.closes_edges()
        self.assertIsNone(edge["valid_to"], "validity untouched: the repository never changed")
        self.assertIsNotNone(edge["retracted_at"])
        self.assertIn("no longer yields closes #994501", edge["retraction_reason"])

    def test_reconcile_retracts_nothing_when_every_edge_is_still_yielded(self) -> None:
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        self.assertEqual(self.extractors.retract_unsupported_edges(self.ctx), 0)

    def test_edges_from_other_sources_are_never_touched(self) -> None:
        pr = self.extractors.extract_issue_or_pr(self.ctx, self.payload("Fixes #994501", JAN))
        reviewer = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) VALUES ('person', 'rev', %s) RETURNING id",
            (self.ctx.repo_node_id,)).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from) "
            "VALUES (%s, %s, 'reviewed', 'explicit', 'explicit', 'pr_review', now() - interval '1 day')",
            (reviewer, pr))
        self.extractors.extract_issue_or_pr(self.ctx, self.payload("completely rewritten", FEB))
        [row] = self.conn.execute("SELECT valid_to, retracted_at FROM edge WHERE extractor = 'pr_review'").fetchall()
        self.assertIsNone(row["valid_to"])
        self.assertIsNone(row["retracted_at"])


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class UpgradeTest(unittest.TestCase):
    """A populated database at schema 0015 keeps every row through 0016."""

    NAME = "dg_upgrade_probe"

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        cls.admin = psycopg.connect(DSN, autocommit=True)
        cls.admin.execute(f"DROP DATABASE IF EXISTS {cls.NAME}")
        cls.admin.execute(f"CREATE DATABASE {cls.NAME}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.admin.execute(f"DROP DATABASE IF EXISTS {cls.NAME} WITH (FORCE)")
        cls.admin.close()

    def _dsn(self) -> str:
        from urllib.parse import urlsplit, urlunsplit

        p = urlsplit(DSN)
        return urlunsplit((p.scheme, p.netloc, "/" + self.NAME, p.query, p.fragment))

    def test_existing_data_survives_and_the_new_columns_are_backfilled(self) -> None:
        from decision_graph import db, migrate

        migrations = ROOT / "db" / "migrations"
        before = {p.name for p in migrate.discover(migrations) if p.name < "0016"}
        conn = db.connect(self._dsn())
        migrate.apply_all(conn, migrations, only=before)
        self.assertNotIn("retracted_at", {
            r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'edge'").fetchall()})

        thread = "thread:1:pr-7"
        ids = {}
        for kind, ext, tk in (("repository", "o/r", None), ("issue", "1", thread), ("pull_request", "7", thread),
                              ("decision", thread, thread)):
            ids[kind] = conn.execute("INSERT INTO node (node_type, external_id, title, thread_key) VALUES "
                                     "(%s, %s, %s, %s) RETURNING id", (kind, ext, f"t {kind}", tk)).fetchone()["id"]
        conn.execute("INSERT INTO issue (node_id, number, state) VALUES (%s, 1, 'closed')", (ids["issue"],))
        conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, 7, 'closed', now())",
                     (ids["pull_request"],))
        conn.execute("INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 's')", (ids["decision"],))
        for src, dst, et, ex in ((ids["pull_request"], ids["issue"], "closes", "pr_body_closing_keyword"),
                                 (ids["decision"], ids["issue"], "motivated_by", "synthesis_closes_cluster"),
                                 (ids["decision"], ids["pull_request"], "implemented_by", "synthesis_closes_cluster")):
            conn.execute("INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from) "
                         "VALUES (%s, %s, %s, 'explicit', 'explicit', %s, now() - interval '3 days')", (src, dst, et, ex))
        # One edge ended before the migration, as synthesis does.
        ended = conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from, valid_to) "
            "VALUES (%s, %s, 'references', 'explicit', 'explicit', 'pr_body_issue_mention', "
            "now() - interval '3 days', now() - interval '1 day') RETURNING id, valid_to",
            (ids["pull_request"], ids["issue"])).fetchone()
        conn.commit()
        edges_before = conn.execute("SELECT count(*) AS n FROM edge").fetchone()["n"]
        nodes_before = conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"]

        result = migrate.apply_all(conn, migrations)
        self.assertEqual(result.applied, ["0016_edge_retraction.sql"])
        self.assertEqual(conn.execute("SELECT count(*) AS n FROM edge").fetchone()["n"], edges_before)
        self.assertEqual(conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"], nodes_before)
        row = conn.execute("SELECT valid_to, valid_to_recorded_at, retracted_at FROM edge WHERE id = %s",
                           (ended["id"],)).fetchone()
        self.assertEqual(row["valid_to_recorded_at"], row["valid_to"], "backfilled: the end and its recording coincide")
        self.assertIsNone(row["retracted_at"])
        self.assertEqual(conn.execute("SELECT count(*) AS n FROM edge WHERE valid_to IS NULL AND valid_to_recorded_at IS NOT NULL").fetchone()["n"], 0)
        # The Decision still passes the rubric, and nothing was withdrawn by the migration.
        self.assertTrue(conn.execute("SELECT passes FROM v_decision_rubric_audit").fetchone()["passes"])
        self.assertEqual(conn.execute("SELECT count(*) AS n FROM decision_withdrawal").fetchone()["n"], 0)
        # And the guards are live on the populated table.
        import psycopg

        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            conn.execute("DELETE FROM edge WHERE id = %s", (ended["id"],))
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    unittest.main()
