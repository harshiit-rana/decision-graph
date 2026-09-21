"""Comments are ingested as artifacts and must stay artifacts (issue #106).

#87 had to write, in every refusal about declined work, that "the closing discussion is not
ingested". This is what makes that stop being permanent -- and the reason it stops at
artifacts is in the PRD roadmap note: asserting a `rejected` outcome without
rejection-rationale extraction "would reintroduce exactly the failure §5.1 exists to
prevent". The sampled comments are the argument. From flask, now in the graph:

    "ok, my bad"                    author_association NONE     -- the reporter withdrawing
    "Duplicate of ..."              author_association MEMBER    -- triage
    "If you throw away the error"   author_association MEMBER    -- an actual reason

So the prose is stored and the reading is left to whoever is looking. `author_association`
is stored for the same reason `state_reason` is: it is the one structured signal that
separates a maintainer's decision from a bystander's opinion, and storing it is not the same
as acting on it.

The schema half of "stays an artifact" is db/tests/0015_comment_checks.sql. This is the
extractor half.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from typing import Any

from decision_graph import extractors

DSN = os.environ.get("DATABASE_URL")


class CommentTitleTest(unittest.TestCase):
    """A comment has no title, and `trace.ref` would otherwise show a bare id."""

    def test_the_first_line_becomes_the_label(self) -> None:
        self.assertEqual(extractors._comment_title("Duplicate of #2832\n\nsearch first"),
                         "Duplicate of #2832")

    def test_a_long_line_is_clipped_with_an_ellipsis(self) -> None:
        label = extractors._comment_title("x" * 200)
        self.assertLessEqual(len(label), extractors._COMMENT_TITLE_CHARS)
        self.assertTrue(label.endswith("…"))

    def test_a_short_line_is_left_alone(self) -> None:
        self.assertEqual(extractors._comment_title("ok, my bad"), "ok, my bad")

    def test_an_empty_body_says_so_rather_than_rendering_blank(self) -> None:
        # A blank title makes the node unidentifiable in a trace, which is worse than
        # saying there is nothing to show.
        for body in ("", "   ", "\n\n", None):
            with self.subTest(body=body):
                self.assertEqual(extractors._comment_title(body), "(empty comment)")

    def test_leading_blank_lines_are_skipped(self) -> None:
        self.assertEqual(extractors._comment_title("\n\n  real text  "), "real text")


@dataclass
class _Page:
    items: list[dict]


class _StubClient:
    """Returns canned pages. The extractor is what is under test, not HTTP."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = pages
        self.paths: list[str] = []

    def paginate(self, path: str, **_: Any):
        self.paths.append(path)
        for page in self._pages:
            yield _Page(items=page)


def _comment(cid: int, body: str, *, login: str = "someone",
             association: str = "NONE") -> dict:
    return {
        "id": cid,
        "node_id": f"IC_{cid}",
        "body": body,
        "html_url": f"https://github.com/pallets/flask/issues/1#issuecomment-{cid}",
        "created_at": "2026-08-28T17:13:03Z",
        "updated_at": "2026-08-28T17:13:03Z",
        "author_association": association,
        "user": {"login": login, "id": 1, "html_url": f"https://github.com/{login}"},
    }


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class ExtractCommentsTest(unittest.TestCase):
    """Against the real schema; every test rolls back."""

    def setUp(self) -> None:
        from decision_graph import db
        from decision_graph.config import Settings

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self.settings = Settings(
            database_url=DSN, github_token="x", target_repo="pallets/flask"
        )
        self.repo = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('repository', %s) RETURNING id",
            (f"fixture-repo-{id(self)}",),
        ).fetchone()["id"]
        self.parent = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) "
            "VALUES ('issue', %s, %s) RETURNING id",
            (f"fixture-issue-{id(self)}", self.repo),
        ).fetchone()["id"]

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def ctx(self, pages: list[list[dict]]) -> extractors.Context:
        self.client = _StubClient(pages)
        return extractors.Context(
            conn=self.conn, client=self.client, settings=self.settings,
            repo_node_id=self.repo,
        )

    def test_a_comment_becomes_a_node_and_a_row(self) -> None:
        ctx = self.ctx([[_comment(1, "ok, my bad")]])

        stored = extractors.extract_comments(ctx, 6120, self.parent)

        self.assertEqual(stored, 1)
        row = self.conn.execute(
            "SELECT c.body, c.parent_node_id, c.author_association, n.title, n.node_type::text t "
            "FROM comment c JOIN node n ON n.id = c.node_id WHERE c.parent_node_id = %s",
            (self.parent,),
        ).fetchone()
        self.assertEqual(row["body"], "ok, my bad")
        self.assertEqual(row["parent_node_id"], self.parent)
        self.assertEqual(row["t"], "comment")
        self.assertEqual(row["title"], "ok, my bad")

    def test_the_association_is_stored_verbatim(self) -> None:
        # The difference between a maintainer closing a request and the reporter
        # withdrawing it. Stored, not interpreted.
        ctx = self.ctx([[_comment(2, "Duplicate of #2832", association="MEMBER")]])
        extractors.extract_comments(ctx, 6114, self.parent)
        self.assertEqual(
            self.conn.execute(
                "SELECT author_association a FROM comment WHERE parent_node_id = %s",
                (self.parent,),
            ).fetchone()["a"],
            "MEMBER",
        )

    def test_the_author_gets_a_created_edge(self) -> None:
        ctx = self.ctx([[_comment(3, "text", login="davidism")]])
        extractors.extract_comments(ctx, 1, self.parent)
        found = self.conn.execute(
            "SELECT count(*) n FROM edge e JOIN node s ON s.id = e.src_node_id "
            "WHERE e.edge_type = 'created' AND e.extractor = 'issue_comment' "
            "AND s.node_type = 'person'"
        ).fetchone()["n"]
        self.assertGreaterEqual(found, 1)

    def test_the_artifact_points_at_the_discussion_not_the_reverse(self) -> None:
        # `render.phrase` reads discussed_in outward as "was discussed in", so the edge has
        # to run artifact -> comment for a Why-walk to travel from what you asked about
        # toward what was said about it.
        ctx = self.ctx([[_comment(4, "text")]])
        extractors.extract_comments(ctx, 1, self.parent)
        row = self.conn.execute(
            "SELECT s.node_type::text src, d.node_type::text dst FROM edge e "
            "JOIN node s ON s.id = e.src_node_id JOIN node d ON d.id = e.dst_node_id "
            "WHERE e.edge_type = 'discussed_in' AND e.extractor = 'issue_comment'"
        ).fetchone()
        self.assertEqual((row["src"], row["dst"]), ("issue", "comment"))

    def test_re_running_stores_no_duplicate(self) -> None:
        # Comments are re-fetched every time the issues cursor surfaces their artifact, so
        # a non-idempotent upsert would grow the graph on every run.
        pages = [[_comment(5, "text")]]
        extractors.extract_comments(self.ctx(pages), 1, self.parent)
        extractors.extract_comments(self.ctx(pages), 1, self.parent)
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) n FROM comment WHERE parent_node_id = %s", (self.parent,)
            ).fetchone()["n"],
            1,
        )

    def test_a_comment_with_no_id_is_skipped_and_counted(self) -> None:
        # No stable identity means no idempotent upsert, and a row that re-inserts every
        # run is worse than an absent one. Counted rather than dropped silently.
        ctx = self.ctx([[{"body": "no id here", "user": {"login": "x"}}]])
        stored = extractors.extract_comments(ctx, 1, self.parent)
        self.assertEqual(stored, 0)
        self.assertIn("comment_without_id", ctx.stats.skipped)

    def test_it_reads_the_issues_endpoint_for_pull_requests_too(self) -> None:
        # GitHub models a pull request as an issue for discussion, so there is one path
        # rather than two, and the caller does not branch on which it has.
        ctx = self.ctx([[_comment(6, "text")]])
        extractors.extract_comments(ctx, 5898, self.parent)
        self.assertEqual(self.client.paths, ["/repos/pallets/flask/issues/5898/comments"])

    def test_discussion_does_not_turn_a_refusal_into_an_answer(self) -> None:
        """The one that would undo #87 if it broke.

        `discussed_in` is in WHY_EDGES, so a Why-walk really does traverse into comments.
        What stops a comment answering "why" is that the walk is looking for a Decision and
        a comment is not one. That is a property of the terminal condition, not of the edge
        set, so it holds only as long as nobody loosens it -- and if it ever did loosen,
        every declined artifact would start answering with whatever someone last said on
        it, which is precisely the unevidenced assertion this feature refused to make.

        Measured against the live graph when comments were first ingested: 263 artifacts
        refused before and 263 after, unchanged.
        """
        from decision_graph import reasoning
        from decision_graph.reasoning import Mode

        before = reasoning.reason(self.conn, self.parent, Mode.WHY, max_depth=3)
        self.assertFalse(before.found, "fixture should refuse before discussion exists")

        ctx = self.ctx([[_comment(9, "we are not going to do this", association="MEMBER")]])
        extractors.extract_comments(ctx, 1, self.parent)

        after = reasoning.reason(self.conn, self.parent, Mode.WHY, max_depth=3)
        self.assertFalse(
            after.found,
            "a comment answered a Why-walk; discussion is not evidence of a decision",
        )

    def test_every_page_is_consumed(self) -> None:
        ctx = self.ctx([[_comment(7, "one")], [_comment(8, "two")]])
        self.assertEqual(extractors.extract_comments(ctx, 1, self.parent), 2)


if __name__ == "__main__":
    unittest.main()
