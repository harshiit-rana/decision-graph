"""The interactive menu, exercised without a database or a terminal.

These are unit checks over the parts that do not touch Postgres: the banner stays ASCII
(the wrappers taught us what a stray non-ASCII byte costs), the action table is well-formed,
and the loop actually exits on `q` and on end-of-input rather than spinning. The DB-backed
status block and the real query dispatch are covered live, not here.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from decision_graph import menu


class BannerTest(unittest.TestCase):
    def test_banner_is_pure_ascii(self) -> None:
        for i, line in enumerate(menu.BANNER):
            offenders = [(c, ord(c)) for c in line if ord(c) > 0x7F]
            self.assertEqual(offenders, [], f"banner line {i} has non-ASCII: {offenders}")


class ActionTableTest(unittest.TestCase):
    def test_keys_are_unique(self) -> None:
        keys = [a.key for a in menu.ACTIONS]
        self.assertEqual(len(keys), len(set(keys)), f"duplicate menu keys: {keys}")

    def test_every_action_is_callable(self) -> None:
        for a in menu.ACTIONS:
            self.assertTrue(callable(a.run), f"action {a.key} is not runnable")

    def test_query_actions_are_present(self) -> None:
        # The two questions the tool answers must both have a door.
        labels = {a.label for a in menu.ACTIONS}
        self.assertIn("Ask why", labels)
        self.assertIn("Trace impact", labels)


class LoopTest(unittest.TestCase):
    def _run_with_input(self, lines: list[str]) -> int:
        # Draw is stubbed so the loop never touches the database; input is scripted.
        with mock.patch.object(menu, "_draw", lambda: None), mock.patch(
            "builtins.input", side_effect=lines
        ), redirect_stdout(io.StringIO()):
            return menu.run(argparse.Namespace())

    def test_q_quits(self) -> None:
        self.assertEqual(self._run_with_input(["q"]), 0)

    def test_eof_quits(self) -> None:
        # A closed stdin must end the loop, not raise -- otherwise a piped invocation spins.
        with mock.patch.object(menu, "_draw", lambda: None), mock.patch(
            "builtins.input", side_effect=EOFError
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(menu.run(argparse.Namespace()), 0)

    def test_unknown_choice_redraws_without_running_anything(self) -> None:
        # "zzz" matches no action, then "q" exits. It must not dispatch or crash.
        self.assertEqual(self._run_with_input(["zzz", "q"]), 0)

    def test_a_failing_action_does_not_kill_the_loop(self) -> None:
        boom = menu.Action("9", "Boom", "raises", lambda ns: (_ for _ in ()).throw(RuntimeError("boom")))
        with mock.patch.object(menu, "ACTIONS", [boom]), mock.patch.object(
            menu, "_draw", lambda: None
        ), mock.patch("builtins.input", side_effect=["9", "", "q"]), redirect_stdout(io.StringIO()):
            # rebuild the key map inside run() picks up the patched ACTIONS
            self.assertEqual(menu.run(argparse.Namespace()), 0)


if __name__ == "__main__":
    unittest.main()
