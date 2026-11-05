"""A NUL in repository text must not stop ingestion (found by the adversarial corpus, #145).

PostgreSQL cannot store U+0000 in `text` or `jsonb`. Before `db.storable`, one in an issue title
made the INSERT raise, and since the poller meets the same payload on every run, anyone who could
post on a tracked repository could stop `dg ingest` for good.
"""

from __future__ import annotations

import os
import unittest

from decision_graph import db

DSN = os.environ.get("DATABASE_URL")
NUL = chr(0)


class StorableTest(unittest.TestCase):
    def test_nul_is_removed_from_strings_keys_and_nested_values(self) -> None:
        value = {"a" + NUL: ["x" + NUL + "y", {"k": "z" + NUL}], "n": 3, "none": None, "t": True}
        self.assertEqual(db.storable(value), {"a": ["xy", {"k": "z"}], "n": 3, "none": None, "t": True})

    def test_everything_else_is_untouched(self) -> None:
        text = "plain ‮ bidi \x1b[31m escape ​ zero-width \n newline"
        self.assertEqual(db.storable(text), text, "only NUL is removed; other characters are the renderers' job")
        self.assertIsNone(db.storable(None))
        self.assertEqual(db.storable(7), 7)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class NodeWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = db.connect(DSN)

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def test_a_node_whose_title_and_payload_carry_nul_is_stored_without_it(self) -> None:
        node_id = db.upsert_node(
            self.conn, node_type="issue", external_id="zz-nul-1", title="bad" + NUL + "title",
            raw={"body": "hi" + NUL + "there", "nested": {"x" + NUL: ["a" + NUL]}})
        row = self.conn.execute("SELECT title, raw FROM node WHERE id = %s", (node_id,)).fetchone()
        self.assertEqual(row["title"], "badtitle")
        self.assertEqual(row["raw"], {"body": "hithere", "nested": {"x": ["a"]}})

    def test_a_detail_row_with_nul_is_stored_without_it(self) -> None:
        node_id = db.upsert_node(self.conn, node_type="issue", external_id="zz-nul-2", title="t")
        db.upsert_detail(self.conn, "issue", node_id, number=990001, state="open", body="b" + NUL + "ody")
        row = self.conn.execute("SELECT body FROM issue WHERE node_id = %s", (node_id,)).fetchone()
        self.assertEqual(row["body"], "body")


if __name__ == "__main__":
    unittest.main()
