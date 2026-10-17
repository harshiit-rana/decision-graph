"""`--as-of` behaves as the README now says it does (issue #124).

README and PRD described `--as-of` as "what the graph knew at a point in time", and the §9
temporal control was cited as proof the filter works. The dates are not all event times:

- A Decision's own edges are dated by `decided_at`, the implementing PR's merge time. That is
  a real event time.
- An edge extracted from TEXT is dated by the artifact's `updated_at` when it was first
  ingested. A reference written at creation but ingested after later edits carries a LATER
  date, so `--as-of` between creation and that update can miss an edge that already existed.
  The error runs toward refusal, never toward a false answer.

The fixture is one pull request created in January, merged in February, and last updated in
March (a comment, say), closing an issue. It is the case the issue names: created_at <
updated_at. DATABASE_URL-gated; seeds its own repository and rolls back.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

DSN = os.environ.get("DATABASE_URL")
NOW = datetime.now(timezone.utc)
JAN = NOW - timedelta(days=90)     # the PR is opened, with `Fixes #N` in its body
FEB = NOW - timedelta(days=60)     # the PR is merged
MAR = NOW - timedelta(days=30)     # a late comment bumps updated_at, before we ingest it
DAY = timedelta(days=1)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class AsOfSemanticsTest(unittest.TestCase):
    ISSUE, PR = 995001, 995002

    def setUp(self) -> None:
        from decision_graph import db, extractors, synthesis
        from decision_graph.config import Settings

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        repo = self.conn.execute(
            "INSERT INTO node (node_type, external_id) VALUES ('repository', %s) RETURNING id",
            (f"zz-asof-{id(self)}",)).fetchone()["id"]
        self.repo = repo
        ctx = extractors.Context(
            conn=self.conn, client=None,  # type: ignore[arg-type]
            settings=Settings(database_url=DSN, github_token="x", target_repo="pallets/flask"),
            repo_node_id=repo)
        issue = {"number": self.ISSUE, "node_id": "I", "title": "zz as-of issue", "html_url": "https://x/i",
                 "state": "closed", "body": "", "created_at": JAN.isoformat(), "updated_at": FEB.isoformat(),
                 "closed_at": FEB.isoformat(), "user": None}
        pr = {"number": self.PR, "node_id": "P", "title": "zz as-of pr", "html_url": "https://x/p",
              "state": "closed", "body": f"Fixes #{self.ISSUE}", "created_at": JAN.isoformat(),
              "updated_at": MAR.isoformat(), "closed_at": FEB.isoformat(), "user": None,
              "pull_request": {"merged_at": FEB.isoformat()}}
        self.issue_id = extractors.extract_issue_or_pr(ctx, issue)
        self.pr_id = extractors.extract_issue_or_pr(ctx, pr)
        synthesis.synthesize(self.conn, repo)

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def edge(self, edge_type: str):
        return self.conn.execute(
            "SELECT valid_from, observed_at FROM edge WHERE edge_type = %s AND (src_node_id = %s "
            "OR dst_node_id = %s OR src_node_id = %s OR dst_node_id = %s) AND valid_to IS NULL",
            (edge_type, self.pr_id, self.pr_id, self.issue_id, self.issue_id)).fetchone()

    def why(self, as_of):
        from decision_graph import reasoning

        return reasoning.reason(self.conn, self.issue_id, reasoning.Mode.WHY, as_of=as_of)

    def impact_reaches_the_issue(self, as_of) -> bool:
        from decision_graph import reasoning

        answer = reasoning.reason(self.conn, self.pr_id, reasoning.Mode.IMPACT, as_of=as_of)
        return any(self.issue_id in p.node_ids for p in answer.paths)

    # -- the two kinds of date ------------------------------------------------------------

    def test_a_decisions_edges_are_dated_by_the_merge_not_by_the_late_update(self) -> None:
        motivated = self.edge("motivated_by")
        self.assertEqual(motivated["valid_from"], FEB, "decided_at: the PR's merge time")

    def test_a_text_extracted_edge_is_dated_by_updated_at_at_first_ingestion(self) -> None:
        closes = self.edge("closes")
        self.assertEqual(closes["valid_from"], MAR, "the artifact's updated_at, not when it was written")
        self.assertLess(JAN, MAR, "the fixture is the created_at < updated_at case")

    # -- what --as-of therefore does ------------------------------------------------------

    def test_why_is_answered_from_the_merge_date_onwards(self) -> None:
        self.assertFalse(self.why(FEB - DAY).found, "before the merge: nothing had been decided")
        self.assertTrue(self.why(FEB + DAY).found, "after the merge, though the closes edge is dated later")

    def test_impact_over_a_text_edge_misses_it_between_creation_and_the_late_update(self) -> None:
        # The documented limitation. The keyword was in the body from January, but the edge is
        # dated March, so an --as-of in between does not see it. Toward refusal, never a false answer.
        self.assertFalse(self.impact_reaches_the_issue(FEB + DAY), "between merge and the late update")
        self.assertTrue(self.impact_reaches_the_issue(MAR + DAY), "after the edge's first-observed date")

    def test_the_error_is_toward_refusal_never_a_false_answer(self) -> None:
        # Nothing is visible at any time that the repository had not already said: as-of before
        # the PR existed shows neither the edge nor the Decision.
        self.assertFalse(self.impact_reaches_the_issue(JAN - DAY))
        self.assertFalse(self.why(JAN - DAY).found)

    def test_the_documentation_states_the_semantics_and_the_gap(self) -> None:
        from pathlib import Path

        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        text = " ".join(readme.split())  # line wrapping must not decide whether a phrase is found
        for needle in ("first observed", "decided_at", "toward refusal", "timeline API"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
