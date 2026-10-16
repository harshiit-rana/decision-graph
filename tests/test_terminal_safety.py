"""Repository text can't drive the reader's terminal (issue #126).

Found while writing SECURITY.md: titles, body excerpts and quoted comments were printed
verbatim, escape sequences included. An issue body with `ESC ] 8 ;; url BEL` printed a
hyperlink whose visible text wasn't its target. `ESC [ 2 J` cleared the screen, evidence
trail and all. `ESC ] 0 ; ... BEL` retitled the terminal window. These tests push such
text through every text renderer and require none of it to survive.
"""

from __future__ import annotations

import io
import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from decision_graph import attempts, render, retrieval
from decision_graph.reasoning import Answer, Mode, NodeRef, Path as WalkPath, Step
from decision_graph.trace import Closure, Standing

EVIL = "fix\x1b]8;;https://evil.example\x07click\x1b]8;;\x07 \x1b[2J\x1b]0;pwned\x07\x9b31m ‮esrever"
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
CASES = json.loads((Path(__file__).parent / "fixtures" / "adversarial_bodies.json").read_text(encoding="utf-8"))["cases"]
T = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _answer() -> Answer:
    step = Step(1, "motivated_by", "explicit", "explicit", 2, 1, "x", None)
    path = WalkPath([1, 2], [step], {1: NodeRef("issue", EVIL, "5"), 2: NodeRef("decision", EVIL, "k")})
    return Answer(Mode.WHY, 1, [path], False, "1 path")


class TerminalSafetyTest(unittest.TestCase):
    def assertInert(self, text: str) -> None:
        self.assertIsNone(UNSAFE.search(text), repr(text))

    def test_safe_strips_and_keeps_layout(self) -> None:
        self.assertEqual(render.safe("a\tb\nc\x1b[2Jd"), "a\tb\ncd")
        self.assertEqual(render.safe(None), "")

    def test_answer_rendering_is_inert(self) -> None:
        out = io.StringIO()
        render.render(_answer(), bodies={1: EVIL + "\n\n" + EVIL}, out=out)
        self.assertInert(out.getvalue())
        self.assertIn("click", out.getvalue(), "the visible text should survive")

    def test_refusal_last_word_is_inert(self) -> None:
        out = io.StringIO()
        closure = Closure("issue #5", "not_planned", T, Standing(EVIL, "MEMBER", T))
        render.render(Answer(Mode.WHY, 1, [], False, "none"), closure=closure, out=out)
        self.assertInert(out.getvalue())

    def test_attempts_rendering_is_inert(self) -> None:
        c = retrieval.Candidate(1, "issue", "5", EVIL, "fuzzy", 0.5)
        a = attempts.Attempt(c, None, "not_planned", T, Standing(EVIL, "MEMBER", T), [])
        out = io.StringIO()
        attempts.render(EVIL, [a], out=out)
        self.assertInert(out.getvalue().replace(repr(EVIL), ""))

    def test_the_shared_adversarial_corpus_is_inert(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                out = io.StringIO()
                render.render(_answer(), bodies={1: case["body"]}, out=out)
                self.assertInert(out.getvalue())


if __name__ == "__main__":
    unittest.main()
