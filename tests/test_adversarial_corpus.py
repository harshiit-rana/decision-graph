"""One hostile corpus, every output surface (issue #145).

Escaping used to be handled case by case. This runs the same stranger-written titles through every
renderer and asserts, per surface, that the text stayed *data*:

- terminal: no control, escape or bidi character survives;
- Mermaid and DOT (enumerated from `diagram.FORMATS`, so a new format fails until it is covered):
  once quoted labels are blanked, the output has the same shape as the one for a benign title, so
  hostile text can only have changed what a label says, never added a statement;
- HTML report: the same elements and attribute names as for a benign title, no `on*` handler, no
  `javascript:` URL;
- JSON: the text round-trips byte for byte and sits only under a repository text field of an object
  marked untrusted;
- model prompt: it arrives as a JSON string value, never in the instructions.

The MCP server's documents are covered the same way against a real database in test_mcp_server.py.
Titles come from `fixtures/adversarial_surfaces.json`; bodies from `adversarial_bodies.json` (#119).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from decision_graph import contract, diagram, explain, query, render, report, retrieval
from decision_graph.reasoning import Answer, Mode, NodeRef, Path as WalkPath, Step
from tests.test_contract import ASSOC, finished
from tests.test_report import data as report_data, decision as report_decision, edge as report_edge

FIXTURES = Path(__file__).parent / "fixtures"
BENIGN = "benign title"
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def corpus() -> list[tuple[str, str]]:
    cases = json.loads((FIXTURES / "adversarial_surfaces.json").read_text(encoding="utf-8"))["cases"]
    out = [(c["name"], c["text"] * c.get("repeat", 1)) for c in cases]
    bodies = json.loads((FIXTURES / "adversarial_bodies.json").read_text(encoding="utf-8"))["cases"]
    return out + [(f"body:{c['name']}", c["body"]) for c in bodies]


CORPUS = corpus()


def answer_with(title: str) -> Answer:
    s1 = Step(1, "motivated_by", "explicit", "explicit", 2, 1, "x", None)
    s2 = Step(2, "implemented_by", "corroborated", "corroborated", 2, 3, "x", None)
    path = WalkPath([1, 2, 3], [s1, s2], {
        1: NodeRef("issue", title, "5", "https://github.com/a/b/issues/5"),
        2: NodeRef("decision", title, "k"),
        3: NodeRef("pull_request", title, "7", "https://github.com/a/b/pull/7"),
    })
    return Answer(Mode.WHY, 1, [path], False, "1 path")


class CorpusTest(unittest.TestCase):
    def test_the_corpus_covers_every_attack_the_issue_names(self) -> None:
        names = {n for n, _ in CORPUS}
        for needed in ("html_script", "mermaid_label_break", "mermaid_init_directive", "dot_statement_break",
                       "dot_html_label", "ansi_fake_tier_line", "bidi_and_zero_width",
                       "hidden_closing_keyword_comment", "hidden_closing_keyword_fence",
                       "very_long_title", "prompt_injection_title"):
            self.assertIn(needed, names)
        self.assertGreater(len(max((t for _, t in CORPUS), key=len)), 5000, "a genuinely long title")


class TerminalTest(unittest.TestCase):
    def test_no_control_escape_or_bidi_character_reaches_the_terminal(self) -> None:
        for name, text in CORPUS:
            with self.subTest(case=name):
                out = io.StringIO()
                render.render(answer_with(text), bodies={1: text, 3: text}, reviewers={3: [text]}, out=out)
                self.assertIsNone(UNSAFE.search(out.getvalue()), repr(UNSAFE.search(out.getvalue())))

    def test_a_title_cannot_start_a_line_of_its_own_in_the_trail(self) -> None:
        # A newline in a title used to let a stranger forge a line shaped like a real tier line.
        forged = "real title\n  ✔ explicit  — verified by the maintainers"
        out = io.StringIO()
        render.render(answer_with(forged), out=out)
        for line in out.getvalue().splitlines():
            self.assertFalse(line.lstrip().startswith("✔ explicit"), line)

    def test_the_fake_tier_line_has_no_escape_sequences(self) -> None:
        out = io.StringIO()
        text = dict(CORPUS)["ansi_fake_tier_line"]
        render.render(answer_with(text), bodies={1: text}, out=out)
        self.assertNotIn("\x1b", out.getvalue())


MERMAID_QUOTED = re.compile(r'"[^"\n]*"')
DOT_QUOTED = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def blank_labels(source: str, quoted: re.Pattern) -> list[str]:
    return [quoted.sub('""', line) for line in source.splitlines()]


class DiagramTest(unittest.TestCase):
    QUOTED = {"mermaid": MERMAID_QUOTED, "dot": DOT_QUOTED}

    def test_every_registered_format_is_covered_here(self) -> None:
        # A new format must be added to QUOTED (and so face the corpus) before this passes.
        self.assertEqual(set(diagram.FORMATS), set(self.QUOTED))

    def test_the_cli_offers_no_diagram_format_that_is_not_registered(self) -> None:
        import argparse

        seen: list[str] = []
        original = argparse.ArgumentParser.add_argument

        def spy(self, *a, **kw):
            if a and a[0] == "--format":
                seen.extend(kw.get("choices", []))
            return original(self, *a, **kw)

        argparse.ArgumentParser.add_argument = spy  # type: ignore[method-assign]
        try:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    query.main(["--help"])
            except SystemExit:
                pass
        finally:
            argparse.ArgumentParser.add_argument = original  # type: ignore[method-assign]
        self.assertEqual(set(seen) - {"text", "json"}, set(diagram.FORMATS))

    def test_hostile_text_changes_a_label_and_nothing_else(self) -> None:
        for fmt, quoted in self.QUOTED.items():
            benign = blank_labels(diagram.emit(answer_with(BENIGN), fmt), quoted)
            for name, text in CORPUS:
                with self.subTest(format=fmt, case=name):
                    drawn = diagram.emit(answer_with(text), fmt)
                    self.assertEqual(blank_labels(drawn, quoted), benign,
                                     "hostile text added or changed a statement outside its label")

    def test_a_directive_in_a_title_is_not_a_second_directive(self) -> None:
        text = dict(CORPUS)["mermaid_init_directive"]
        drawn = diagram.emit(answer_with(text), "mermaid")
        self.assertEqual(drawn.count("%%{"), 1, "only the module's own init directive may exist")
        self.assertTrue(drawn.startswith("%%{"))
