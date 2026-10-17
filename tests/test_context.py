"""Context pack: `dg context` (issue #138).

What's guarded:
- The pack validates against the published schema, and the committed goldens (one
  answered, one refused) don't change by accident.
- `gaps` names every mechanical gap, using the same refusal vocabulary as the text and JSON
  refusals.
- The budget cuts honestly: everything removed is counted, and gaps are read before
  anything is cut.
- The pack's Why answer is the same document `dg query --format json` returns, checked
  end to end against a seeded database.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from decision_graph import attempts, context, contract, query, retrieval
from decision_graph.reasoning import Mode

from tests.test_attempts import _attempt
from tests.test_contract import (
    ASSOC, GOLDEN, SCHEMA, finished, T, candidate, closure, explicit_answer, explicit_facts, inferred_answer,
    refused, roundtrip, validate,
)

DSN = os.environ.get("DATABASE_URL")


def answered(**budget) -> dict:
    c = candidate()
    why = contract.result_doc(c, explicit_answer(), explicit_facts())
    impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
    attempt_docs = attempts.document("q", [_attempt()])["attempts"]
    doc = context.assemble("#5895", why, impact, attempt_docs, **budget)
    return finished(doc, ASSOC)


def refused_pack() -> dict:
    c = candidate()
    why = contract.result_doc(c, refused(), contract.Facts(), closure=closure("not_planned"))
    impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
    return finished(context.assemble("#5900", why, impact, []), ASSOC)


def codes(doc: dict) -> list[str]:
    return [g["code"] for g in doc["gaps"]]


class ShapeTest(unittest.TestCase):
    def test_both_examples_validate(self) -> None:
        for build in (answered, refused_pack):
            with self.subTest(case=build.__name__):
                self.assertEqual(validate(roundtrip(build()), SCHEMA), [])

    def test_goldens(self) -> None:
        regen = os.environ.get("DG_REGEN_GOLDEN") == "1"
        for name, build in (("context_answered", answered), ("context_refused", refused_pack)):
            with self.subTest(case=name):
                text = contract.dumps(build()) + "\n"
                path = GOLDEN / f"{name}.json"
                if regen:
                    path.write_text(text, encoding="utf-8", newline="\n")
                self.assertEqual(path.read_text(encoding="utf-8"), text)


class GapsTest(unittest.TestCase):
    def test_answered_pack_names_only_what_is_missing(self) -> None:
        doc = answered()
        self.assertEqual(codes(doc), ["impact_refused"])
        self.assertEqual(doc["gaps"][0]["refusal_kind"], "nothing_downstream")

    def test_refusals_carry_the_refusal_vocabulary(self) -> None:
        doc = refused_pack()
        self.assertEqual(codes(doc), ["why_refused", "impact_refused", "no_prior_attempt_found"])
        self.assertEqual(doc["gaps"][0]["refusal_kind"], "closed_not_planned")
        self.assertEqual(doc["gaps"][0]["refusal_kind"], doc["why"]["refusal"]["kind"])

    def test_inferred_fallback_is_a_gap(self) -> None:
        c = candidate(641)
        why = contract.result_doc(c, inferred_answer(), contract.Facts())
        impact = contract.result_doc(c, inferred_answer(), contract.Facts())
        self.assertIn("inferred_fallback_used", codes(context.assemble("q", why, impact, [])))

    def test_a_fuzzy_start_is_a_gap(self) -> None:
        c = candidate(match="fuzzy")
        why = contract.result_doc(c, explicit_answer(), explicit_facts())
        impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
        self.assertIn("match_not_exact", codes(context.assemble("q", why, impact, [])))

    def test_unreviewed_work_is_a_gap(self) -> None:
        facts = explicit_facts()
        facts.reviewers = {}
        c = candidate()
        why = contract.result_doc(c, explicit_answer(), facts)
        impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
        self.assertIn("no_review_recorded", codes(context.assemble("q", why, impact, [])))

    def test_every_gap_code_is_in_the_schema(self) -> None:
        self.assertEqual(set(context.GAPS), set(SCHEMA["$defs"]["gap"]["properties"]["code"]["enum"]))


class BudgetTest(unittest.TestCase):
    def test_no_budget_cuts_nothing(self) -> None:
        doc = answered()
        self.assertIsNone(doc["truncated"])
        self.assertEqual(doc["budget"], {"max_paths": None, "max_quote_chars": None})

    def test_quote_cut_is_counted(self) -> None:
        full = answered()["why"]["context"]["excerpt"]
        doc = answered(max_quote_chars=10)
        self.assertTrue(doc["why"]["context"]["excerpt"].endswith("..."))
        self.assertGreaterEqual(doc["truncated"]["quote_chars_cut"], len(full) - 10)
        self.assertEqual(validate(roundtrip(doc), SCHEMA), [])

    def test_path_cut_is_counted_and_gaps_survive_it(self) -> None:
        c = candidate()
        two = explicit_answer()
        two.paths.append(two.paths[0])
        why = contract.result_doc(c, two, explicit_facts())
        impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
        doc = context.assemble("q", why, impact, [], max_paths=1)
        self.assertEqual(len(doc["why"]["paths"]), 1)
        self.assertEqual(doc["truncated"]["paths_omitted"], 1)
        self.assertIn("impact_refused", codes(doc))


def run(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = context.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_bad_budget_is_a_json_error(self) -> None:
        code, out = run(["#1", "--max-paths", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["kind"], "error")

    def test_usage_error_is_json(self) -> None:
        code, out = run(["#1", "--depth", "deep"])
        self.assertEqual(code, 2)
        self.assertEqual(validate(json.loads(out), SCHEMA), [])

    def test_the_dg_command_is_registered(self) -> None:
        from decision_graph import cli

        args, extra = cli.build_parser().parse_known_args(["context", "#1", "--max-paths", "2"])
        self.assertEqual(args.command, "context")
        self.assertEqual(extra, ["--max-paths", "2"])


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class EndToEndTest(unittest.TestCase):
    """The pack's Why is exactly what `dg query --format json` returns. Each test rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def test_why_matches_dg_query(self) -> None:
        ids = {}
        for kind, ext, title in (("issue", "990901", "zz context fixture issue"),
                                 ("decision", "ctx-decision", "zz context fixture issue"),
                                 ("pull_request", "990902", "zz context fixture pr")):
            ids[kind] = self.conn.execute(
                "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) RETURNING id",
                (kind, ext, title),
            ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 'f')",
            (ids["decision"],),
        )
        self.conn.execute(
            "INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, 990902, 'closed', %s)",
            (ids["pull_request"], T),
        )
        for src, dst, et in ((ids["decision"], ids["issue"], "motivated_by"),
                             (ids["decision"], ids["pull_request"], "implemented_by")):
            self.conn.execute(
                "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, "
                "valid_from) VALUES (%s, %s, %s, 'explicit', 'explicit', 'test_fixture', now() - interval '1 day')",
                (src, dst, et),
            )

        pack = roundtrip(context.build(self.conn, "#990901"))
        self.assertEqual(validate(pack, SCHEMA), [])
        c = retrieval.find_candidates(self.conn, "#990901", limit=1)
        direct = roundtrip(query.answer_document(
            self.conn, query="#990901", mode="why", candidates=c, as_of=None, max_depth=3,
        ))["results"][0]
        self.assertEqual(pack["why"], direct)
        self.assertTrue(pack["why"]["found"])

    def test_no_match_is_a_no_match_document(self) -> None:
        doc = roundtrip(context.build(self.conn, "#999999999"))
        self.assertEqual(doc["kind"], "no_match")


if __name__ == "__main__":
    unittest.main()
