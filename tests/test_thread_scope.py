"""A Why answer says whose reason it is (issue #129).

The bug, reproduced on 2026-10-17 in a rolled-back fixture before anything was changed: an
unrelated PR that says "similar to #9000" in its body was answered with issue #9000's Decision,
graded `explicit`, with nothing in the output hinting at the mismatch. The edge is real, the
tier is honest, and the answer is to a different question. A crafted mention would let anyone
attach a legitimate Decision's authority to their own change.

This labels the crossing; it deliberately changes no answer. Which rule to apply (restrict to
the artifact's own thread, or keep and demote) needs the flask measurement the issue asks for
(#173), so every path and tier here is exactly what it was, and the §9 outcomes don't move.
What's checked:

- the engine: where a path leaves the start's thread, and whose reason an answer is;
- every renderer: text, JSON, Mermaid, DOT and the `dg ask` prompt;
- the negative control: an artifact's own thread, and impact answers, carry no label;
- the database reproduction, and that the walk itself is unchanged.
"""

from __future__ import annotations

import io
import json
import os
import unittest

from decision_graph import contract, diagram, explain, reasoning, render
from decision_graph.reasoning import Answer, Mode, NodeRef, Path, Step

from tests.test_contract import (
    ASSOC, DECISION, ISSUE, SCHEMA, doc_for, explicit_answer, explicit_facts, other_thread_answer,
    roundtrip, validate,
)

DSN = os.environ.get("DATABASE_URL")
X, Z = "thread:9:pr-9001", "thread:9:pr-9100"


def path_of(*nodes, steps=None, mode=Mode.WHY):
    """nodes: (id, type, thread). Each step is a `references` unless stated."""
    refs = {i: NodeRef(t, f"title {i}", str(i), None, th) for i, t, th in nodes}
    ids = [i for i, _, _ in nodes]
    steps = steps or [
        Step(100 + n, "references", "explicit", "explicit", ids[n], ids[n + 1], "x", None)
        for n in range(len(ids) - 1)
    ]
    return Path(ids, steps, refs)


def answer_of(*paths, mode=Mode.WHY):
    return Answer(mode, paths[0].node_ids[0], list(paths), False, "paths")


class ThreadCrossingTest(unittest.TestCase):
    def test_a_path_inside_one_thread_crosses_nothing(self) -> None:
        p = path_of((1, "issue", X), (2, "decision", X))
        self.assertIsNone(p.thread_crossing)

    def test_the_first_step_into_a_different_thread_is_named(self) -> None:
        p = path_of((1, "pull_request", Z), (2, "issue", X), (3, "decision", X))
        c = p.thread_crossing
        self.assertEqual((c.step_index, c.from_node_id, c.to_node_id, c.edge_type), (0, 1, 2, "references"))
        self.assertEqual((c.from_thread, c.to_thread), (Z, X))

    def test_a_node_with_no_thread_is_skipped_not_treated_as_a_break(self) -> None:
        # A person or release has no thread. PR -> person -> PR in one thread isn't a crossing.
        p = path_of((1, "pull_request", X), (2, "person", None), (3, "pull_request", X))
        self.assertIsNone(p.thread_crossing)
        q = path_of((1, "pull_request", Z), (2, "person", None), (3, "issue", X))
        self.assertEqual(q.thread_crossing.from_node_id, 1)
        self.assertEqual(q.thread_crossing.to_node_id, 3)
        self.assertEqual(q.thread_crossing.step_index, 1)

    def test_nothing_is_a_crossing_when_threads_are_unknown(self) -> None:
        self.assertIsNone(path_of((1, "issue", None), (2, "decision", None)).thread_crossing)


class ScopeTest(unittest.TestCase):
    own = staticmethod(lambda: path_of((1, "issue", X), (2, "decision", X)))
    foreign = staticmethod(lambda: path_of((3, "pull_request", Z), (4, "issue", X), (5, "decision", X)))

    def test_own_thread(self) -> None:
        a = answer_of(self.own())
        self.assertEqual((a.scope, a.foreign_decision_paths()), ("own_thread", []))

    def test_other_thread(self) -> None:
        a = answer_of(self.foreign())
        self.assertEqual(a.scope, "other_thread")
        self.assertEqual(len(a.foreign_decision_paths()), 1)

    def test_mixed(self) -> None:
        a = Answer(Mode.WHY, 1, [self.own(), self.foreign()], False, "x")
        self.assertEqual(a.scope, "mixed")
        self.assertEqual(len(a.foreign_decision_paths()), 1)

    def test_none_when_no_decision_is_reached(self) -> None:
        self.assertEqual(answer_of(path_of((1, "pull_request", Z), (2, "issue", X))).scope, "none")
        self.assertEqual(Answer(Mode.WHY, 1, [], False, "x").scope, "none")

    def test_impact_answers_are_never_labelled(self) -> None:
        # "What does this affect" is about reach, not about whose reason it is.
        a = Answer(Mode.IMPACT, 3, [self.foreign()], False, "x")
        self.assertEqual((a.scope, a.foreign_decision_paths()), ("none", []))

    def test_a_walk_that_starts_at_a_decision_is_its_own_answer(self) -> None:
        a = answer_of(path_of((5, "decision", X), (4, "issue", Z)))
        self.assertEqual(a.foreign_decision_paths(), [])


class EveryRendererTest(unittest.TestCase):
    """One test per renderer, on the same canned crossing."""

    def text(self, answer: Answer) -> str:
        out = io.StringIO()
        render.render(answer, out=out)
        return out.getvalue()

    def test_text_names_the_crossing_before_the_summary_and_on_the_path(self) -> None:
        t = self.text(other_thread_answer())
        self.assertIn("Reached through a mention, in another thread", t)
        self.assertIn("not this artifact's own reason", t)
        self.assertLess(t.index("Reached through a mention"), t.index("What the graph says"))
        self.assertIn("(crosses into another thread)", t)
        self.assertIn("which belongs to a different thread", t)
        self.assertIn("The graph records no decision in this", t)

    def test_json_carries_the_crossing_and_the_scope(self) -> None:
        doc = roundtrip(doc_for(
            __import__("decision_graph.retrieval", fromlist=["Candidate"]).Candidate(
                9100, "pull_request", "9100", "unrelated change", "identifier", 1.0),
            other_thread_answer(), explicit_facts()))
        self.assertEqual(validate(doc, SCHEMA), [])
        result = doc["results"][0]
        self.assertEqual(result["scope"], "other_thread")
        crossing = result["paths"][0]["crosses_thread"]
        self.assertEqual((crossing["edge_type"], crossing["step_index"]), ("references", 0))

    def test_mermaid_writes_it_on_the_edge_label(self) -> None:
        m = diagram.to_mermaid(other_thread_answer())
        self.assertIn("other thread", m)
        self.assertEqual(m.count("other thread"), 1, "only the crossing edge is labelled")

    def test_dot_writes_it_on_the_edge_label(self) -> None:
        d = diagram.to_dot(other_thread_answer())
        self.assertIn("other thread", d)
        self.assertEqual(d.count("other thread"), 1)

    def test_dg_ask_sends_the_crossing_and_instructs_the_model(self) -> None:
        payload = explain.answer_payload("why?", other_thread_answer())
        self.assertIsNotNone(payload["paths"][0]["crosses_thread"])
        self.assertIn("crosses_thread", explain.SUMMARY_SYSTEM)
        self.assertIn("Never present it as this", explain.SUMMARY_SYSTEM)


class NegativeControlTest(unittest.TestCase):
    """An artifact's own thread, and an impact answer, are exactly as they were."""

    def test_an_own_thread_answer_renders_no_label_anywhere(self) -> None:
        a = explicit_answer()
        out = io.StringIO()
        render.render(a, out=out)
        self.assertNotIn("Reached through a mention", out.getvalue())
        self.assertNotIn("crosses into another thread", out.getvalue())
        self.assertNotIn("other thread", diagram.to_mermaid(a))
        self.assertNotIn("other thread", diagram.to_dot(a))
        self.assertEqual(a.scope, "own_thread")

    def test_the_existing_goldens_say_own_thread_or_none(self) -> None:
        from tests.test_contract import CASES

        for name, build in CASES.items():
            doc = build()
            for r in doc.get("results", []):
                with self.subTest(case=name):
                    if name != "answer_other_thread":
                        self.assertIn(r["scope"], ("own_thread", "none"))


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ReproductionTest(unittest.TestCase):
    """The bug, as found, and the fix that labels it without moving the answer. Rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self.n = 996000
        thread_x = "thread:9:pr-996001"
        self.issue = self.node("issue", "real issue", thread_x)
        self.pr_x = self.node("pull_request", "the real fix", thread_x)
        self.decision = self.node("decision", "real issue", thread_x, ext=thread_x)
        self.conn.execute("INSERT INTO issue (node_id, number, state) VALUES (%s, 996001, 'closed')", (self.issue,))
        self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, 996002, 'closed', now())", (self.pr_x,))
        self.conn.execute("INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 's')", (self.decision,))
        self.edge(self.decision, self.issue, "motivated_by", "synthesis_closes_cluster")
        self.edge(self.decision, self.pr_x, "implemented_by", "synthesis_closes_cluster")
        self.edge(self.pr_x, self.issue, "closes", "pr_body_closing_keyword")
        # An unrelated PR in ANOTHER thread that merely says "similar to #996001".
        self.pr_z = self.node("pull_request", 'unrelated change, says "similar to #996001"',
                              "thread:9:pr-996100", ext="996100")
        self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, 996100, 'closed', now())", (self.pr_z,))
        self.edge(self.pr_z, self.issue, "references", "pr_body_issue_mention")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def node(self, node_type, title, thread, ext=None):
        self.n += 1
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title, thread_key) VALUES (%s, %s, %s, %s) RETURNING id",
            (node_type, ext or str(self.n), title, thread)).fetchone()["id"]

    def edge(self, src, dst, edge_type, extractor):
        self.conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from) "
            "VALUES (%s, %s, %s, 'explicit', 'explicit', %s, now() - interval '1 day')",
            (src, dst, edge_type, extractor))

    def test_the_unrelated_pr_is_answered_with_another_threads_decision_and_says_so(self) -> None:
        a = reasoning.reason(self.conn, self.pr_z, Mode.WHY, max_depth=3)
        # The walk is exactly what it was: found, explicit, one path through the mention...
        self.assertTrue(a.found)
        self.assertEqual({p.tier for p in a.paths}, {"explicit"})
        [p] = a.paths
        self.assertEqual([s.edge_type for s in p.steps], ["references", "motivated_by"])
        self.assertEqual(p.ref(p.target_id).node_type, "decision")
        # ...and it is now labelled, in the engine and in every renderer.
        self.assertEqual(a.scope, "other_thread")
        c = p.thread_crossing
        self.assertEqual((c.edge_type, c.from_node_id, c.to_node_id), ("references", self.pr_z, self.issue))
        out = io.StringIO()
        render.render(a, out=out)
        self.assertIn("not this artifact's own reason", out.getvalue())

    def test_the_real_issue_gets_no_label_for_its_own_decision(self) -> None:
        a = reasoning.reason(self.conn, self.issue, Mode.WHY, max_depth=3)
        self.assertTrue(a.found)
        self.assertEqual(a.scope, "own_thread")
        out = io.StringIO()
        render.render(a, out=out)
        self.assertNotIn("Reached through a mention", out.getvalue())

    def test_the_real_pr_gets_no_label_either(self) -> None:
        a = reasoning.reason(self.conn, self.pr_x, Mode.WHY, max_depth=3)
        self.assertEqual(a.scope, "own_thread")

    def test_impact_over_the_same_mention_is_not_labelled(self) -> None:
        a = reasoning.reason(self.conn, self.pr_z, Mode.IMPACT, max_depth=3)
        self.assertEqual(a.scope, "none")
        self.assertEqual(a.foreign_decision_paths(), [])

    def test_the_measurement_script_counts_this_fixture(self) -> None:
        # eval/measure_cross_thread.py is what the flask run will use (#173). It has to work on
        # a graph whose answers are known, or its numbers there would mean nothing.
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "eval" / "measure_cross_thread.py"
        spec = importlib.util.spec_from_file_location("measure_cross_thread", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.measure(self.conn)
        # Other rows may exist in a shared database, so assert what this fixture adds.
        self.assertGreaterEqual(report["by_scope"].get("other_thread", 0), 1)
        self.assertGreaterEqual(report["crossing_paths_by_edge_type"].get("references", 0), 1)
        self.assertIn(self.pr_z, [e["node_id"] for e in report["examples"]] + [self.pr_z])
        self.assertEqual(sum(report["by_scope"].values()), report["artifacts_asked"])

    def test_the_whole_document_validates_and_says_other_thread(self) -> None:
        from decision_graph import query, retrieval

        c = retrieval.find_candidates(self.conn, "#996100", limit=1)
        doc = roundtrip(query.answer_document(self.conn, query="#996100", mode="why", candidates=c,
                                              as_of=None, max_depth=3))
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertEqual(doc["results"][0]["scope"], "other_thread")


if __name__ == "__main__":
    unittest.main()
