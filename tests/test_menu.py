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

    def test_browsing_needs_no_prior_knowledge_and_comes_first(self) -> None:
        # Every other query action opens by asking you to name something. Browsing is the
        # only entry that asks nothing, so it is the one a first-time reader must land on
        # (issue #78).
        self.assertEqual(menu.ACTIONS[0].label, "Browse decisions")

    def test_no_capability_is_reachable_only_by_a_flag(self) -> None:
        # The point of #78: diagram, details, all-matches, point-in-time and mode-switching
        # were reachable only by knowing a flag existed before you ran anything.
        offered = {label for _key, label, _hint in menu.FOLLOW_UPS}
        self.assertEqual(
            offered,
            {"diagram", "details", "all matches", "as of a date", "switch mode"},
        )


class ViewTest(unittest.TestCase):
    """The follow-up toggles (issue #78).

    These map keystrokes to `dg query` flags, and a key that quietly set the wrong flag
    would answer a different question than the one on screen while looking entirely
    plausible doing it. The first implementation accumulated flags instead of toggling,
    which left "diagram" stuck on with no way back except leaving the answer.
    """

    def test_the_default_view_is_a_plain_why_answer(self) -> None:
        self.assertEqual(menu.View("why").flags(), ["--mode", "why"])

    def test_each_toggle_adds_its_flag(self) -> None:
        for key, expected in [
            ("d", ["--format", "mermaid"]),
            ("v", ["-v"]),
            ("a", ["--all"]),
        ]:
            with self.subTest(key=key):
                view = menu.View("why")
                self.assertTrue(view.toggle(key))
                self.assertEqual(view.flags(), ["--mode", "why"] + expected)

    def test_a_toggle_turns_back_off(self) -> None:
        view = menu.View("why")
        view.toggle("d")
        view.toggle("d")
        self.assertEqual(view.flags(), ["--mode", "why"])

    def test_switch_mode_flips_and_flips_back(self) -> None:
        view = menu.View("why")
        view.toggle("s")
        self.assertIn("impact", view.flags())
        view.toggle("s")
        self.assertIn("why", view.flags())

    def test_as_of_prompts_and_can_be_cleared(self) -> None:
        # Time travel you cannot leave is the toggle where being stuck is least obvious:
        # every later answer would be about a past the reader has stopped thinking about.
        view = menu.View("why")
        view.toggle("t", ask=lambda _prompt: "2025-06-01")
        self.assertIn("--as-of", view.flags())
        self.assertIn("2025-06-01T00:00:00Z", view.flags())
        view.toggle("t")
        self.assertNotIn("--as-of", view.flags())

    def test_cancelling_the_date_prompt_changes_nothing(self) -> None:
        view = menu.View("why")
        view.toggle("t", ask=lambda _prompt: "")
        self.assertEqual(view.flags(), ["--mode", "why"])

    def test_an_unknown_key_is_refused_rather_than_ignored(self) -> None:
        # The loop prints "not an option" on False; silently swallowing it would look like
        # the tool had done something.
        self.assertFalse(menu.View("why").toggle("z"))

    def test_the_bar_reports_what_is_already_on(self) -> None:
        view = menu.View("why")
        self.assertEqual(view.label("d"), "")
        view.toggle("d")
        self.assertEqual(view.label("d"), "on")
        self.assertEqual(view.label("s"), "why")

    def test_every_advertised_follow_up_is_a_real_toggle(self) -> None:
        # The bar and the handler are two lists that must not drift: a row advertised with
        # no handler behind it reads as a broken key.
        for key, _label, _hint in menu.FOLLOW_UPS:
            with self.subTest(key=key):
                self.assertTrue(menu.View("why").toggle(key, ask=lambda _p: ""))


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
