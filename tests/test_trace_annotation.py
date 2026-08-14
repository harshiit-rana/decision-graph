"""Tests for the Decision annotation in evaluation traces (issue #19).

The defect these guard against was not a wrong answer — the graph was correct — but a
trace that showed the reader the wrong pull request number. A Why-walk reaching a
Decision across `motivated_by` stops there and never traverses `implemented_by`, so the
only PR visible was the one embedded in the thread key, and that key names the cluster,
not the implementer. Where a change took two attempts it names the abandoned one.

So these assert a *reporting* property: whatever the graph currently credits a Decision
to must appear in the trace, and anything unmerged or missing must appear loudly. A
report that quietly drops either is the failure being prevented.
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from decision_graph import evaluation, reasoning

DSN = os.environ.get("DATABASE_URL")


def _load_renderer():
    """eval/ is a script directory, not a package, so load it by path."""
    path = Path(__file__).resolve().parents[1] / "eval" / "render_report.py"
    spec = importlib.util.spec_from_file_location("render_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FormatImplementerTest(unittest.TestCase):
    def test_merged_pull_request_states_its_merge_date(self) -> None:
        text = evaluation._format_implementer(
            {
                "implementer_type": "pull_request",
                "implementer_ref": "5899",
                "merged_at": datetime(2026, 1, 25, 3, 53, tzinfo=timezone.utc),
            }
        )
        self.assertEqual(text, "PR 5899, merged 2026-01-25")

    def test_unmerged_pull_request_says_so_explicitly(self) -> None:
        # Post-#17 this cannot occur. It is spelled out rather than left blank so that a
        # regression announces itself instead of reading as an ordinary path.
        text = evaluation._format_implementer(
            {
                "implementer_type": "pull_request",
                "implementer_ref": "5867",
                "merged_at": None,
            }
        )
        self.assertIn("NOT MERGED", text)

    def test_commit_implementer_is_abbreviated_and_not_called_unmerged(self) -> None:
        # A commit reachable in the graph is on the default branch by construction, so
        # borrowing the PR vocabulary of "not merged" would be actively misleading.
        text = evaluation._format_implementer(
            {
                "implementer_type": "commit",
                "implementer_ref": "c77a5203438fe772d41f6a47303ad3f57a4efe6d",
                "merged_at": None,
            }
        )
        self.assertEqual(text, "commit c77a520")
        self.assertNotIn("NOT MERGED", text)


class DescribeTest(unittest.TestCase):
    def _path(self) -> reasoning.Path:
        return reasoning.Path(
            node_ids=[928],
            steps=[],
            titles={928: "deprecate `should_ignore_error`"},
            types={928: "decision"},
        )

    def test_annotation_is_appended_when_present(self) -> None:
        text = evaluation._describe(
            self._path(), 928, {928: "implemented by PR 5899, merged 2026-01-25"}
        )
        self.assertIn("decision:928", text)
        self.assertIn("PR 5899", text)

    def test_unannotated_node_is_unchanged(self) -> None:
        self.assertEqual(
            evaluation._describe(self._path(), 928),
            "decision:928 — deprecate `should_ignore_error`",
        )


class RenderTest(unittest.TestCase):
    """The annotation is only useful if the renderer keeps it distinguishable."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = _load_renderer()

    def test_thread_source_ref_is_labelled_as_a_cluster(self) -> None:
        html = self.r.render_ref("thread:30:pr-5867")
        self.assertIn("cluster", html)
        self.assertIn("thread:30:pr-5867", html)

    def test_url_source_ref_is_left_alone(self) -> None:
        html = self.r.render_ref("https://github.com/pallets/flask/pull/5899")
        self.assertNotIn("ref-kind", html)

    def test_annotation_is_split_out_of_the_node_label(self) -> None:
        html = self.r.render_node("decision:928 — x  [implemented by PR 5899, merged 2026-01-25]")
        self.assertIn("node-note", html)
        self.assertNotIn("[", html)

    def test_broken_claims_render_as_warnings(self) -> None:
        for note in ("implemented by PR 5867, NOT MERGED", "no current implementer"):
            with self.subTest(note=note):
                html = self.r.render_node(f"decision:1 — x  [{note}]")
                self.assertIn("node-note warn", html)

    def test_ordinary_annotation_is_not_a_warning(self) -> None:
        html = self.r.render_node("decision:1 — x  [implemented by PR 5899, merged 2026-01-25]")
        self.assertNotIn("warn", html)

    def test_bracketed_titles_survive_intact(self) -> None:
        # flask really ships these. A left-hand split would tear the title in half and
        # render "bytes]" as if it were an annotation.
        for title in (
            "pull_request:41 — Loosen send_file annotation to include typing.IO[bytes]",
            "commit:9 — [pre-commit.ci lite] apply automatic fixes",
        ):
            with self.subTest(title=title):
                html = self.r.render_node(title)
                self.assertNotIn("node-note", html)

    def test_bracketed_title_plus_annotation_splits_at_the_annotation(self) -> None:
        html = self.r.render_node(
            "pull_request:41 — include typing.IO[bytes]  [implemented by PR 5899, merged 2026-01-25]"
        )
        self.assertIn("node-note", html)
        self.assertIn("typing.IO[bytes]", html)
        self.assertIn("PR 5899", html)

    def test_node_text_is_escaped(self) -> None:
        html = self.r.render_node("decision:1 — <script>")
        self.assertNotIn("<script>", html)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class DecisionAnnotationsTest(unittest.TestCase):
    """Each test seeds its own fixture in a transaction and rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        # The rubric guard is deferred, so a fixture Decision that would not qualify is
        # never challenged — these tests are about reporting, not about the rubric.
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._seq = 0

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _node(self, node_type: str, title: str = "") -> int:
        self._seq += 1
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) "
            "RETURNING id",
            (node_type, f"anno-{id(self)}-{self._seq}", title),
        ).fetchone()["id"]

    def _decision(self) -> int:
        node_id = self._node("decision", "fixture decision")
        self.conn.execute(
            "INSERT INTO decision (node_id, status, summary) "
            "VALUES (%s, 'reconstructed', 'fixture')",
            (node_id,),
        )
        return node_id

    def _pr(self, number: int, merged_at: datetime | None) -> int:
        node_id = self._node("pull_request", f"PR {number}")
        self.conn.execute(
            "INSERT INTO pull_request (node_id, number, state, merged_at) "
            "VALUES (%s, %s, %s, %s)",
            (node_id, number, "closed", merged_at),
        )
        self.conn.execute("UPDATE node SET external_id = %s WHERE id = %s", (str(number), node_id))
        return node_id

    def _implemented_by(self, decision: int, target: int, *, valid_to=None) -> None:
        self.conn.execute(
            """
            INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                              extractor, valid_from, valid_to)
            VALUES (%s, %s, 'implemented_by', 'explicit', 'explicit', 'test_fixture',
                    now() - interval '1 day', %s)
            """,
            (decision, target, valid_to),
        )

    MERGED = datetime(2026, 1, 25, tzinfo=timezone.utc)

    def test_credits_the_current_implementer(self) -> None:
        d = self._decision()
        self._implemented_by(d, self._pr(5899, self.MERGED))
        notes = evaluation.decision_annotations(self.conn, [d])
        self.assertEqual(notes[d], "implemented by PR 5899, merged 2026-01-25")

    def test_superseded_implementer_is_excluded(self) -> None:
        # This is the exact shape of decision 928: the abandoned attempt is still in the
        # graph as history and must not be reported as the credited implementer.
        d = self._decision()
        self._implemented_by(d, self._pr(5867, None), valid_to=datetime.now(timezone.utc))
        self._implemented_by(d, self._pr(5899, self.MERGED))
        notes = evaluation.decision_annotations(self.conn, [d])
        self.assertIn("5899", notes[d])
        self.assertNotIn("5867", notes[d])

    def test_every_current_implementer_is_listed(self) -> None:
        # No LIMIT 1. The #17 fix briefly left two live implementers on one Decision;
        # a report showing only the first would have hidden it.
        d = self._decision()
        self._implemented_by(d, self._pr(5867, None))
        self._implemented_by(d, self._pr(5899, self.MERGED))
        notes = evaluation.decision_annotations(self.conn, [d])
        self.assertIn("5867", notes[d])
        self.assertIn("5899", notes[d])
        self.assertIn("NOT MERGED", notes[d])

    def test_decision_with_no_implementer_is_reported_not_omitted(self) -> None:
        d = self._decision()
        notes = evaluation.decision_annotations(self.conn, [d])
        self.assertEqual(notes[d], "no current implementer")

    def test_empty_input_does_not_query(self) -> None:
        self.assertEqual(evaluation.decision_annotations(self.conn, []), {})


if __name__ == "__main__":
    unittest.main()
