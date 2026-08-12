"""Integration tests for the §5.3 explicit-first / inferred-fallback traversal order.

The real graph holds zero inferred edges, so none of this behaviour is exercised by
pallets/flask. These tests seed a fixture instead. Without them the fallback branch
would ship unverified — and its failure mode is silent: an implementation that simply
admits inferred edges in one pass returns answers that look entirely reasonable while
quietly blending tiers.

Requires a live database. Skipped when DATABASE_URL is unset, so the unit suite still
runs standalone:

    DATABASE_URL=postgresql://... python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import unittest

import psycopg

from decision_graph import reasoning
from decision_graph.reasoning import Mode

DSN = os.environ.get("DATABASE_URL")


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class TraversalFallbackTest(unittest.TestCase):
    """Each test runs in its own transaction and rolls back — no fixture leaks."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._seq = 0

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    # -- fixture helpers ---------------------------------------------------

    def node(self, node_type: str, title: str = "") -> int:
        self._seq += 1
        row = self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) "
            "RETURNING id",
            (node_type, f"fixture-{id(self)}-{self._seq}", title),
        ).fetchone()
        return row["id"]

    def edge(
        self, src: int, dst: int, edge_type: str, *, inferred: bool = False, relevance=None
    ) -> int:
        tier = "inferred" if inferred else "explicit"
        row = self.conn.execute(
            """
            INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                              extractor, relevance)
            VALUES (%s, %s, %s, %s, %s, 'test_fixture', %s) RETURNING id
            """,
            (src, dst, edge_type, tier, tier, relevance if inferred else None),
        ).fetchone()
        return row["id"]

    # -- §5.3 step 2: the fallback fires when nothing explicit connects -----

    def test_impact_falls_back_to_inferred_when_no_explicit_path(self) -> None:
        a = self.node("commit", "orphan commit")
        b = self.node("issue", "unreachable issue")
        self.edge(a, b, "depends_on", inferred=True, relevance=0.9)

        answer = reasoning.reason(self.conn, a, Mode.IMPACT, max_depth=2)

        self.assertTrue(answer.found, "fallback should have found the inferred route")
        self.assertTrue(answer.used_inferred_fallback)
        self.assertEqual(answer.paths[0].tier, "inferred")
        self.assertIn(b, answer.paths[0].node_ids)

    # -- §5.3 step 3: an explicit route excludes inferred edges ENTIRELY ----

    def test_inferred_edges_never_blend_into_a_connected_explicit_path(self) -> None:
        start = self.node("pull_request", "start pr")
        explicit_target = self.node("issue", "explicitly closed issue")
        inferred_target = self.node("issue", "merely similar issue")

        self.edge(start, explicit_target, "closes")
        self.edge(start, inferred_target, "depends_on", inferred=True, relevance=0.99)

        answer = reasoning.reason(self.conn, start, Mode.IMPACT, max_depth=2)

        self.assertTrue(answer.found)
        self.assertFalse(
            answer.used_inferred_fallback, "explicit path existed; fallback must not run"
        )

        reached = {n for p in answer.paths for n in p.node_ids}
        self.assertIn(explicit_target, reached)
        self.assertNotIn(
            inferred_target,
            reached,
            "an inferred edge leaked into an already-connected explicit answer",
        )
        for path in answer.paths:
            self.assertEqual(path.tier, "explicit")
            for step in path.steps:
                self.assertEqual(step.tag, "explicit")

    def test_why_falls_back_across_an_inferred_bridge_at_depth_two(self) -> None:
        # decision --implemented_by--> pr   (explicit)
        #   artifact ~~depends_on~~> pr     (inferred bridge)
        # Asking WHY the artifact exists is answerable only by crossing the bridge.
        decision_node = self.node("decision", "fixture decision")
        artifact_src = self.node("release", "formal artifact")
        self.conn.execute(
            "INSERT INTO decision (node_id, status, source_artifact_node_id) "
            "VALUES (%s, 'explicit', %s)",
            (decision_node, artifact_src),
        )
        pr = self.node("pull_request", "implementing pr")
        orphan = self.node("commit", "orphan artifact")

        self.edge(decision_node, pr, "implemented_by")
        self.edge(orphan, pr, "references", inferred=True, relevance=0.85)

        answer = reasoning.reason(self.conn, orphan, Mode.WHY, max_depth=3)

        self.assertTrue(answer.found)
        self.assertTrue(answer.used_inferred_fallback)
        self.assertIn(decision_node, answer.paths[0].node_ids)
        # Weakest-link tiering: one inferred hop makes the whole answer inferred.
        self.assertEqual(answer.paths[0].tier, "inferred")

    def test_no_path_at_all_reports_honestly(self) -> None:
        lonely = self.node("commit", "entirely disconnected")
        answer = reasoning.reason(self.conn, lonely, Mode.IMPACT, max_depth=3)

        self.assertFalse(answer.found)
        self.assertIn("no path found", answer.explanation)

    # -- the §5.1 gate still binds during traversal-relevant writes --------

    def test_gate_refuses_inferred_edge_below_threshold(self) -> None:
        a, b = self.node("commit"), self.node("issue")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.edge(a, b, "depends_on", inferred=True, relevance=0.10)

    def test_gate_refuses_inferred_edge_touching_a_decision(self) -> None:
        """Consequence worth stating: because no inferred edge may touch a Decision,
        the WHY fallback can never fire on its FIRST hop from a Decision node. The
        bridge always has to be further out, as in the depth-two test above."""
        artifact = self.node("release", "artifact")
        decision_node = self.node("decision", "guarded decision")
        self.conn.execute(
            "INSERT INTO decision (node_id, status, source_artifact_node_id) "
            "VALUES (%s, 'explicit', %s)",
            (decision_node, artifact),
        )
        commit = self.node("commit")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.edge(commit, decision_node, "references", inferred=True, relevance=0.99)

    def test_per_node_cap_binds(self) -> None:
        hub = self.node("commit", "hub")
        landed = 0
        for _ in range(6):
            partner = self.node("issue")
            try:
                with self.conn.transaction():
                    self.edge(hub, partner, "relates_to", inferred=True, relevance=0.9)
                landed += 1
            except psycopg.errors.CheckViolation:
                break
        cap = self.conn.execute(
            "SELECT value FROM graph_config WHERE key = 'inferred_edge_max_per_node'"
        ).fetchone()["value"]
        self.assertEqual(landed, int(cap))


if __name__ == "__main__":
    unittest.main()
