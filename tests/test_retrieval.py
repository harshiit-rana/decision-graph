"""An identifier must resolve however the query phrases it (issue #96).

`trace.ref` renders an artifact as `issue #6143`, `dg report` links read the same, and a
model asked by `dg ask` for a search term writes `issue 6143`. None of those resolved: the
identifier tier only fired when the *whole* query was the number, so any phrasing around it
fell through to trigram similarity, where the word "issue" matched the title "this is issue
here" and the tool answered confidently about an unrelated pull request.

The other half of the rule matters as much. `change default redirect code to 303` is a real
title in this graph, and treating its 303 as an identifier would answer a title search with
whatever artifact happens to be numbered 303 -- trading this bug for its mirror image. A
number counts as a reference only when the query says it is one.

`identifiers_in` is pure, so most of this needs no database. The class at the bottom checks
the tiers against the real schema.
"""

from __future__ import annotations

import os
import unittest

from decision_graph import retrieval

DSN = os.environ.get("DATABASE_URL")


class IdentifiersInTest(unittest.TestCase):
    def test_the_forms_that_already_worked_still_do(self) -> None:
        self.assertEqual(retrieval.identifiers_in("#6143"), ["6143"])
        self.assertEqual(retrieval.identifiers_in("6143"), ["6143"])

    def test_the_tools_own_rendering_round_trips(self) -> None:
        # `trace.ref(node_type, external_id)` produces exactly these two strings, and they
        # appear in every trace, every report link and every refusal.
        self.assertEqual(retrieval.identifiers_in("issue #6143"), ["6143"])
        self.assertEqual(retrieval.identifiers_in("pull request #5814"), ["5814"])

    def test_the_phrasings_a_model_writes(self) -> None:
        for query in ("issue 6143", "Issue 6143", "PR 6143", "pr #6143", "pull 6143"):
            with self.subTest(query=query):
                self.assertEqual(retrieval.identifiers_in(query), ["6143"])

    def test_a_reference_inside_a_sentence(self) -> None:
        self.assertEqual(
            retrieval.identifiers_in("why was issue #6143 closed without a fix"), ["6143"]
        )

    def test_several_references_keep_their_order(self) -> None:
        self.assertEqual(retrieval.identifiers_in("#5895 supersedes #5814"), ["5895", "5814"])

    def test_a_repeated_reference_is_named_once(self) -> None:
        self.assertEqual(retrieval.identifiers_in("issue #6143 and #6143 again"), ["6143"])

    # -- the guard -----------------------------------------------------------

    def test_a_bare_number_in_prose_is_not_a_reference(self) -> None:
        # The title this protects is real: issue #5895, "change default redirect code to
        # 303". Extracting 303 would answer a title search with artifact 303.
        self.assertEqual(retrieval.identifiers_in("change default redirect code to 303"), [])

    def test_a_version_number_is_not_a_reference(self) -> None:
        self.assertEqual(retrieval.identifiers_in("drop support for Python 3.9"), [])

    def test_a_word_ending_in_the_artifact_word_does_not_count(self) -> None:
        # `\b` before the alternation: "reissue 42" and "spr 42" are not references.
        self.assertEqual(retrieval.identifiers_in("reissue 42"), [])
        self.assertEqual(retrieval.identifiers_in("spr 42"), [])

    def test_a_query_with_no_numbers_at_all(self) -> None:
        self.assertEqual(retrieval.identifiers_in("why did this happen"), [])


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class FindCandidatesTest(unittest.TestCase):
    """Against the real schema. Each test rolls back, so no fixture outlives the run."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._seq = 0

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def node(self, node_type: str, external_id: str, title: str) -> int:
        self._seq += 1
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) RETURNING id",
            (node_type, external_id, title),
        ).fetchone()["id"]

    def test_a_phrased_identifier_finds_the_artifact(self) -> None:
        wanted = self.node("issue", "990001", "a route stays registered after an error")
        self.node("pull_request", "990002", "this is issue here")

        found = retrieval.find_candidates(self.conn, "issue #990001", limit=5)

        self.assertTrue(found, "nothing matched a reference the tool itself prints")
        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "identifier")

    def test_the_decoy_title_no_longer_wins(self) -> None:
        # The exact shape of the bug: the word "issue" fuzzy-matched a title containing it,
        # and that artifact was rendered as the answer with no sign anything was wrong.
        self.node("issue", "990003", "a route stays registered after an error")
        decoy = self.node("pull_request", "990004", "this is issue here")

        found = retrieval.find_candidates(self.conn, "issue 990003", limit=5)

        self.assertNotEqual(found[0].node_id, decoy)

    def test_an_exact_title_still_outranks_a_number_it_mentions(self) -> None:
        # Squash-merge subjects carry a `#N`, and 35 titles here do. They must keep matching
        # on their own text rather than on the pull request they name.
        titled = self.node("commit", "9900000000", "Docs typo/markup fixes (#990005)")
        mentioned = self.node("pull_request", "990005", "something else entirely")

        found = retrieval.find_candidates(self.conn, "Docs typo/markup fixes (#990005)", limit=5)

        self.assertEqual(found[0].node_id, titled)
        self.assertIn(mentioned, [c.node_id for c in found],
                      "the mentioned artifact should still be offered, just not first")

    def test_a_title_with_a_bare_number_is_not_read_as_a_reference(self) -> None:
        # Modelled on issue #5895, "change default redirect code to 303", but with a title
        # of its own: that one really is in the graph, and a fixture repeating it ties with
        # the real node on the exact tier and the tie is broken arbitrarily.
        titled = self.node("issue", "990006", "fixture raises the default retry budget to 990303")
        self.node("pull_request", "990303", "fixture artifact that merely has the number")

        found = retrieval.find_candidates(
            self.conn, "fixture raises the default retry budget to 990303", limit=5
        )

        self.assertEqual(found[0].node_id, titled)


if __name__ == "__main__":
    unittest.main()
