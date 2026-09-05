"""Bad input must produce a sentence, never a traceback and never a false answer (#82).

Found by executing every command and flag rather than by reading the code, which is also
why none of it was covered: each of these is a path a caller reaches by mistyping, and
mistyping is not something a test thinks to do on your behalf.

The one that matters most is `--depth 0`. It did not crash — it printed a refusal saying
the graph holds no evidence, and exited 0. A refusal that reports the caller's own mistake
as a fact about the repository is worse than an error, because it is believable, and it is
the same failure as the point-in-time refusal fixed in #77.

Standalone: these drive `main()` with argv and capture output. Only the paths that reach a
database are skipped without one.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from decision_graph import evaluation, query

DSN = os.environ.get("DATABASE_URL")


def run_query(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = query.main(argv)
    return code, out.getvalue() + err.getvalue()


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class QueryArgumentTest(unittest.TestCase):
    def test_a_non_timestamp_as_of_is_reported_not_raised(self) -> None:
        # `datetime.fromisoformat` raised straight out of the standard library, past every
        # error path this CLI has.
        code, text = run_query(["#5898", "--as-of", "nonsense"])
        self.assertEqual(code, 2)
        self.assertIn("not a timestamp", text)

    def test_a_bare_date_is_still_accepted(self) -> None:
        # The fix must not narrow what works: YYYY-MM-DD is what the error message tells
        # people to use, so it had better parse.
        code, _text = run_query(["#5898", "--as-of", "2026-03-01"])
        self.assertEqual(code, 0)

    def test_depth_zero_is_refused_rather_than_answered_with_nothing(self) -> None:
        code, text = run_query(["#5898", "--depth", "0"])
        self.assertEqual(code, 2)
        self.assertIn("cannot reach anything", text)
        self.assertNotIn("No answer", text, "a caller's mistake was stated as a finding")

    def test_negative_depth_is_refused(self) -> None:
        code, _text = run_query(["#5898", "--depth", "-1"])
        self.assertEqual(code, 2)

    def test_depth_one_still_works(self) -> None:
        code, _text = run_query(["#5898", "--depth", "1"])
        self.assertEqual(code, 0)


class AskArgumentTest(unittest.TestCase):
    def test_an_empty_question_never_reaches_the_provider(self) -> None:
        # It used to get as far as the API layer, spending a real request asking a model to
        # extract a search term from an empty string.
        import argparse

        from decision_graph import cli

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.cmd_ask(argparse.Namespace(question="   "), [])
        self.assertEqual(code, 2)
        self.assertIn("no question", err.getvalue())


class EvaluationArgumentTest(unittest.TestCase):
    """`--help` used to run the entire evaluation and overwrite the committed record."""

    def test_help_exits_without_running_anything(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(io.StringIO()):
                evaluation.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_an_unknown_flag_is_an_error_not_a_full_run(self) -> None:
        # The dangerous shape: any argument at all was ignored, so a typo ran the
        # evaluation and wrote over eval/results.json.
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                evaluation.main(["--not-a-flag"])
        self.assertEqual(caught.exception.code, 2)

    def test_the_output_path_can_be_pointed_elsewhere(self) -> None:
        # So the committed record is not the only place a run can land.
        flags = {o for a in evaluation.build_parser()._actions for o in a.option_strings}
        self.assertIn("--output", flags)
        self.assertIn("--query-set", flags)

    def test_a_missing_query_set_is_reported_rather_than_traced(self) -> None:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = evaluation.main(["--query-set", "no/such/file.json"])
        self.assertEqual(code, 2)
        self.assertIn("no query set", err.getvalue())


if __name__ == "__main__":
    unittest.main()
