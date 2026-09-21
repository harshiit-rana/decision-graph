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


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class TierLabelTest(unittest.TestCase):
    """The label must name the strongest tier a node matched (issue #98).

    `query.py` treats it as load-bearing -- "a fuzzy match on a paraphrase is a different
    claim from a title hit" -- and it was arbitrary whenever a node matched two tiers at the
    same score. A title equal to the query scores 1.0 on `exact`, and `similarity()` of
    identical strings is also 1.0, so `ORDER BY id, score DESC` kept whichever row it
    happened to. Across the 220 titles in this graph it kept `fuzzy` for 77 candidates, 34
    of them the top result: the tool reported a guess where it had an exact hit.
    """

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def node(self, node_type: str, external_id: str, title: str) -> int:
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) RETURNING id",
            (node_type, external_id, title),
        ).fetchone()["id"]

    def test_a_verbatim_title_is_called_exact_not_fuzzy(self) -> None:
        title = "fixture the scheduler retries with a widening backoff"
        wanted = self.node("issue", "990101", title)

        found = retrieval.find_candidates(self.conn, title, limit=5)

        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "exact", "an exact hit was reported as a guess")

    def test_a_genuine_paraphrase_is_still_called_fuzzy(self) -> None:
        # The label only moves where the node really did match a stronger tier. A near-miss
        # must keep saying so, or the distinction stops carrying information in the other
        # direction.
        self.node("issue", "990102", "fixture the scheduler retries with a widening backoff")

        found = retrieval.find_candidates(
            self.conn, "fixture the scheduler retries with widening backoffs", limit=5
        )

        self.assertTrue(found)
        self.assertEqual(found[0].match, "fuzzy")

    def test_the_tie_break_does_not_reorder_candidates(self) -> None:
        # `tier` is the last ordering key, after `score`, so it settles which label survives
        # for one node and never which node comes first.
        exact = self.node("issue", "990103", "fixture widening backoff")
        near = self.node("issue", "990104", "fixture widening backoffs")

        found = retrieval.find_candidates(self.conn, "fixture widening backoff", limit=5)
        ids = [c.node_id for c in found]

        self.assertEqual(ids[0], exact)
        self.assertIn(near, ids)
        self.assertLess(ids.index(exact), ids.index(near))

    def test_an_identifier_hit_outranks_a_fuzzy_one_and_says_which(self) -> None:
        wanted = self.node("issue", "990105", "fixture unrelated wording entirely")
        self.node("pull_request", "990106", "fixture issue mentioned in a title")

        found = retrieval.find_candidates(self.conn, "issue #990105", limit=5)

        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "identifier")


class ShaPrefixOfTest(unittest.TestCase):
    """What may be read as a commit sha (issue #102). Pure, so no database."""

    def test_the_form_the_tool_prints(self) -> None:
        # `trace.ref` abbreviates every commit to seven characters, so this is the exact
        # string a reader copies out of a trace, a diagram or a report row.
        self.assertEqual(retrieval.sha_prefix_of("eca5fd1"), "eca5fd1")

    def test_the_word_the_tool_prints_in_front_of_it(self) -> None:
        # `trace.ref` renders "commit eca5fd1", and that whole string is what gets copied.
        # Accepting only the bare sha fixed the identifier without fixing the rendering it
        # came from -- and "commit 028160e" fuzzy-matched a *workflow*.
        self.assertEqual(retrieval.sha_prefix_of("commit eca5fd1"), "eca5fd1")
        self.assertEqual(retrieval.sha_prefix_of("COMMIT ECA5FD1"), "eca5fd1")
        self.assertEqual(retrieval.sha_prefix_of("commit  eca5fd1"), "eca5fd1")

    def test_only_that_exact_word(self) -> None:
        self.assertIsNone(retrieval.sha_prefix_of("commits eca5fd1"))
        self.assertIsNone(retrieval.sha_prefix_of("commit 6143"))
        self.assertIsNone(retrieval.sha_prefix_of("commit"))

    def test_a_full_sha(self) -> None:
        full = "eca5fd1dfdc614c2df876cc32018a7d71f84ea82"
        self.assertEqual(retrieval.sha_prefix_of(full), full)

    def test_case_is_normalised_because_git_accepts_either(self) -> None:
        # `git show ECA5FD1` resolves. Storing shas lowercase and matching with LIKE would
        # have made this "Nothing in the graph matches", which is a false statement about
        # the repository rather than an unrecognised input.
        self.assertEqual(retrieval.sha_prefix_of("ECA5FD1"), "eca5fd1")
        self.assertEqual(retrieval.sha_prefix_of("Eca5Fd1"), "eca5fd1")

    # -- the guard -----------------------------------------------------------

    def test_an_issue_number_is_not_a_sha_prefix(self) -> None:
        # Every decimal string is valid hex. Without the seven-character floor, `6143`
        # would offer whatever commit starts with those digits beside the issue asked for.
        for number in ("6143", "5898", "614300", "1"):
            with self.subTest(number=number):
                self.assertIsNone(retrieval.sha_prefix_of(number))

    def test_too_short_is_not_a_sha_prefix(self) -> None:
        self.assertIsNone(retrieval.sha_prefix_of("eca5fd"))

    def test_non_hex_is_not_a_sha_prefix(self) -> None:
        for query in ("eca5fd1z", "redirect", "issue #6143", "eca5fd1 "  "extra"):
            with self.subTest(query=query):
                self.assertIsNone(retrieval.sha_prefix_of(query))

    def test_longer_than_a_sha_is_not_one(self) -> None:
        self.assertIsNone(retrieval.sha_prefix_of("a" * 41))


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ShaLookupTest(unittest.TestCase):
    """Against the real schema; fixtures roll back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def commit(self, sha: str, title: str) -> int:
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES ('commit', %s, %s) "
            "RETURNING id",
            (sha, title),
        ).fetchone()["id"]

    def test_the_abbreviation_resolves_to_the_commit(self) -> None:
        full = "beef1230000000000000000000000000000000aa"
        wanted = self.commit(full, "fixture a commit with a known sha")

        found = retrieval.find_candidates(self.conn, full[:7], limit=5)

        self.assertTrue(found, "the only form the tool prints resolved to nothing")
        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "sha")

    def test_the_full_sha_is_still_an_identifier_match(self) -> None:
        # Exact identifier scores above the prefix tier, so the stronger label survives.
        full = "beef1240000000000000000000000000000000aa"
        wanted = self.commit(full, "fixture another commit")

        found = retrieval.find_candidates(self.conn, full, limit=5)

        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "identifier")

    def test_the_rendered_form_round_trips(self) -> None:
        # The whole point: what `dg query` prints as the start node must be typeable back in.
        full = "beef1260000000000000000000000000000000aa"
        wanted = self.commit(full, "fixture round trip")

        from decision_graph import trace

        found = retrieval.find_candidates(self.conn, trace.ref("commit", full), limit=5)

        self.assertTrue(found, "the tool's own rendering of a commit resolved to nothing")
        self.assertEqual(found[0].node_id, wanted)

    def test_an_ambiguous_prefix_offers_every_match(self) -> None:
        # What git does. `dg query` already prints "N other candidates matched this query",
        # so choosing one here would hide the ambiguity rather than resolve it.
        a = self.commit("beef1250000000000000000000000000000000aa", "fixture first")
        b = self.commit("beef1250000000000000000000000000000000bb", "fixture second")

        ids = [c.node_id for c in retrieval.find_candidates(self.conn, "beef125", limit=10)]

        self.assertIn(a, ids)
        self.assertIn(b, ids)

    def test_a_number_does_not_reach_the_sha_tier(self) -> None:
        self.commit("6143000000000000000000000000000000000000", "fixture numeric-looking sha")
        wanted = self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) "
            "VALUES ('issue', '614399', 'fixture the issue actually asked for') RETURNING id"
        ).fetchone()["id"]

        found = retrieval.find_candidates(self.conn, "614399", limit=5)

        self.assertEqual(found[0].node_id, wanted)
        self.assertEqual(found[0].match, "identifier")


if __name__ == "__main__":
    unittest.main()
