"""A refusal must not give a reason the graph itself contradicts (issue #86).

Third instance of the class fixed in #77 and #82. Asked why an issue closed `not_planned`
happened, the renderer said "a change made without an issue leaves nothing to reconstruct
a decision from" -- to an artifact that is an issue, and that was closed unacted-on rather
than changed. Measured across the graph, that sentence was wrong for 237 of the 263
artifacts a Why-walk refuses: 185 unmerged pull requests, 50 `not_planned` issues, and two
others.

The distinction these tests defend is between *how* something ended and *why*. The first is
in the graph and checkable; the second lives in closing discussion that is not ingested, and
the sampled comments are a fair warning against guessing at it -- "ok, my bad" is a reporter
withdrawing, not a maintainers' decision. So the paragraph must name the ending and must
refuse the inference, and both halves are pinned here.

Standalone: `Answer` and `Closure` are plain dataclasses. The database-gated class at the
bottom checks the lookup that feeds them.
"""

from __future__ import annotations

import io
import os
import unittest
from datetime import datetime, timezone

from decision_graph import render, trace
from decision_graph.reasoning import Answer, Mode

DSN = os.environ.get("DATABASE_URL")

# The sentence that was wrong for 237 artifacts. Pinned as a constant because every test
# here is ultimately about whether a reader sees it.
INVENTED = "a change made"


def refusal(mode=Mode.WHY) -> Answer:
    return Answer(
        mode=mode,
        start_node_id=1,
        paths=[],
        used_inferred_fallback=False,
        explanation="no path found, explicit or inferred",
    )


def closure(kind: str, *, when=datetime(2026, 8, 28, tzinfo=timezone.utc),
            last_word=None) -> trace.Closure:
    ref = "pull request #6015" if kind == "unmerged" else "issue #6143"
    return trace.Closure(ref=ref, kind=kind, closed_at=when, last_word=last_word)


def said(body: str, association: str = "MEMBER",
         at=datetime(2026, 8, 28, tzinfo=timezone.utc)) -> trace.Standing:
    return trace.Standing(body=body, association=association, at=at)


def rendered(answer: Answer, **kwargs) -> str:
    out = io.StringIO()
    render.render(answer, out=out, **kwargs)
    return out.getvalue()


class ClosureIsNamedTest(unittest.TestCase):
    def test_a_declined_issue_is_not_told_it_lacked_an_issue(self) -> None:
        text = rendered(refusal(), closure=closure("not_planned"))
        self.assertNotIn(INVENTED, text, "the sentence the graph contradicts survived")
        self.assertIn("issue #6143", text)
        self.assertIn("not planned", text)
        self.assertIn("2026-08-28", text)

    def test_an_unmerged_pull_request_says_so(self) -> None:
        text = rendered(refusal(), closure=closure("unmerged"))
        self.assertNotIn(INVENTED, text)
        self.assertIn("pull request #6015", text)
        self.assertIn("without being merged", text)

    def test_the_date_is_named_because_a_closure_without_one_is_not_checkable(self) -> None:
        # The reader's only way to verify any of this is to open the artifact, and the
        # date is what tells them which closure they are looking for.
        self.assertIn("2026-08-28", rendered(refusal(), closure=closure("not_planned")))

    def test_a_closure_with_no_recorded_date_still_renders(self) -> None:
        # `closed_at` is nullable. Formatting None with %Y-%m-%d raises, which would turn
        # a refusal into a traceback -- the exact shape of #82.
        text = rendered(refusal(), closure=closure("not_planned", when=None))
        self.assertIn("issue #6143", text)
        self.assertNotIn("None", text)


class TheInferenceIsRefusedTest(unittest.TestCase):
    """Naming the ending is safe only while the paragraph declines to explain it."""

    def test_it_does_not_claim_the_idea_was_rejected(self) -> None:
        text = rendered(refusal(), closure=closure("not_planned"))
        self.assertIn("not evidence the idea was rejected", text)

    def test_it_names_the_alternatives_it_cannot_rule_out(self) -> None:
        # Without this, "closed without landing" reads as a polite synonym for rejected.
        text = rendered(refusal(), closure=closure("unmerged"))
        for word in ("rejection", "supersession", "abandonment"):
            self.assertIn(word, text)

    def test_it_says_the_reasoning_is_not_ingested(self) -> None:
        text = rendered(refusal(), closure=closure("unmerged"))
        self.assertIn("not ingested", text)


class OtherRefusalsAreUnchangedTest(unittest.TestCase):
    """The old sentence is correct wherever the graph records no explaining ending."""

    def test_without_a_closure_the_original_wording_stands(self) -> None:
        text = rendered(refusal())
        self.assertIn(INVENTED, text)

    def test_a_point_in_time_refusal_still_wins(self) -> None:
        # #77 established that the timestamp is named before anything else, because "nothing
        # records why" is the most easily misread thing to say to someone asking about the
        # past. A closure must not displace it.
        text = rendered(refusal(), as_of=datetime(2026, 1, 1), closure=closure("not_planned"))
        self.assertIn("Asked as of 2026-01-01", text)
        self.assertNotIn(INVENTED, text)

    def test_impact_refusals_get_the_forward_wording(self) -> None:
        text = rendered(refusal(mode=Mode.IMPACT), closure=closure("unmerged"))
        self.assertIn("Nothing downstream references it", text)
        self.assertNotIn("Decision to walk back to", text)


class TheLastWordTest(unittest.TestCase):
    """Comments arrived in #106, so the refusal can show one -- and must not explain it."""

    def test_it_quotes_what_was_said(self) -> None:
        text = rendered(refusal(), closure=closure(
            "not_planned", last_word=said("If you throw away the error, yes.")))
        self.assertIn("If you throw away the error, yes.", text)

    def test_it_names_the_association_and_the_date(self) -> None:
        # Both are how a reader weighs it. "A MEMBER said this in August" is a different
        # claim from an anonymous sentence sitting under a closure.
        text = rendered(refusal(), closure=closure("not_planned", last_word=said("no.")))
        self.assertIn("MEMBER", text)
        self.assertIn("2026-08-28", text)

    def test_it_refuses_to_call_it_the_reason(self) -> None:
        # The line this feature must not cross. Adjacency is not causation, and the graph
        # holds no edge saying any comment closed anything.
        text = " ".join(rendered(refusal(), closure=closure(
            "not_planned", last_word=said("no."))).split())
        self.assertIn("what was said, not why it closed", text)
        self.assertIn("records no link between the two", text)

    def test_it_says_standing_is_not_the_project_deciding(self) -> None:
        text = " ".join(rendered(refusal(), closure=closure(
            "not_planned", last_word=said("no."))).split())
        self.assertIn("not the same as the project deciding", text)

    def test_the_alternatives_survive(self) -> None:
        # #87 named rejection, supersession and abandonment as the things it cannot tell
        # apart. Showing a comment must not quietly drop that.
        text = rendered(refusal(), closure=closure("unmerged", last_word=said("no.")))
        for word in ("rejection", "supersession", "abandonment"):
            self.assertIn(word, text)

    def test_a_very_long_comment_is_clipped_rather_than_dumped(self) -> None:
        text = rendered(refusal(), closure=closure(
            "not_planned", last_word=said("word " * 400)))
        self.assertIn("...", text)
        self.assertLess(len(text.splitlines()), 40, "a comment took over the refusal")

    def test_internal_line_breaks_do_not_fight_the_layout(self) -> None:
        text = rendered(refusal(), closure=closure(
            "not_planned", last_word=said("first line\n\n```\ncode\n```\n\nlast line")))
        self.assertIn("first line", text)
        self.assertIn("last line", text)
        # Re-wrapped as one paragraph, so the comment's own fences do not appear as layout.
        self.assertNotIn("\n```\n", text)

    def test_without_a_comment_the_previous_wording_stands(self) -> None:
        # And it is true again in that case, which is the only reason it may stay.
        text = rendered(refusal(), closure=closure("not_planned"))
        self.assertIn("closing discussion is", text)
        self.assertIn("not ingested", text)

    def test_a_duplicate_gets_neither(self) -> None:
        # A duplicate's reason is its duplicate, which the closure line already said.
        text = rendered(refusal(), closure=closure("duplicate", last_word=said("dupe")))
        self.assertNotIn("The last word", text)
        self.assertNotIn("not ingested", text)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class LastWordLookupTest(unittest.TestCase):
    """Which comment is chosen, against the real schema. Rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self.repo = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('repository', %s) RETURNING id",
            (f"lw-repo-{id(self)}",),
        ).fetchone()["id"]
        self.issue = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) "
            "VALUES ('issue', %s, %s) RETURNING id",
            (f"lw-issue-{id(self)}", self.repo),
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO issue (node_id, node_type, number, state, state_reason, closed_at) "
            "VALUES (%s, 'issue', 9911, 'closed', 'not_planned', now())",
            (self.issue,),
        )
        self._seq = 0

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def comment(self, body: str, association: str, minutes: int) -> None:
        self._seq += 1
        node = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) "
            "VALUES ('comment', %s, %s) RETURNING id",
            (f"lw-c-{id(self)}-{self._seq}", self.repo),
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO comment (node_id, parent_node_id, body, author_association, created_at) "
            "VALUES (%s, %s, %s, %s, now() + make_interval(mins => %s))",
            (node, self.issue, body, association, minutes),
        )

    def test_the_reporter_withdrawing_is_not_the_last_word(self) -> None:
        # The real shape of flask#6120: a MEMBER says why, then the reporter says "ok, my
        # bad" and that is chronologically last. Showing it would present a withdrawal as
        # the project's position.
        self.comment("Do not submit a fix. Wait for the next release.", "MEMBER", 1)
        self.comment("ok, my bad", "NONE", 2)

        found = trace.closure_fact(self.conn, self.issue)

        self.assertIsNotNone(found.last_word)
        self.assertEqual(found.last_word.association, "MEMBER")
        self.assertIn("Do not submit a fix", found.last_word.body)

    def test_the_most_recent_standing_comment_wins(self) -> None:
        self.comment("first", "MEMBER", 1)
        self.comment("second", "OWNER", 2)
        self.assertEqual(trace.closure_fact(self.conn, self.issue).last_word.body, "second")

    def test_a_contributor_does_not_have_standing(self) -> None:
        # CONTRIBUTOR means "has had a pull request merged here", which is not standing to
        # close someone else's request.
        self.comment("I think this is wrong", "CONTRIBUTOR", 1)
        self.assertIsNone(trace.closure_fact(self.conn, self.issue).last_word)

    def test_an_empty_comment_is_not_the_last_word(self) -> None:
        self.comment("real reason here", "MEMBER", 1)
        self.comment("   ", "MEMBER", 2)
        self.assertEqual(
            trace.closure_fact(self.conn, self.issue).last_word.body, "real reason here"
        )

    def test_no_comments_leaves_it_absent(self) -> None:
        found = trace.closure_fact(self.conn, self.issue)
        self.assertIsNotNone(found)
        self.assertIsNone(found.last_word)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ClosureLookupTest(unittest.TestCase):
    """What `closure_fact` reads, against the real schema.

    Each test rolls back, so the fixtures never outlive the run.
    """

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._seq = 0

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def node(self, node_type: str, external_id: str) -> int:
        self._seq += 1
        row = self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) "
            "RETURNING id",
            (node_type, f"{external_id}-{id(self)}-{self._seq}", "fixture"),
        ).fetchone()
        return row["id"]

    def issue(self, state_reason: str) -> int:
        nid = self.node("issue", "9001")
        self.conn.execute(
            "INSERT INTO issue (node_id, node_type, number, state, state_reason, closed_at) "
            "VALUES (%s, 'issue', %s, 'closed', %s, now())",
            (nid, 9000 + self._seq, state_reason),
        )
        return nid

    def pr(self, *, state: str, merged: bool) -> int:
        nid = self.node("pull_request", "9002")
        self.conn.execute(
            "INSERT INTO pull_request (node_id, node_type, number, state, closed_at, merged_at) "
            "VALUES (%s, 'pull_request', %s, %s, now(), %s)",
            (nid, 9500 + self._seq, state, datetime.now(timezone.utc) if merged else None),
        )
        return nid

    def test_a_not_planned_issue_is_an_explaining_ending(self) -> None:
        found = trace.closure_fact(self.conn, self.issue("not_planned"))
        self.assertIsNotNone(found)
        self.assertEqual(found.kind, "not_planned")
        self.assertIsNotNone(found.closed_at)

    def test_a_duplicate_issue_is_too(self) -> None:
        self.assertEqual(trace.closure_fact(self.conn, self.issue("duplicate")).kind, "duplicate")

    def test_a_completed_issue_is_not(self) -> None:
        # It refuses for an ordinary reason -- no rubric-qualifying evidence in the thread.
        # Naming its closure would dress a plain miss up as an explanation.
        self.assertIsNone(trace.closure_fact(self.conn, self.issue("completed")))

    def test_an_unmerged_closed_pull_request_is(self) -> None:
        found = trace.closure_fact(self.conn, self.pr(state="closed", merged=False))
        self.assertEqual(found.kind, "unmerged")

    def test_a_merged_pull_request_is_not(self) -> None:
        self.assertIsNone(trace.closure_fact(self.conn, self.pr(state="closed", merged=True)))

    def test_an_open_pull_request_is_not(self) -> None:
        # It has not been refused; it has not been decided at all. Calling that an ending
        # is the same over-claim in the other direction.
        self.assertIsNone(trace.closure_fact(self.conn, self.pr(state="open", merged=False)))

    def test_a_commit_has_no_closure(self) -> None:
        self.assertIsNone(trace.closure_fact(self.conn, self.node("commit", "abc123")))


if __name__ == "__main__":
    unittest.main()
