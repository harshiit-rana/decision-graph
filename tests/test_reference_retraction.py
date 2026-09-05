"""Which queued references get withdrawn, and which must not (issue #61).

`db/tests/0013_reference_retraction_checks.sql` covers the schema half — that a withdrawal
is auditable, exclusive with resolution, and reversible. This covers the decision half:
`retract_stale_references` withdraws a row iff re-reading the same stored body with the
current extractors no longer yields it.

The distinction under test is the one the issue turns on. "The parser no longer produces
this" and "this can never resolve" look alike in the queue and are opposite verdicts: the
first is an obsolete row that will fire the moment its target arrives, the second is the
bounded window working correctly. Getting them the wrong way round either leaves the
loaded row in place or deletes five perfectly good references to out-of-window issues.

Requires a live database, and rolls back. Skipped when DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import unittest

from decision_graph import extractors
from decision_graph.config import Settings

DSN = os.environ.get("DATABASE_URL")
REPO = "pallets/flask"


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ReferenceRetractionTest(unittest.TestCase):
    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._seq = 0
        self.repo_node_id = self.node("repository", external_id=f"fixture-repo-{id(self)}")
        self.ctx = extractors.Context(
            conn=self.conn,
            # Retraction reads stored text and never calls GitHub — that is what makes it
            # safe to run on --reconcile, which is documented as costing no API calls. A
            # None client is therefore not a shortcut here; a client this code could reach
            # for would be the bug.
            client=None,  # type: ignore[arg-type]
            settings=Settings(database_url=DSN, github_token="unused", target_repo=REPO),
            repo_node_id=self.repo_node_id,
        )

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    # -- fixture helpers ---------------------------------------------------

    def node(self, node_type: str, *, external_id: str | None = None) -> int:
        self._seq += 1
        repo = getattr(self, "repo_node_id", None)
        row = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) VALUES (%s, %s, %s) "
            "RETURNING id",
            (
                node_type,
                external_id or f"fixture-{id(self)}-{self._seq}",
                repo if node_type != "repository" else None,
            ),
        ).fetchone()
        return row["id"]

    def pull_request(self, number: int, body: str | None) -> int:
        node_id = self.node("pull_request", external_id=str(number))
        self.conn.execute(
            "INSERT INTO pull_request (node_id, number, state, body) VALUES (%s, %s, %s, %s)",
            (node_id, number, "closed", body),
        )
        return node_id

    def enqueue(self, src: int, ref_number: int, edge_type: str = "closes") -> int:
        row = self.conn.execute(
            """
            INSERT INTO pending_reference (repo_node_id, src_node_id, ref_number,
                                           edge_type, extractor)
            VALUES (%s, %s, %s, %s, 'test_fixture') RETURNING id
            """,
            (self.repo_node_id, src, ref_number, edge_type),
        ).fetchone()
        return row["id"]

    def state(self, pending_id: int) -> tuple[bool, str | None]:
        row = self.conn.execute(
            "SELECT retracted_at, retraction_reason FROM pending_reference WHERE id = %s",
            (pending_id,),
        ).fetchone()
        return row["retracted_at"] is not None, row["retraction_reason"]

    # -- the case the issue is about ---------------------------------------

    def test_reference_only_present_in_an_html_comment_is_retracted(self) -> None:
        # flask PR 6106, reduced: commented-out template scaffolding above a real change.
        # #59 stopped the parser reading it; the row it had already queued stayed armed.
        pr = self.pull_request(6106, "<!-- Fixes #11 -->\n\nFix a typo in the docs.")
        stale = self.enqueue(pr, 11)

        self.assertEqual(extractors.retract_stale_references(self.ctx), 1)

        retracted, reason = self.state(stale)
        self.assertTrue(retracted)
        self.assertIn("#11", reason or "")

    def test_reference_the_parser_still_produces_survives(self) -> None:
        pr = self.pull_request(6110, "Fixes #5200 — real closing keyword, visible text.")
        live = self.enqueue(pr, 5200)

        self.assertEqual(extractors.retract_stale_references(self.ctx), 0)
        self.assertFalse(self.state(live)[0])

    def test_out_of_window_target_is_not_retracted(self) -> None:
        # The failure mode this function must not have. #5199 is a genuine closing
        # reference to an issue outside the 12-month window: unresolvable, and correct.
        # Retracting it would be reading "cannot resolve" as "was never produced".
        pr = self.pull_request(6111, "Fixes #5199")
        out_of_window = self.enqueue(pr, 5199)

        extractors.retract_stale_references(self.ctx)

        self.assertFalse(self.state(out_of_window)[0])

    def test_edge_type_must_match_not_just_the_number(self) -> None:
        # The body mentions 4242 but does not close it. A queued `closes` for that number
        # is a different claim from the `references` the parser now produces, and the
        # weaker edge standing in for the stronger one is exactly the substitution the
        # §5.1 rubric would then read as Validation.
        pr = self.pull_request(6112, "Related to #4242, but not fixed here.")
        wrong_type = self.enqueue(pr, 4242, "closes")
        right_type = self.enqueue(pr, 4242, "references")

        self.assertEqual(extractors.retract_stale_references(self.ctx), 1)
        self.assertTrue(self.state(wrong_type)[0])
        self.assertFalse(self.state(right_type)[0])

    def test_a_body_with_no_text_is_not_evidence(self) -> None:
        # A NULL body cannot distinguish "the author removed it" from "we never stored
        # it", so it licenses nothing. Silence is not the parser declining to produce.
        pr = self.pull_request(6113, None)
        unknown = self.enqueue(pr, 999)

        self.assertEqual(extractors.retract_stale_references(self.ctx), 0)
        self.assertFalse(self.state(unknown)[0])

    def test_retraction_is_reversible_by_re_reconciling(self) -> None:
        # The recovery path for a parser regression: withdraw a reference, restore the
        # rule that produces it, and the row comes back. If the open-row unique index
        # still covered retracted rows, the re-enqueue would be swallowed by ON CONFLICT
        # DO NOTHING and report success while changing nothing.
        pr = self.pull_request(6114, "<!-- Fixes #12 -->")
        self.enqueue(pr, 12)
        self.assertEqual(extractors.retract_stale_references(self.ctx), 1)

        extractors._enqueue_reference(
            self.ctx,
            src_node_id=pr,
            number=12,
            edge_type="closes",
            extractor="test_fixture",
            source_ref=None,
            observed_at=None,
        )

        open_rows = self.conn.execute(
            "SELECT count(*) AS n FROM pending_reference WHERE src_node_id = %s "
            "AND ref_number = 12 AND resolved_at IS NULL AND retracted_at IS NULL",
            (pr,),
        ).fetchone()
        self.assertEqual(open_rows["n"], 1, "a retracted reference must be re-queueable")

    def test_a_retracted_reference_never_resolves_into_an_edge(self) -> None:
        # The whole point. The damage an obsolete row does happens on the run where its
        # target finally arrives, so the drain must not pick it up afterwards.
        pr = self.pull_request(6115, "<!-- Fixes #13 -->")
        self.enqueue(pr, 13)
        extractors.retract_stale_references(self.ctx)

        target = self.node("issue", external_id="13")
        self.conn.execute(
            "INSERT INTO issue (node_id, number, state) VALUES (%s, 13, 'closed')",
            (target,),
        )

        resolved, still_pending = extractors.drain_pending_references(self.ctx)

        self.assertEqual(resolved, 0)
        edges = self.conn.execute(
            "SELECT count(*) AS n FROM edge WHERE src_node_id = %s AND dst_node_id = %s",
            (pr, target),
        ).fetchone()
        self.assertEqual(edges["n"], 0, "a withdrawn reference became an edge anyway")


if __name__ == "__main__":
    unittest.main()
