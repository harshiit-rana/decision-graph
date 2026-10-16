"""The urllib3 pre-registration is internally consistent and reproducible (#131).

No database and no network. These check the committed files against each other and against
the issue's acceptance criteria, so the registration can't quietly lose a property:

- the random arm is exactly what the recorded seed and committed pool produce;
- at least half the why-queries are random, 15 or more queries exist, at least 3 are
  paraphrases and at least 4 are expected refusals;
- every query states an expected outcome, a prediction and how a human verifies it;
- the set has the fields the evaluation runner reads;
- the predictions document quotes the numbers its inputs file computed.

What this can't check is the one property that makes it a pre-registration: that the commit
is earlier than any ingestion. That is the git history, recorded in the issue.
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "eval" / "urllib3"


def load_select():
    spec = importlib.util.spec_from_file_location("select_queries", DIR / "select_queries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUERIES = json.loads((DIR / "query_set.json").read_text(encoding="utf-8"))["queries"]
DRAW = json.loads((DIR / "draw.json").read_text(encoding="utf-8"))
INPUTS = json.loads((DIR / "predict_inputs.json").read_text(encoding="utf-8"))
PREDICTIONS = (DIR / "predictions.md").read_text(encoding="utf-8")


class RandomArmTest(unittest.TestCase):
    def test_the_draw_reproduces_from_the_committed_pool(self) -> None:
        pool = json.loads((DIR / "pool.json").read_text(encoding="utf-8"))
        self.assertEqual(load_select().draw(pool), DRAW)

    def test_the_seed_is_the_registration_date_and_was_not_tuned(self) -> None:
        self.assertEqual(DRAW["seed"], 20261017)

    def test_each_drawn_pr_is_the_source_of_one_random_query(self) -> None:
        random_queries = [q for q in QUERIES if q["arm"] == "random"]
        sources = [int(q["source"].rsplit("/", 1)[1]) for q in random_queries]
        self.assertEqual(sources, DRAW["drawn_in_order"])

    def test_query_form_alternates_by_draw_parity(self) -> None:
        # Odd draws: the linked issue's exact title. Even draws: `#<issue number>`.
        for i, q in enumerate([q for q in QUERIES if q["arm"] == "random"], start=1):
            with self.subTest(query=q["id"]):
                self.assertEqual(q["query"].startswith("#"), i % 2 == 0)


class AcceptanceCriteriaTest(unittest.TestCase):
    def test_at_least_fifteen_queries(self) -> None:
        self.assertGreaterEqual(len(QUERIES), 15)

    def test_ids_are_unique(self) -> None:
        ids = [q["id"] for q in QUERIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_at_least_half_the_why_queries_are_random(self) -> None:
        why = [q for q in QUERIES if q["category"] in ("why-random", "why-hand")]
        random_ = [q for q in why if q["arm"] == "random"]
        self.assertGreaterEqual(len(random_) * 2, len(why))

    def test_every_query_is_labelled_with_its_arm(self) -> None:
        for q in QUERIES:
            self.assertIn(q["arm"], ("random", "hand"), q["id"])

    def test_at_least_three_paraphrases(self) -> None:
        self.assertGreaterEqual(sum(q["category"] == "paraphrase" for q in QUERIES), 3)

    def test_at_least_four_expected_refusals_each_with_its_reason(self) -> None:
        refusals = [q for q in QUERIES if q["category"].startswith("refusal")
                    or q["category"] == "impact-refusal"]
        self.assertGreaterEqual(len(refusals), 4)
        for q in refusals:
            with self.subTest(query=q["id"]):
                self.assertRegex(q["expected"], r"^(Refusal|No match)", "the reason must be stated")
                self.assertTrue(q["predicted"].lower().startswith(("refusal", "no candidates")))

    def test_point_in_time_cases_exist_and_carry_an_as_of(self) -> None:
        temporal = [q for q in QUERIES if q["category"] == "temporal"]
        self.assertGreaterEqual(len(temporal), 2)
        self.assertTrue(all(q.get("as_of") for q in temporal))

    def test_every_query_has_expected_predicted_and_verify_written_in_advance(self) -> None:
        for q in QUERIES:
            for key in ("expected", "predicted", "verify", "intent", "source"):
                with self.subTest(query=q["id"], field=key):
                    self.assertTrue(q.get(key, "").strip(), f"{q['id']} lacks {key}")

    def test_the_runner_can_read_every_query(self) -> None:
        # The fields evaluation.run_query reads. Schema only: no database.
        for q in QUERIES:
            for key in ("id", "category", "mode", "query", "intent"):
                self.assertIn(key, q)
            self.assertIn(q["mode"], ("why", "impact"))
            self.assertGreaterEqual(q.get("depth", 3), 1)

    def test_contract_phrases_are_ones_the_runner_understands(self) -> None:
        for q in QUERIES:
            if "contract" in q:
                with self.subTest(query=q["id"]):
                    text = q["contract"].lower()
                    self.assertTrue(any(p in text for p in ("no answer", "no candidates", "same paths",
                                                            "fewer paths")), q["contract"])


class PredictionsTest(unittest.TestCase):
    def test_the_four_required_predictions_are_present_and_quote_the_inputs(self) -> None:
        for needle in (
            f"**{INPUTS['decision_threads']}**",
            f"{INPUTS['decisions_published']} explicit",
            f"{INPUTS['decisions_with_corroboration']} of {INPUTS['decision_threads']}",
            f"**{INPUTS['codeowners']['owns_edges']}**",
            INPUTS["codeowners"]["sha"],
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, PREDICTIONS)

    def test_no_decision_pr_is_predicted_to_be_bot_authored(self) -> None:
        self.assertEqual(INPUTS["decision_prs_authored_by_bots"], [])

    def test_the_document_states_it_is_never_edited(self) -> None:
        self.assertRegex(PREDICTIONS, r"never edited")
        self.assertIn("## Annotations", PREDICTIONS)

    def test_every_issue_number_a_query_names_is_in_the_inputs_or_the_pool(self) -> None:
        # A typo'd `#3734` would silently register a different query than intended.
        pool = json.loads((DIR / "pool.json").read_text(encoding="utf-8"))
        known = {p["number"] for p in pool["prs"]}
        for d in INPUTS["decisions"]:
            known.update(d["prs"])
            known.update(d["issues"])
        for q in QUERIES:
            m = re.fullmatch(r"#(\d+)", q["query"])
            if m and q["category"] in ("why-random", "why-hand", "retrieval-identifier", "temporal"):
                with self.subTest(query=q["id"]):
                    self.assertIn(int(m.group(1)), known)


if __name__ == "__main__":
    unittest.main()
