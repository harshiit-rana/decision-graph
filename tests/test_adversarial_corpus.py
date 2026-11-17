"""One hostile corpus, every output surface (issue #145).

Escaping used to be handled case by case. This runs the same stranger-written titles and bodies
through every renderer and asserts, per surface, that the text stayed *data*:

- terminal (`dg query`'s trail, prior attempts, the menu's decision list): no control, escape or
  bidi character survives, and a title cannot start a line of its own;
- Mermaid and DOT (enumerated from `diagram.FORMATS`, so a new format fails until it is covered),
  and the report's own per-Decision Mermaid: once quoted labels are blanked, the output has the
  same shape as the one for a benign title, so hostile text can only have changed what a label
  says, never added a statement;
- HTML report: the same elements and attribute names as for a benign title, no `on*` handler, no
  `javascript:` URL. This is a static parse of the file. Mermaid's in-browser drawing of a label
  is not run here; the page relies on `securityLevel: "strict"` for that, which is pinned;
- JSON (answer, refusal, lookup and attempts documents): the text round-trips byte for byte and
  changes nothing but repository text fields, each in an object carrying an `untrusted` label;
- model prompt: hostile text reaches only fields the instructions name as written by strangers,
  and never the instructions themselves.

Every `--format` flag in `src/` is found by reading the source, so a new one fails until its
formats face the corpus here.

The MCP server's documents are covered against a real database in test_mcp_server.py.
Titles come from `fixtures/adversarial_surfaces.json`; bodies from `adversarial_bodies.json` (#119).
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import re
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from decision_graph import (agent_instructions, attempts, cli, contract, diagram, explain, menu,
                            query, render, report, retrieval)
from decision_graph.reasoning import Answer, Mode, NodeRef, Path as WalkPath, Step
from decision_graph.trace import Closure, Standing
from tests.test_contract import finished
from tests.test_explain import StubClient
from tests.test_report import (bucket as report_bucket, data as report_data,
                               decision as report_decision, edge as report_edge)

FIXTURES = Path(__file__).parent / "fixtures"
BENIGN = "benign title"
UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
T = datetime(2026, 1, 25, tzinfo=timezone.utc)


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


def candidate_with(title: str, node_id: int = 1) -> retrieval.Candidate:
    return retrieval.Candidate(node_id, "issue", "5", title, "exact", 1.0)


def attempt_with(text: str) -> attempts.Attempt:
    """A prior attempt whose title, last word and landing pull request are all `text`."""
    landed = attempts.Landed(3, "7", text, "https://github.com/a/b/pull/7", T)
    return attempts.Attempt(candidate_with(text), "https://github.com/a/b/issues/5", "not_planned", T,
                            Standing(text, "MEMBER", T), [landed])


def objects(value):
    """Every JSON object in a document, nested ones included."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from objects(v)


def blank(value, keys):
    """A copy with every non-null value under one of `keys` replaced by the same marker."""
    if isinstance(value, dict):
        return {k: "<text>" if k in keys and v is not None else blank(v, keys) for k, v in value.items()}
    if isinstance(value, list):
        return [blank(v, keys) for v in value]
    return value


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
    def setUp(self) -> None:
        # Colour is decided at import, from whether stdout is a terminal. With it off, any escape
        # in the output came from the corpus rather than from the renderer's own styling, and
        # these pass the same in a terminal as in CI.
        for module in (render, cli):
            patcher = mock.patch.object(module, "_COLOR", False)
            patcher.start()
            self.addCleanup(patcher.stop)

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

    def test_prior_attempts_print_titles_and_last_words_as_inert_text(self) -> None:
        for name, text in CORPUS:
            with self.subTest(case=name):
                out = io.StringIO()
                attempts.render("#5", [attempt_with(text)], out=out)
                self.assertIsNone(UNSAFE.search(out.getvalue()), repr(UNSAFE.search(out.getvalue())))
                for line in out.getvalue().splitlines():
                    self.assertFalse(line.lstrip().startswith("✔ explicit"), line)

    def test_the_menus_decision_list_prints_a_title_as_one_inert_line(self) -> None:
        for name, text in CORPUS:
            with self.subTest(case=name):
                line = menu.decision_line(1, {"decided_at": T, "issue_ref": "5",
                                              "status": "reconstructed", "title": text})
                self.assertIsNone(UNSAFE.search(line), repr(UNSAFE.search(line)))
                self.assertNotIn("\n", line)


MERMAID_QUOTED = re.compile(r'"[^"\n]*"')
DOT_QUOTED = re.compile(r'"(?:[^"\\\n]|\\.)*"')
SRC = Path(diagram.__file__).parent


def blank_labels(source: str, quoted: re.Pattern) -> list[str]:
    return [quoted.sub('""', line) for line in source.splitlines()]


def modules_with_a_format_flag() -> set[str]:
    """Every module in src/ that declares `add_argument("--format", ...)`."""
    found = set()
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and any(isinstance(a, ast.Constant) and a.value == "--format" for a in node.args)):
                found.add(path.stem)
    return found


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

    def test_every_format_flag_in_src_belongs_to_a_command_covered_here(self) -> None:
        # Read from the source, so a `--format` added to any module is found, not only one added
        # to `dg query`. query's formats (text, json, mermaid, dot) all face the corpus in this
        # file. agent-instructions prints static text: its renderer takes the format and nothing
        # else, so no repository text can reach it.
        self.assertEqual(modules_with_a_format_flag(), {"query", "agent_instructions"},
                         "a new --format flag: run its formats over the corpus here, then add it")
        self.assertEqual(list(inspect.signature(agent_instructions.render).parameters), ["fmt"])

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


# ---------------------------------------------------------------------------
# The HTML report
# ---------------------------------------------------------------------------


def report_page(text: str) -> str:
    """The report with `text` in every field a stranger can write: a Decision's title (copied from
    an issue), an evidence row's title, and, defensively, the logins (GitHub restricts those)."""
    concentration = {"decisions": report_bucket(top=(text, 10), bots=(text,)),
                     "merged_prs": report_bucket(top=(text, 16), total=29, authors=13)}
    return report.render_html(report_data(decisions=[report_decision(title=text)],
                                          edges=[{**report_edge(), "other_title": text}],
                                          reviewers={1: [text]}, concentration=concentration),
                              "pallets/flask")


class PageShape(HTMLParser):
    """What a browser would build from a page, minus the text: tags, attribute names, comments,
    declarations, and the content of script and style elements (which run, so count as shape)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.shape: list[tuple] = []
        self.attrs: list[tuple[str, str | None]] = []
        self._raw: str | None = None

    def _tag(self, kind: str, tag: str, attrs) -> None:
        self.shape.append((kind, tag, tuple(sorted(name for name, _ in attrs))))
        self.attrs.extend(attrs)

    def handle_starttag(self, tag, attrs) -> None:
        self._tag("start", tag, attrs)
        self._raw = tag if tag in ("script", "style") else None

    def handle_startendtag(self, tag, attrs) -> None:
        self._tag("startend", tag, attrs)

    def handle_endtag(self, tag) -> None:
        self.shape.append(("end", tag))
        self._raw = None

    def handle_data(self, data) -> None:
        if self._raw:
            self.shape.append(("raw", self._raw, data))

    def handle_comment(self, data) -> None:
        self.shape.append(("comment", data))

    def handle_decl(self, decl) -> None:
        self.shape.append(("decl", decl))

    def handle_pi(self, data) -> None:
        self.shape.append(("pi", data))

    def unknown_decl(self, data) -> None:
        self.shape.append(("unknown_decl", data))


def page_shape(page: str) -> PageShape:
    parsed = PageShape()
    parsed.feed(page)
    parsed.close()
    return parsed


class HtmlReportTest(unittest.TestCase):
    def test_hostile_text_adds_no_element_attribute_comment_or_script(self) -> None:
        benign = page_shape(report_page(BENIGN)).shape
        for name, text in CORPUS:
            with self.subTest(case=name):
                self.assertEqual(page_shape(report_page(text)).shape, benign,
                                 "hostile text changed the page's markup, not just its text")

    def test_no_event_handler_or_javascript_url_anywhere(self) -> None:
        for name, text in CORPUS:
            with self.subTest(case=name):
                for attr, value in page_shape(report_page(text)).attrs:
                    self.assertFalse(attr.lower().startswith("on"), attr)
                    squeezed = re.sub(r"[\s\x00-\x1f]", "", value or "").lower()
                    self.assertFalse(squeezed.startswith("javascript:"), f"{attr}={value!r}")

    def test_the_reports_own_diagram_changes_a_label_and_nothing_else(self) -> None:
        def drawn(text: str) -> str:
            return report._decision_mermaid(report_decision(title=text),
                                            [{**report_edge(), "other_title": text}])

        benign = blank_labels(drawn(BENIGN), MERMAID_QUOTED)
        for name, text in CORPUS:
            with self.subTest(case=name):
                self.assertEqual(blank_labels(drawn(text), MERMAID_QUOTED), benign)
                self.assertNotIn("%%", drawn(text), "a title can't open a Mermaid directive")

    def test_mermaid_draws_labels_in_strict_mode(self) -> None:
        # Mermaid turns label text into SVG in the browser, which the parse above never sees.
        # "strict" is what keeps markup in a label from becoming markup on the page; "loose"
        # would let a title's <img onerror> run. A title can't change it (the %% test above).
        page = report_page(BENIGN)
        self.assertEqual(page.count("mermaid.initialize("), 1)
        self.assertIn('securityLevel: "strict"', page)


# ---------------------------------------------------------------------------
# The JSON contract
# ---------------------------------------------------------------------------

TEXT_FIELDS = contract.REPOSITORY_TEXT_FIELDS


def json_documents(text: str, quotes: str = "all") -> dict[str, dict]:
    """Every document kind that carries repository text, built around `text`, labelled as `dg`
    labels them, and read back the way a consumer reads it. No author is known for nodes 1-4, so
    their text is untrusted; the closing comment is a maintainer's."""
    answer = contract.answer_document(
        query="#5", mode="why", as_of=None, max_depth=3,
        results=[contract.result_doc(candidate_with(text), answer_with(text), contract.Facts())],
        other_candidates=[candidate_with(text, node_id=4)])
    closed = Closure("issue #5", "not_planned", T, Standing(text, "MEMBER", T))
    refusal = contract.answer_document(
        query="#5", mode="why", as_of=None, max_depth=3, other_candidates=[],
        results=[contract.result_doc(candidate_with(text), Answer(Mode.WHY, 1, [], False, "no path"),
                                     contract.Facts(), closure=closed)])
    docs = {"answer": answer, "refusal": refusal,
            "lookup": contract.lookup_document("#5", [candidate_with(text)]),
            "attempts": attempts.document("#5", [attempt_with(text)])}
    return {kind: json.loads(contract.dumps(finished(doc, {}, quotes))) for kind, doc in docs.items()}


class JsonTest(unittest.TestCase):
    BENIGN_DOCS = json_documents(BENIGN)

    def test_text_round_trips_byte_for_byte_and_changes_only_text_fields(self) -> None:
        for name, text in CORPUS:
            for kind, doc in json_documents(text).items():
                with self.subTest(case=name, kind=kind):
                    self.assertEqual(blank(doc, TEXT_FIELDS), blank(self.BENIGN_DOCS[kind], TEXT_FIELDS),
                                     "hostile text changed something other than a repository text field")
                    carried = [(k, obj) for obj in objects(doc) for k in TEXT_FIELDS
                               if obj.get(k) is not None]
                    self.assertTrue(carried)
                    for key, obj in carried:
                        self.assertEqual(obj[key], text, f"{key} was altered on the way through")
                        self.assertIsInstance(obj.get("untrusted"), bool, f"{key} without a label")

    def test_a_body_excerpt_sits_in_a_labelled_text_field(self) -> None:
        # The excerpt is cleaned for reading, so it isn't byte for byte; where it sits still matters.
        for name, text in CORPUS:
            with self.subTest(case=name):
                result = contract.result_doc(candidate_with(text), answer_with(text),
                                             contract.Facts(bodies={1: text, 3: text}))
                doc = json.loads(contract.dumps(finished(contract.answer_document(
                    query="#5", mode="why", as_of=None, max_depth=3, results=[result],
                    other_candidates=[]), {})))
                context = doc["results"][0]["context"]
                if context is not None:
                    self.assertEqual(set(context),
                                     {"node_id", "ref", "excerpt", "author_association", "untrusted"})
                    self.assertIs(context["untrusted"], True)

    def test_maintainers_mode_leaves_no_untrusted_text_anywhere(self) -> None:
        for name, text in CORPUS:
            for kind, doc in json_documents(text, quotes="maintainers").items():
                with self.subTest(case=name, kind=kind):
                    for obj in objects(doc):
                        if obj.get("untrusted") is not False:
                            for key in TEXT_FIELDS:
                                self.assertIsNone(obj.get(key), f"{key} kept in {kind}")
                    for obj in objects(doc):
                        for key, value in obj.items():
                            if isinstance(value, str) and text in value:
                                self.assertIs(obj.get("untrusted"), False, f"{key} in {kind}")


# ---------------------------------------------------------------------------
# The model prompt (`dg ask`)
# ---------------------------------------------------------------------------

# The keys under which `explain.answer_payload` puts repository-written text. The instructions must
# name each one as data; the shape check below fails if text turns up under any other key.
PROMPT_TEXT_FIELDS = ("title", "decision_title", "untrusted_artifact_text")


def sent_to_model(text: str) -> tuple[str, dict]:
    client = StubClient("A summary.")
    explain.summarize_answer("why did this change?", answer_with(text), client,
                             bodies={1: text, 3: text}, annotations={2: "PR #7, merged 2026-01-25"})
    [(system, user)] = [tuple(m["content"] for m in msgs) for msgs in client.messages]
    return system, json.loads(user)


class PromptTest(unittest.TestCase):
    def setUp(self) -> None:
        saved = os.environ.pop(explain.SEND_BODIES_ENV, None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, explain.SEND_BODIES_ENV, saved)

    def test_hostile_text_never_changes_the_instructions(self) -> None:
        benign, _ = sent_to_model(BENIGN)
        for name, text in CORPUS:
            with self.subTest(case=name):
                system, _ = sent_to_model(text)
                self.assertEqual(system, benign, "repository text reached the instructions")

    def test_hostile_text_reaches_only_the_fields_named_as_data(self) -> None:
        _, benign = sent_to_model(BENIGN)
        for name, text in CORPUS:
            with self.subTest(case=name):
                _, doc = sent_to_model(text)
                self.assertEqual(blank(doc, PROMPT_TEXT_FIELDS), blank(benign, PROMPT_TEXT_FIELDS),
                                 "repository text added a field or reached an unnamed one")
                cleaned = explain._clean(text)
                for obj in objects(doc):
                    for key in ("title", "decision_title"):
                        if key in obj:
                            self.assertEqual(obj[key], cleaned, f"{key} altered beyond cleaning")

    def test_every_field_that_carries_repository_text_is_named_in_the_instructions(self) -> None:
        for field in PROMPT_TEXT_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', explain._BOUNDARY)
