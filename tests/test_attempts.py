"""Prior attempts: `dg query --mode attempts` (issue #137).

What's guarded:
- Only ended work is listed: an unmerged PR, a not-planned issue, a duplicate. Open work
  and an issue closed as completed are not attempts.
- "Tried in #A, landed in #B" is reported when a merged PR shares the attempt's thread.
- No sentence states why anything closed. This is #109's rule, checked here the same way.
- Every printed reference resolves back to its artifact (the round-trip rule).
- An empty list says what it does not mean.
- The JSON document validates against the published schema.
"""

from __future__ import annotations

import io
import json
import os
import re
import unittest
from datetime import datetime, timezone

from decision_graph import attempts, contract, retrieval
from decision_graph.trace import Standing

from tests.test_contract import SCHEMA, validate

DSN = os.environ.get("DATABASE_URL")
T = datetime(2026, 1, 10, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 25, tzinfo=timezone.utc)

# Words that would turn "how it ended" into "why it ended". The caveat sentence names
# rejection and supersession on purpose, to deny them, so it is removed before checking.
_CAUSAL = re.compile(r"\b(because|due to|was rejected|were rejected|reason (?:was|is)|so that)\b", re.I)


def _attempt(ending="unmerged", *, landed=True, said=True) -> attempts.Attempt:
    c = retrieval.Candidate(41, "pull_request", "5867", "use 303 for redirect", "fuzzy", 0.5)
    return attempts.Attempt(
        candidate=c, url="https://github.com/pallets/flask/pull/5867", ending=ending,
        closed_at=T,
        last_word=Standing("Superseded by the other one, thanks!", "MEMBER", T) if said else None,
        landed_in=[attempts.Landed(42, "5899", "use 303", "https://github.com/pallets/flask/pull/5899", LATER)]
        if landed else [],
    )


def _text(found) -> str:
    out = io.StringIO()
    attempts.render("redirect 303", found, out=out)
    return out.getvalue()


class RenderTest(unittest.TestCase):
    def test_tried_here_landed_there(self) -> None:
        text = _text([_attempt()])
        self.assertIn("pull request #5867", text)
        self.assertIn("closed without merging on 2026-01-10", text)
        self.assertIn("the same thread later landed in pull request #5899, merged 2026-01-25", text)

    def test_quoted_last_word_is_attributed_and_dated(self) -> None:
        text = _text([_attempt()])
        self.assertIn("from a MEMBER, 2026-01-10", text)
        self.assertIn("Superseded by the other one", text)

    def test_no_sentence_states_a_reason(self) -> None:
        for ending in attempts.ENDINGS:
            with self.subTest(ending=ending):
                text = _text([_attempt(ending, said=False)])
                text = text.replace(attempts.NO_REASON_CAVEAT, "")
                self.assertIsNone(_CAUSAL.search(text), text)
                self.assertIn(attempts.NO_REASON_CAVEAT, _text([_attempt(ending)]))

    def test_empty_list_does_not_claim_nobody_tried(self) -> None:
        text = _text([])
        self.assertIn("not evidence that nobody tried", text)
        self.assertIn("lexical", text)

    def test_every_printed_reference_round_trips_to_an_identifier(self) -> None:
        refs = re.findall(r"(?:pull request|issue) #\d+", _text([_attempt()]))
        self.assertTrue(refs)
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertEqual(retrieval.identifiers_in(ref), [ref.split("#")[1]])


class DocumentTest(unittest.TestCase):
    def test_validates_and_keeps_repository_text_in_its_fields(self) -> None:
        doc = json.loads(contract.dumps(attempts.document("redirect 303", [_attempt()])))
        self.assertEqual(validate(doc, SCHEMA), [])
        a = doc["attempts"][0]
        self.assertEqual(a["ending"], "unmerged")
        self.assertEqual(a["landed_in"][0]["ref"], "pull request #5899")
        self.assertIn("not why it closed", a["last_word"]["caveat"])

    def test_empty_document_still_carries_its_caveats(self) -> None:
        doc = json.loads(contract.dumps(attempts.document("x", [])))
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertEqual(doc["attempts"], [])
        self.assertIn(attempts.LEXICAL_CAVEAT, doc["caveats"])


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class FindAttemptsTest(unittest.TestCase):
    """A seeded fixture; each test rolls back."""

    TOPIC = "zz-fixture-attempts frobnicate the widget"

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._n = 990100

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _node(self, node_type: str, suffix: str, thread: str | None = None) -> tuple[int, int]:
        self._n += 1
        row = self.conn.execute(
            "INSERT INTO node (node_type, external_id, title, url, thread_key) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (node_type, str(self._n), f"{self.TOPIC} {suffix}",
             f"https://example.invalid/{self._n}", thread),
        ).fetchone()
        return row["id"], self._n

    def _pr(self, suffix, *, state="closed", merged_at=None, thread=None) -> int:
        nid, number = self._node("pull_request", suffix, thread)
        self.conn.execute(
            "INSERT INTO pull_request (node_id, number, state, merged_at, closed_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (nid, number, state, merged_at, T if state == "closed" else None),
        )
        return nid

    def _issue(self, suffix, state_reason) -> int:
        nid, number = self._node("issue", suffix)
        self.conn.execute(
            "INSERT INTO issue (node_id, number, state, closed_at, state_reason) "
            "VALUES (%s, %s, 'closed', %s, %s)",
            (nid, number, T, state_reason),
        )
        return nid

    def test_lists_ended_work_only_and_links_where_it_landed(self) -> None:
        thread = "thread:999:pr-990101"
        abandoned = self._pr("first try", thread=thread)
        landed = self._pr("second try", merged_at=LATER, thread=thread)
        declined = self._issue("please add", "not_planned")
        dupe = self._issue("same again", "duplicate")
        open_pr = self._pr("still open", state="open")
        done = self._issue("all done", "completed")

        found = attempts.find_attempts(self.conn, self.TOPIC, limit=50)
        by_id = {a.candidate.node_id: a for a in found}

        self.assertEqual(set(by_id), {abandoned, declined, dupe})
        self.assertNotIn(open_pr, by_id)
        self.assertNotIn(done, by_id)
        self.assertNotIn(landed, by_id, "merged work is not an attempt")
        self.assertEqual(by_id[declined].ending, "not_planned")
        self.assertEqual(by_id[dupe].ending, "duplicate")
        self.assertEqual([l.node_id for l in by_id[abandoned].landed_in], [landed])
        self.assertEqual(by_id[declined].landed_in, [])

    def test_last_word_comes_from_someone_with_standing(self) -> None:
        declined = self._issue("please add", "not_planned")
        self._n += 1
        comment = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('comment', %s) RETURNING id",
            (str(self._n),),
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO comment (node_id, parent_node_id, body, created_at, author_association) "
            "VALUES (%s, %s, 'Not something we will add.', %s, 'MEMBER')",
            (comment, declined, T),
        )
        found = attempts.find_attempts(self.conn, self.TOPIC)
        self.assertEqual(found[0].last_word.body, "Not something we will add.")

    def test_limit_is_respected(self) -> None:
        for i in range(4):
            self._issue(f"variant {i}", "not_planned")
        self.assertEqual(len(attempts.find_attempts(self.conn, self.TOPIC, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
