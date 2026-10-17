"""The scorer computes figures from committed labels, and only from them (issue #128).

What's pinned:
- Wilson intervals at the edges (0/n, n/n, n=0) and against a published reference value;
- the always-refuse baseline is printed beside the overall figure, never omitted;
- flask's committed labels reproduce 18/18 and the baseline 8/18, and agree with results.json;
- disagreements between judges are listed, not averaged away;
- the scorer refuses a label with no verdict (it never assigns one) and a label that contradicts
  what the runner recorded the engine returning.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from decision_graph import evaluation, scoring
from decision_graph.scoring import Label, LabelError, LabelSet

ROOT = Path(__file__).resolve().parents[1]


def label(item, verdict, judge="j", **kw) -> Label:
    return Label(item=item, verdict=verdict, judge=judge, **kw)


def write_dir(tmp: str, judges: dict[str, list[dict]], results=None, queries=None) -> Path:
    d = Path(tmp)
    (d / "labels").mkdir()
    for judge, labels in judges.items():
        (d / "labels" / f"{judge}.json").write_text(json.dumps(
            {"format": 1, "repo": "o/r", "judge": judge, "judge_role": "test", "labels": labels}),
            encoding="utf-8")
    if results is not None:
        (d / "results.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    if queries is not None:
        (d / "query_set.json").write_text(json.dumps({"queries": [{"id": q} for q in queries]}), encoding="utf-8")
    return d


class WilsonTest(unittest.TestCase):
    def test_no_items_means_nothing_is_known(self) -> None:
        self.assertEqual(scoring.wilson(0, 0), (0.0, 1.0))
        self.assertEqual(scoring.fmt(0, 0), "0/0 (no items)")

    def test_n_of_n_is_a_range_not_a_certainty(self) -> None:
        lo, hi = scoring.wilson(18, 18)
        self.assertAlmostEqual(lo, 0.824, places=3)
        self.assertEqual(hi, 1.0)

    def test_zero_of_n_has_an_upper_bound_above_zero(self) -> None:
        lo, hi = scoring.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775, places=3)

    def test_the_bounds_satisfy_the_defining_equation(self) -> None:
        # The Wilson bounds are the p at which (p_hat - p)^2 = z^2 * p(1-p) / n, derived
        # independently of the closed form the module uses.
        for k, n in ((81, 100), (3, 7), (18, 18), (1, 50)):
            for bound in scoring.wilson(k, n):
                if bound in (0.0, 1.0):
                    continue
                with self.subTest(k=k, n=n, bound=bound):
                    self.assertAlmostEqual((k / n - bound) ** 2,
                                           scoring.Z95 ** 2 * bound * (1 - bound) / n, places=9)

    def test_the_interval_always_contains_the_estimate_and_stays_in_range(self) -> None:
        for n in range(1, 40):
            for k in range(n + 1):
                lo, hi = scoring.wilson(k, n)
                with self.subTest(k=k, n=n):
                    self.assertLessEqual(0.0, lo)
                    self.assertLessEqual(lo, k / n + 1e-12)
                    self.assertGreaterEqual(hi, k / n - 1e-12)
                    self.assertLessEqual(hi, 1.0)


class FiguresTest(unittest.TestCase):
    def test_counts_and_ratios(self) -> None:
        f = scoring.figures([
            label("a", "correct", tier="explicit"), label("b", "incorrect", tier="explicit"),
            label("c", "correct-refusal"), label("d", "missed"),
        ])
        self.assertEqual(f["counts"], {"correct": 1, "incorrect": 1, "correct-refusal": 1, "missed": 1})
        self.assertEqual(f["overall"], (2, 4))
        self.assertEqual(f["precision_answered"], (1, 2))
        self.assertEqual(f["abstention_precision"], (1, 2))
        self.assertEqual(f["by_tier"], {"explicit": (1, 2)})

    def test_the_always_refuse_baseline_is_the_items_whose_right_outcome_was_nothing(self) -> None:
        f = scoring.figures([
            label("a", "correct"), label("b", "correct-refusal"), label("c", "correct-refusal"),
            label("d", "incorrect", truth="should_have_refused"),
            label("e", "incorrect"), label("f", "missed"),
        ])
        self.assertEqual(f["always_refuse_baseline"], (3, 6))

    def test_an_answer_that_should_have_been_a_refusal_lowers_abstention_recall(self) -> None:
        f = scoring.figures([label("a", "correct-refusal"), label("b", "incorrect", truth="should_have_refused")])
        self.assertEqual(f["abstention_recall"], (1, 2))

    def test_a_wrong_answer_is_not_an_abstention_failure(self) -> None:
        f = scoring.figures([label("a", "correct-refusal"), label("b", "incorrect", truth="wrong_answer")])
        self.assertEqual(f["abstention_recall"], (1, 1))

    def test_contested_labels_are_counted_wrong_in_the_sensitivity_figure(self) -> None:
        f = scoring.figures([label("a", "correct"), label("b", "correct-refusal", contested=True)])
        self.assertEqual(f["overall"], (2, 2))
        self.assertEqual(f["if_contested_were_wrong"], (1, 2))
        self.assertEqual(f["contested"], ["b"])

    def test_the_markdown_prints_the_baseline_next_to_the_overall_figure(self) -> None:
        text = scoring.render_markdown([LabelSet("o/r", "j", "r", [label("a", "correct"), label("b", "correct-refusal")])])
        lines = text.splitlines()
        overall = next(i for i, ln in enumerate(lines) if "correct outcome, overall" in ln)
        self.assertIn("always-refuse baseline", lines[overall + 1])


class AgreementTest(unittest.TestCase):
    def test_disagreements_are_listed_with_both_notes(self) -> None:
        a = LabelSet("o/r", "x", "", [label("1", "correct", "x", note="fine"), label("2", "correct", "x")])
        b = LabelSet("o/r", "y", "", [label("1", "incorrect", "y", note="wrong parent"), label("2", "correct", "y"),
                                      label("3", "missed", "y")])
        ag = scoring.agreement(a, b)
        self.assertEqual((ag["both"], ag["same"]), (2, 1))
        self.assertEqual(ag["disagreements"], [("1", "correct", "incorrect", "fine", "wrong parent")])
        self.assertEqual(ag["only_second"], ["3"])
        text = scoring.render_markdown([a, b])
        self.assertIn("| 1 | correct | incorrect | fine / wrong parent |", text)
        self.assertIn("unstable: n under 30", text)

    def test_identical_labels_give_kappa_one_unless_chance_agreement_is_total(self) -> None:
        rows = lambda j: [label("1", "correct", j), label("2", "correct-refusal", j)]
        ag = scoring.agreement(LabelSet("r", "x", "", rows("x")), LabelSet("r", "y", "", rows("y")))
        self.assertEqual(ag["kappa"], 1.0)
        same = lambda j: [label("1", "correct", j), label("2", "correct", j)]
        self.assertIsNone(scoring.agreement(LabelSet("r", "x", "", same("x")), LabelSet("r", "y", "", same("y")))["kappa"])


class LoadingTest(unittest.TestCase):
    def load(self, labels, **top):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text(json.dumps({"format": 1, "judge": "j", "labels": labels, **top}), encoding="utf-8")
            return scoring.load_labels(p)

    def test_a_label_without_a_verdict_is_refused_because_the_scorer_never_assigns_one(self) -> None:
        for bad in ({"item": "a"}, {"item": "a", "verdict": None}):
            with self.subTest(bad=bad), self.assertRaisesRegex(LabelError, "never assigns"):
                self.load([bad])

    def test_unknown_values_are_refused(self) -> None:
        for bad, word in (({"item": "a", "verdict": "ok"}, "verdict"),
                          ({"item": "a", "verdict": "correct", "truth": "x"}, "truth"),
                          ({"item": "a", "verdict": "correct", "basis": "x"}, "basis")):
            with self.subTest(word=word), self.assertRaisesRegex(LabelError, word):
                self.load([bad])

    def test_the_same_item_twice_by_one_judge_is_refused(self) -> None:
        with self.assertRaisesRegex(LabelError, "twice"):
            self.load([{"item": "a", "verdict": "correct"}, {"item": "a", "verdict": "missed"}])

    def test_a_judge_must_be_named_and_the_format_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text(json.dumps({"format": 1, "labels": []}), encoding="utf-8")
            with self.assertRaisesRegex(LabelError, "no judge"):
                scoring.load_labels(p)
            p.write_text(json.dumps({"format": 2, "judge": "j", "labels": []}), encoding="utf-8")
            with self.assertRaisesRegex(LabelError, "format"):
                scoring.load_labels(p)

    def test_a_directory_without_labels_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(LabelError, "no label files"):
            scoring.load_dir(Path(tmp))


class CliTest(unittest.TestCase):
    def run_score(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = evaluation.main(["score", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_label_that_contradicts_the_recorded_result_stops_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = write_dir(tmp, {"j": [{"item": "a", "verdict": "correct"}]},
                          results=[{"id": "a", "answered": False}])
            code, out, err = self.run_score(str(d))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("a is labelled correct, but the engine returned nothing", err)

    def test_coverage_names_the_queries_nobody_labelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = write_dir(tmp, {"j": [{"item": "a", "verdict": "correct"}]}, queries=["a", "b"])
            code, out, _ = self.run_score(str(d))
        self.assertEqual(code, 0)
        self.assertIn("**unlabelled: b**", out)

    def test_a_missing_directory_is_an_error_not_a_traceback(self) -> None:
        code, _, err = self.run_score(str(ROOT / "eval" / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("no label files", err)

    def test_score_needs_no_database(self) -> None:
        import os

        saved = os.environ.pop("DATABASE_URL", None)
        try:
            code, _, _ = self.run_score(str(ROOT / "eval"))
        finally:
            if saved is not None:
                os.environ["DATABASE_URL"] = saved
        self.assertEqual(code, 0)


class FlaskReproductionTest(unittest.TestCase):
    """RESULTS.md's headline, recomputed from the transcribed labels instead of typed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sets = scoring.load_dir(ROOT / "eval")
        cls.f = scoring.figures(cls.sets[0].labels)

    def test_eighteen_of_eighteen_with_its_interval(self) -> None:
        self.assertEqual(self.f["overall"], (18, 18))
        self.assertIn("18/18 = 100.0% (95% CI 82.4% to 100.0%)", scoring.fmt(*self.f["overall"]))

    def test_the_always_refuse_baseline_is_eight_of_eighteen(self) -> None:
        self.assertEqual(self.f["always_refuse_baseline"], (8, 18))

    def test_ten_answered_and_eight_correctly_refused(self) -> None:
        self.assertEqual((self.f["counts"]["correct"], self.f["counts"]["correct-refusal"]), (10, 8))

    def test_eleven_checked_directly_as_results_md_says(self) -> None:
        self.assertEqual(self.f["by_basis"]["checked_directly"], (11, 11))
        direct = {lb.item for lb in self.sets[0].labels if lb.basis == "checked_directly"}
        self.assertEqual(direct, {"F1", "F2", "H1", "H2", "H3", "H4", "H7", "H8", "H9", "H10", "N1"})

    def test_labels_agree_with_the_runners_record_and_cover_the_query_set(self) -> None:
        self.assertEqual(scoring.check_against_results(self.sets, ROOT / "eval" / "results.json"), [])
        ids = scoring.query_ids_of(ROOT / "eval")
        self.assertEqual({lb.item for lb in self.sets[0].labels}, set(ids))

    def test_h6_is_flagged_as_the_judges_own_doubt(self) -> None:
        self.assertEqual(self.f["contested"], ["H6"])
        self.assertEqual(self.f["if_contested_were_wrong"], (17, 18))

    def test_results_md_figures_match_the_scorer(self) -> None:
        text = (ROOT / "eval" / "RESULTS.md").read_text(encoding="utf-8")
        self.assertIn("**18 of 18 correct.", text)
        self.assertIn("would score 8 of 18", text)
        self.assertIn("python -m decision_graph.evaluation score eval", text)


if __name__ == "__main__":
    unittest.main()
