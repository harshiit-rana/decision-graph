"""An opt-in bound on how many paths an answer carries (issue #130).

Measured on 2026-10-17 on SYNTHETIC graphs in a scratch database (not flask, not a claim about any
real repository): an impact walk from a hub issue referenced by N pull requests, each with 3
commits, returns about 4N paths, and a tangled graph more. N=1000 gave 4,000 paths in 2.4 s at
depth 3; a tangled 400-PR graph gave 23,200 paths. flask's largest answer is 17. So the risk is a
busy repository, or an agent looping over one, not flask.

The cap bounds what an answer CARRIES: its text, its JSON, an agent's context. It does not bound
the database's work, which the depth limit does. It is opt-in, because the right default is the
owner's call (#130), so nothing changes unless `--max-paths` is passed. What's checked:

- the paths kept are the first N in the walk's own order, and the rest are counted, never dropped;
- below the cap, and with no cap, an answer is exactly what it was;
- every renderer says so: text, JSON, Mermaid, DOT and the `dg ask` prompt;
- the CLI refuses a cap that would make every answer look like a refusal.
"""

from __future__ import annotations

import io
import json
import os
import unittest

from decision_graph import contract, diagram, explain, reasoning, render
from decision_graph.reasoning import Answer, Mode, NodeRef, Path, Step

from tests.test_contract import SCHEMA, doc_for, roundtrip, validate

DSN = os.environ.get("DATABASE_URL")


def three_path_answer(max_paths=None, omitted=0) -> Answer:
    refs = {i: NodeRef("pull_request" if i else "issue", f"title {i}", str(i), f"https://x/{i}", None)
            for i in range(4)}
    paths = [Path([0, i], [Step(10 + i, "closes", "explicit", "explicit", i, 0, "x", None)],
                  {0: refs[0], i: refs[i]}) for i in (1, 2, 3)][: 3 - omitted]
    return Answer(Mode.IMPACT, 0, paths, False, "paths", max_paths=max_paths, paths_omitted=omitted)


class BoundedTest(unittest.TestCase):
    def paths(self, n):
        return [Path([0, i], [], {}) for i in range(n)]

    def test_no_cap_keeps_everything(self) -> None:
        p = self.paths(5)
        self.assertEqual(reasoning._bounded(p, None), (p, 0))

    def test_a_cap_at_or_above_the_count_changes_nothing(self) -> None:
        p = self.paths(5)
        for cap in (5, 6, 1000):
            with self.subTest(cap=cap):
                self.assertEqual(reasoning._bounded(p, cap), (p, 0))

    def test_a_cap_below_the_count_keeps_the_first_n_and_counts_the_rest(self) -> None:
        p = self.paths(5)
        kept, omitted = reasoning._bounded(p, 2)
        self.assertEqual(kept, p[:2])
        self.assertEqual(omitted, 3)


class EveryRendererTest(unittest.TestCase):
    """One test per renderer, on the same truncated answer: 2 of 3 paths kept."""

    def answer(self) -> Answer:
        return three_path_answer(max_paths=2, omitted=1)

    def test_text_says_how_many_were_not_shown_and_that_nothing_was_ranked(self) -> None:
        out = io.StringIO()
        render.render(self.answer(), out=out)
        text = out.getvalue()
        self.assertIn("2 paths shown of 3", text)
        self.assertIn("1 more path not shown", text)
        self.assertIn("capped at 2 (--max-paths)", text)
        self.assertIn("Nothing was filtered or ranked", text)

    def test_json_counts_the_omitted_paths(self) -> None:
        from decision_graph import retrieval

        doc = roundtrip(doc_for(retrieval.Candidate(0, "issue", "0", "title 0", "identifier", 1.0),
                                self.answer()))
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertEqual(doc["results"][0]["truncated"],
                         {"max_paths": 2, "paths_omitted": 1, "paths_returned": 2})

    def test_mermaid_says_so(self) -> None:
        self.assertIn("%% 1 more path(s) not drawn (capped at 2)", diagram.to_mermaid(self.answer()))

    def test_dot_says_so(self) -> None:
        self.assertIn("// 1 more path(s) not drawn (capped at 2)", diagram.to_dot(self.answer()))

    def test_dg_ask_tells_the_model_the_answer_is_incomplete(self) -> None:
        self.assertEqual(explain.answer_payload("why?", self.answer())["paths_omitted"], 1)
        self.assertIn("paths_omitted", explain.SUMMARY_SYSTEM)
        self.assertIn("do\n  not describe the answer as complete", explain.SUMMARY_SYSTEM)


class NoNoticeWhenNotTruncatedTest(unittest.TestCase):
    def test_an_uncapped_answer_is_exactly_what_it_was(self) -> None:
        a = three_path_answer()
        out = io.StringIO()
        render.render(a, out=out)
        self.assertNotIn("not shown", out.getvalue())
        self.assertIn("(3 paths)", out.getvalue())
        self.assertNotIn("not drawn", diagram.to_mermaid(a))
        self.assertNotIn("not drawn", diagram.to_dot(a))
        self.assertEqual(explain.answer_payload("why?", a)["paths_omitted"], 0)

    def test_a_cap_that_did_not_bite_adds_no_notice_even_though_a_cap_was_set(self) -> None:
        a = three_path_answer(max_paths=10, omitted=0)
        out = io.StringIO()
        render.render(a, out=out)
        self.assertNotIn("not shown", out.getvalue())
        from decision_graph import retrieval

        doc = roundtrip(doc_for(retrieval.Candidate(0, "issue", "0", "title 0", "identifier", 1.0), a))
        self.assertIsNone(doc["results"][0]["truncated"])

    def test_the_goldens_have_no_truncation(self) -> None:
        from tests.test_contract import CASES

        for name, build in CASES.items():
            for r in build().get("results", []):
                with self.subTest(case=name):
                    self.assertIsNone(r["truncated"])


class CliTest(unittest.TestCase):
    def run_query(self, argv):
        from contextlib import redirect_stderr, redirect_stdout

        from decision_graph import query

        out, err = io.StringIO(), io.StringIO()
        saved = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://unused"
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = query.main(argv)
                except SystemExit as exc:
                    code = exc.code
        finally:
            if saved is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = saved
        return code, out.getvalue(), err.getvalue()

    def test_a_cap_of_zero_is_refused_as_it_would_look_like_a_refusal(self) -> None:
        code, out, err = self.run_query(["#1", "--max-paths", "0", "--format", "json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["kind"], "error")
        self.assertIn("every answer would", err)

    def test_the_flag_is_documented_with_the_flask_figure(self) -> None:
        _, out, _ = self.run_query(["--help"])
        self.assertIn("--max-paths", out)
        self.assertIn("17", out)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class HubTest(unittest.TestCase):
    """A synthetic hub: one issue closed by 50 PRs, each implemented by 3 commits. Rolls back."""

    N, K = 50, 3

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        mk = lambda t, e: self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, 'zz hub') RETURNING id",
            (t, e)).fetchone()["id"]
        edge = lambda a, b, t: self.conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from) "
            "VALUES (%s, %s, %s, 'explicit', 'explicit', 'x', now() - interval '1 day')", (a, b, t))
        self.hub = mk("issue", "zzhub")
        for i in range(self.N):
            pr = mk("pull_request", f"zzp{i}")
            edge(pr, self.hub, "closes")
            for j in range(self.K):
                edge(pr, mk("commit", f"zzc{i}_{j}"), "implements")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def impact(self, **kw):
        return reasoning.reason(self.conn, self.hub, Mode.IMPACT, max_depth=3, **kw)

    def test_the_hub_returns_n_times_k_plus_one_paths(self) -> None:
        self.assertEqual(len(self.impact().paths), self.N * (self.K + 1))

    def test_a_cap_keeps_the_first_paths_in_the_walks_own_order_and_counts_the_rest(self) -> None:
        full, capped = self.impact(), self.impact(max_paths=25)
        self.assertEqual(len(capped.paths), 25)
        self.assertEqual(capped.paths_omitted, len(full.paths) - 25)
        self.assertEqual([p.node_ids for p in capped.paths], [p.node_ids for p in full.paths[:25]])

    def test_below_the_cap_the_answer_is_identical_to_the_uncapped_one(self) -> None:
        full, roomy = self.impact(), self.impact(max_paths=10_000)
        self.assertEqual([(p.node_ids, [s.edge_id for s in p.steps]) for p in roomy.paths],
                         [(p.node_ids, [s.edge_id for s in p.steps]) for p in full.paths])
        self.assertEqual(roomy.paths_omitted, 0)

    def test_the_traversal_measurement_script_runs_and_sees_the_hub(self) -> None:
        # eval/measure_traversal.py is what the flask run will use (#173).
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "eval" / "measure_traversal.py"
        spec = importlib.util.spec_from_file_location("measure_traversal", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.measure(self.conn, depths=(2,))
        self.assertGreaterEqual(report["by_depth"]["2"]["paths"]["max"], self.N * (self.K + 1))
        self.assertEqual(len(report["by_depth"]["2"]["worst_five"]), 5)
        self.assertGreater(report["database"]["edges"], 0)

    def test_the_capped_document_validates_and_says_how_much_is_missing(self) -> None:
        from decision_graph import query, retrieval

        c = retrieval.find_candidates(self.conn, "zzhub", limit=1)
        doc = roundtrip(query.answer_document(self.conn, query="zzhub", mode="impact", candidates=c,
                                              as_of=None, max_depth=3, max_paths=10))
        self.assertEqual(validate(doc, SCHEMA), [])
        t = doc["results"][0]["truncated"]
        self.assertEqual((t["max_paths"], t["paths_returned"], t["paths_omitted"]),
                         (10, 10, self.N * (self.K + 1) - 10))
        self.assertEqual(doc["provenance"]["request"]["max_paths"], 10)


if __name__ == "__main__":
    unittest.main()
