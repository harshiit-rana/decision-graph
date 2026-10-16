"""Newest wins: an older payload never moves an artifact's state backwards (issue #120).

GitHub doesn't order webhook deliveries, retries failed ones, and lets a maintainer
redeliver any past one. Before this, every write applied whatever arrived last. A PR's
"opened" delivery replayed after its "merged" one set `merged_at` back to NULL, and the
next synthesis withdrew a correct Decision. The poller never repaired it, because its
UPDATED_ASC cursor doesn't revisit an artifact whose updated_at hasn't moved.

The DB-gated tests seed their own repository and roll back. CI's database is empty.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from decision_graph import db, extractors
from decision_graph.config import Settings

DSN = os.environ.get("DATABASE_URL")
REPO = "pallets/flask"
T1 = datetime(2026, 1, 20, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=5)


class IsStaleTest(unittest.TestCase):
    def test_older_payload_is_stale(self) -> None:
        self.assertTrue(db.is_stale(T1, T2))

    def test_equal_timestamps_apply_so_re_polling_is_a_no_op_that_succeeds(self) -> None:
        self.assertFalse(db.is_stale(T2, T2))

    def test_newer_payload_applies(self) -> None:
        self.assertFalse(db.is_stale(T2, T1))

    def test_a_payload_without_a_timestamp_counts_as_oldest(self) -> None:
        self.assertTrue(db.is_stale(None, T1))

    def test_nothing_stored_means_anything_applies(self) -> None:
        self.assertFalse(db.is_stale(None, None))
        self.assertFalse(db.is_stale(T1, None))


def pr_payload(number: int, *, updated: datetime, merged: bool, body: str = "") -> dict:
    """The issues-endpoint shape `extract_issue_or_pr` reads, which the webhook also feeds."""
    return {
        "number": number,
        "node_id": f"PR_{number}",
        "title": "merged title" if merged else "draft title",
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": "closed" if merged else "open",
        "body": body,
        "created_at": T1.isoformat(),
        "updated_at": updated.isoformat(),
        "closed_at": updated.isoformat() if merged else None,
        "user": None,
        "pull_request": {"merged_at": updated.isoformat() if merged else None},
    }


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class NewestWinsTest(unittest.TestCase):
    NUMBER = 990500

    def setUp(self) -> None:
        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        repo = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('repository', %s) RETURNING id",
            (f"fixture-repo-newest-{id(self)}",),
        ).fetchone()["id"]
        self.ctx = extractors.Context(
            conn=self.conn,
            client=None,  # type: ignore[arg-type]  # extract_issue_or_pr never calls GitHub
            settings=Settings(database_url=DSN, github_token="unused", target_repo=REPO),
            repo_node_id=repo,
        )

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def state(self, node_id: int) -> dict:
        row = self.conn.execute(
            """
            SELECT n.title, n.source_updated_at, p.state, p.merged_at, p.closed_at, p.body
              FROM node n JOIN pull_request p ON p.node_id = n.id WHERE n.id = %s
            """,
            (node_id,),
        ).fetchone()
        edges = self.conn.execute(
            "SELECT edge_type::text AS t, dst_node_id FROM edge "
            "WHERE src_node_id = %s AND valid_to IS NULL ORDER BY 1, 2",
            (node_id,),
        ).fetchall()
        return {**row, "edges": [tuple(e.values()) for e in edges]}

    def test_a_replayed_open_delivery_does_not_unmerge(self) -> None:
        # The exact regression: merged first, then the older "opened" delivery arrives.
        node_id = extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER, updated=T2, merged=True))
        extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER, updated=T1, merged=False))
        s = self.state(node_id)
        self.assertIsNotNone(s["merged_at"], "a stale delivery set merged_at back to NULL")
        self.assertEqual(s["state"], "closed")
        self.assertEqual(s["title"], "merged title")
        self.assertEqual(self.ctx.stats.skipped.get("stale_payload"), 1)

    def test_order_of_arrival_does_not_change_the_result(self) -> None:
        a = extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER, updated=T1, merged=False))
        extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER, updated=T2, merged=True))
        in_order = self.state(a)

        b = extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER + 1, updated=T2, merged=True))
        extractors.extract_issue_or_pr(self.ctx, pr_payload(self.NUMBER + 1, updated=T1, merged=False))
        reversed_ = self.state(b)

        for key in ("title", "state", "merged_at", "closed_at", "source_updated_at"):
            with self.subTest(field=key):
                self.assertEqual(in_order[key], reversed_[key])

    def test_re_polling_unchanged_state_is_a_no_op(self) -> None:
        payload = pr_payload(self.NUMBER, updated=T2, merged=True)
        node_id = extractors.extract_issue_or_pr(self.ctx, payload)
        before = self.state(node_id)
        extractors.extract_issue_or_pr(self.ctx, payload)
        self.assertEqual(self.state(node_id), before)
        self.assertNotIn("stale_payload", self.ctx.stats.skipped, "a tie must apply, not skip")

    def _issue(self, number: int) -> int:
        issue = self.conn.execute(
            "INSERT INTO node (node_type, external_id, repo_node_id) VALUES ('issue', %s, %s) "
            "RETURNING id",
            (str(number), self.ctx.repo_node_id),
        ).fetchone()["id"]
        self.conn.execute(
            "INSERT INTO issue (node_id, number, state) VALUES (%s, %s, 'open')", (issue, number)
        )
        return issue

    def test_a_fresh_body_does_add_the_reference(self) -> None:
        # The positive control. Without it, the test below would pass if closing keywords
        # simply never resolved in this fixture.
        issue = self._issue(990778)
        node_id = extractors.extract_issue_or_pr(
            self.ctx, pr_payload(self.NUMBER, updated=T2, merged=True, body="Fixes #990778")
        )
        self.assertIn(("closes", issue), self.state(node_id)["edges"])

    def test_a_stale_body_adds_no_reference(self) -> None:
        # The closing keyword was edited out in the newer version. Replaying the older body
        # must not put the edge back.
        issue = self._issue(990777)
        node_id = extractors.extract_issue_or_pr(
            self.ctx, pr_payload(self.NUMBER, updated=T2, merged=True, body="unrelated")
        )
        extractors.extract_issue_or_pr(
            self.ctx, pr_payload(self.NUMBER, updated=T1, merged=False, body="Fixes #990777")
        )
        self.assertNotIn(("closes", issue), self.state(node_id)["edges"])


if __name__ == "__main__":
    unittest.main()
