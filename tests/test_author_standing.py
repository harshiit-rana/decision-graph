"""Every quoted span says who wrote it, and `--quotes maintainers` removes the rest (#177).

The JSON contract (#135) confines repository text to named fields, but a title from a
drive-by account and one from a maintainer looked identical to an agent. These check:

- every object that carries a `title`, `excerpt` or `body` also carries `author_association`
  and `untrusted`, in all three document kinds, and the published schema requires them;
- `untrusted` is true for everything except OWNER, MEMBER and COLLABORATOR, and for unknown;
- the shared adversarial corpus, posted by an outsider, arrives labelled untrusted;
- `--quotes maintainers` removes untrusted text, keeps the objects, and counts what it removed;
- the author really is read from the stored data: comment column, issue/PR payload, and
  null for commits.

The labels are advisory. A client model can still follow injected text, and these tests say
nothing about that.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from decision_graph import attempts, context, contract, query, retrieval
from decision_graph.reasoning import Mode

from tests.test_attempts import _attempt
from tests.test_contract import (
    ASSOC, DECISION, ISSUE, PR, SCHEMA, candidate, finished, closure, doc_for, explicit_answer,
    explicit_facts, refused, roundtrip, validate,
)

DSN = os.environ.get("DATABASE_URL")
CASES = json.loads((Path(__file__).parent / "fixtures" / "adversarial_bodies.json")
                   .read_text(encoding="utf-8"))["cases"]


def objects(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from objects(v)


def text_bearing(doc):
    return [o for o in objects(doc) if any(k in o for k in contract.REPOSITORY_TEXT_FIELDS)]


def answered(assoc=None, quotes="all"):
    return doc_for(candidate(), explicit_answer(), explicit_facts(), quotes=quotes) if assoc is None \
        else _with(assoc, quotes)


def _with(assoc, quotes):
    c = candidate()
    result = contract.result_doc(c, explicit_answer(), explicit_facts())
    doc = contract.answer_document(query="q", mode="why", as_of=None, max_depth=3,
                                   results=[result], other_candidates=[])
    return finished(doc, assoc, quotes)


class EveryQuotedFieldHasAnAuthorTest(unittest.TestCase):
    def test_all_three_document_kinds_label_every_text_object(self) -> None:
        c = candidate()
        why = contract.result_doc(c, explicit_answer(), explicit_facts())
        impact = contract.result_doc(c, refused(Mode.IMPACT), contract.Facts())
        docs = {
            "answer": doc_for(c, explicit_answer(), explicit_facts()),
            "attempts": finished(attempts.document("q", [_attempt()]), {41: "NONE", 42: "OWNER"}),
            "context": finished(
                context.assemble("q", why, impact, attempts.document("q", [_attempt()])["attempts"]), ASSOC),
            "refusal": doc_for(c, refused(), clo=closure("not_planned")),
        }
        for name, doc in docs.items():
            found = text_bearing(doc)
            self.assertTrue(found, name)
            for obj in found:
                with self.subTest(document=name, keys=sorted(obj)[:4]):
                    self.assertIn("untrusted", obj)
                    self.assertIn("author_association", obj)
            self.assertEqual(validate(roundtrip(doc), SCHEMA), [], name)

    def test_the_schema_requires_the_sibling_fields(self) -> None:
        for name in ("node", "candidate", "landed", "context"):
            with self.subTest(definition=name):
                self.assertIn("author_association", SCHEMA["$defs"][name]["required"])
                self.assertIn("untrusted", SCHEMA["$defs"][name]["required"])
        self.assertIn("untrusted", SCHEMA["$defs"]["last_word"]["required"])

    def test_a_document_missing_the_labels_fails_validation(self) -> None:
        doc = roundtrip(answered())
        del doc["results"][0]["start"]["untrusted"]
        self.assertTrue(validate(doc, SCHEMA))


class UntrustedRuleTest(unittest.TestCase):
    def test_only_owner_member_and_collaborator_are_trusted(self) -> None:
        trusted = {a: not contract.is_untrusted(a) for a in
                   ("OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "NONE", "FIRST_TIMER",
                    "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN")}
        self.assertEqual({a for a, t in trusted.items() if t}, {"OWNER", "MEMBER", "COLLABORATOR"})

    def test_unknown_is_never_promoted_to_trusted(self) -> None:
        self.assertTrue(contract.is_untrusted(None))
        self.assertTrue(contract.is_untrusted(""))

    def test_the_trusted_set_matches_who_has_standing_to_close(self) -> None:
        from decision_graph import trace
        self.assertEqual(set(trace._STANDING), set(contract.TRUSTED_ASSOCIATIONS))

    def test_labels_follow_each_fixture_association(self) -> None:
        for assoc, untrusted in (("OWNER", False), ("MEMBER", False), ("COLLABORATOR", False),
                                 ("CONTRIBUTOR", True), ("NONE", True), (None, True)):
            with self.subTest(association=assoc):
                doc = _with({ISSUE: assoc}, "all")
                issue = doc["results"][0]["paths"][0]["nodes"][0]
                self.assertEqual((issue["author_association"], issue["untrusted"]), (assoc, untrusted))


class AdversarialCorpusTest(unittest.TestCase):
    def test_an_outsiders_injection_arrives_labelled_untrusted(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                facts = explicit_facts()
                facts.bodies = {ISSUE: case["body"]}
                c = candidate()
                result = contract.result_doc(c, explicit_answer(), facts)
                doc = finished(contract.answer_document(
                    query="q", mode="why", as_of=None, max_depth=3, results=[result],
                    other_candidates=[]), {ISSUE: "NONE"})
                excerpt = doc["results"][0]["context"]
                self.assertTrue(excerpt["untrusted"])
                self.assertEqual(excerpt["author_association"], "NONE")
                self.assertEqual(validate(roundtrip(doc), SCHEMA), [])


class MaintainersOnlyTest(unittest.TestCase):
    def test_untrusted_text_is_removed_and_counted(self) -> None:
        # Issue by a MEMBER stays. The decision (unknown author) and PR (CONTRIBUTOR) go.
        doc = answered({ISSUE: "MEMBER", PR: "CONTRIBUTOR"}, quotes="maintainers")
        nodes = {n["node_id"]: n for p in doc["results"][0]["paths"] for n in p["nodes"]}
        self.assertEqual(nodes[ISSUE]["title"], "change default redirect code to 303")
        self.assertNotIn("suppressed", nodes[ISSUE])
        self.assertIsNone(nodes[DECISION]["title"])
        self.assertTrue(nodes[DECISION]["suppressed"])
        self.assertGreater(doc["quotes"]["suppressed"], 0)
        self.assertEqual(doc["quotes"]["mode"], "maintainers")
        self.assertEqual(validate(roundtrip(doc), SCHEMA), [])

    def test_the_count_equals_the_fields_actually_removed(self) -> None:
        loud = _with({ISSUE: "NONE"}, "all")
        before = sum(1 for o in text_bearing(loud) for k in contract.REPOSITORY_TEXT_FIELDS
                     if o.get(k) is not None and o["untrusted"])
        quiet = _with({ISSUE: "NONE"}, "maintainers")
        self.assertEqual(quiet["quotes"]["suppressed"], before)
        after = sum(1 for o in text_bearing(quiet) for k in contract.REPOSITORY_TEXT_FIELDS
                    if o.get(k) is not None and o["untrusted"])
        self.assertEqual(after, 0)

    def test_nothing_trusted_is_removed(self) -> None:
        doc = _with({ISSUE: "OWNER", PR: "MEMBER"}, "maintainers")
        trusted = [o for o in text_bearing(doc) if not o["untrusted"]]
        self.assertTrue(trusted)
        for obj in trusted:
            for key in contract.REPOSITORY_TEXT_FIELDS:
                if key in obj:
                    self.assertIsNotNone(obj[key])

    def test_a_non_maintainers_closing_comment_is_removed_too(self) -> None:
        clo = closure("not_planned")
        doc = finished(doc_for(candidate(), refused(), clo=clo), {}, quotes="maintainers")
        # The fixture's last word is from a MEMBER, so it stays.
        self.assertIsNotNone(doc["results"][0]["refusal"]["closure"]["last_word"]["body"])
        from decision_graph.trace import Closure, Standing
        outsider = Closure("issue #1", "not_planned", None, Standing("drive-by text", "NONE", None))
        doc = finished(doc_for(candidate(), refused(), clo=outsider), {}, quotes="maintainers")
        said = doc["results"][0]["refusal"]["closure"]["last_word"]
        self.assertIsNone(said["body"])
        self.assertTrue(said["suppressed"])

    def test_an_unknown_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            finished({}, {}, quotes="everyone")


def run_query(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = query.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_quotes_with_the_text_format_is_refused(self) -> None:
        saved = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://unused"
        try:
            code, out, err = run_query(["#1", "--quotes", "maintainers"])
        finally:
            if saved is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = saved
        self.assertEqual(code, 2)
        self.assertIn("--format json", err)

    def test_an_unknown_quotes_value_is_a_usage_error(self) -> None:
        code, _, err = run_query(["#1", "--quotes", "nobody"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class StoredAssociationTest(unittest.TestCase):
    """The association is read from the stored data. Each test rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _node(self, node_type, ext, raw=None):
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title, raw) VALUES (%s, %s, 't', %s) RETURNING id",
            (node_type, ext, None if raw is None else json.dumps(raw)),
        ).fetchone()["id"]

    def test_reads_issue_pr_comment_and_leaves_commits_unknown(self) -> None:
        issue = self._node("issue", "991001", {"author_association": "MEMBER"})
        pr = self._node("pull_request", "991002", {"author_association": "first_timer"})
        commit = self._node("commit", "ab" * 20, {"sha": "x"})
        comment = self._node("comment", "991003", {"author_association": "OWNER"})
        self.conn.execute(
            "INSERT INTO comment (node_id, parent_node_id, body, author_association) "
            "VALUES (%s, %s, 'b', 'COLLABORATOR')", (comment, issue))
        got = contract.read_associations(self.conn, [issue, pr, commit, comment])
        self.assertEqual(got[issue], "MEMBER")
        self.assertEqual(got[pr], "first_timer")
        self.assertIsNone(got[commit])
        self.assertEqual(got[comment], "COLLABORATOR", "the comment column wins over raw")

    def test_end_to_end_maintainers_mode_through_query(self) -> None:
        issue = self._node("issue", "991010", {"author_association": "NONE"})
        self.conn.execute("INSERT INTO issue (node_id, number, state, body) VALUES (%s, 991010, 'closed', 'x')", (issue,))
        c = retrieval.find_candidates(self.conn, "#991010", limit=1)
        doc = roundtrip(query.answer_document(self.conn, query="#991010", mode="why", candidates=c,
                                              as_of=None, max_depth=3, quotes="maintainers"))
        start = doc["results"][0]["start"]
        self.assertEqual(start["author_association"], "NONE")
        self.assertTrue(start["untrusted"])
        self.assertIsNone(start["title"])
        self.assertGreaterEqual(doc["quotes"]["suppressed"], 1)
        self.assertEqual(validate(doc, SCHEMA), [])


if __name__ == "__main__":
    unittest.main()
