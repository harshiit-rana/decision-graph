"""Provenance: what was asked, which engine answered, and exactly what it read (#176).

An answer that can't be replayed can't be diffed. These check that the block is present and
schema-valid in all three document kinds, that two runs over an unchanged database produce
identical provenance, that a migration bump changes `engine`, and that no credential or DSN
can reach it.

The determinism and migration tests need a database. They seed their own rows and roll back.
"""

from __future__ import annotations

import json
import os
import unittest

from decision_graph import contract, query, retrieval

from tests.test_contract import (
    FIXTURE_ENGINE, SCHEMA, candidate, doc_for, explicit_answer, explicit_facts, refused,
    roundtrip, validate,
)

DSN = os.environ.get("DATABASE_URL")


class PresenceTest(unittest.TestCase):
    def test_every_document_kind_carries_a_valid_block(self) -> None:
        doc = roundtrip(doc_for(candidate(), explicit_answer(), explicit_facts()))
        self.assertEqual(validate(doc, SCHEMA), [])
        block = doc["provenance"]
        self.assertEqual(set(block), {"request", "engine", "used"})
        self.assertEqual(block["engine"], FIXTURE_ENGINE)
        self.assertIn("nodes", block["used"])

    def test_the_schema_requires_it(self) -> None:
        for name in ("answer_document", "attempts_document", "context_document"):
            with self.subTest(document=name):
                self.assertIn("provenance", SCHEMA["$defs"][name]["required"])

    def test_a_document_without_it_fails_validation(self) -> None:
        doc = roundtrip(doc_for(candidate(), refused(), explicit_facts()))
        del doc["provenance"]
        self.assertTrue(validate(doc, SCHEMA))

    def test_the_block_holds_no_timestamp_of_its_own(self) -> None:
        # Two runs over an unchanged database must be identical, so the block can't say when
        # it was built. The per-edge times it lists are the graph's, not the run's.
        keys = set()

        def walk(v):
            if isinstance(v, dict):
                keys.update(v)
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        walk(doc_for(candidate(), explicit_answer(), explicit_facts())["provenance"])
        self.assertFalse({"generated_at", "run_at", "now", "timestamp"} & keys)


class NoSecretsTest(unittest.TestCase):
    def test_no_dsn_or_credential_can_reach_the_block(self) -> None:
        saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "GITHUB_TOKEN", "NVIDIA_API_KEY")}
        os.environ.update({"DATABASE_URL": "postgresql://postgres:hunter2@db:5432/dg",
                           "GITHUB_TOKEN": "ghp_SECRET_TOKEN_VALUE",
                           "NVIDIA_API_KEY": "nvapi-SECRET-KEY-VALUE"})
        try:
            text = contract.dumps(doc_for(candidate(), explicit_answer(), explicit_facts()))
            for forbidden in ("postgresql://", "hunter2", "ghp_SECRET", "nvapi-SECRET", "DATABASE_URL"):
                self.assertNotIn(forbidden, text)
            self.assertNotIn("postgresql://", json.dumps(contract.engine_facts.__doc__ or ""))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_request_is_built_from_query_arguments_only(self) -> None:
        # The callers pass literals from their parsed arguments. This pins the key sets, so a
        # future caller adding an environment-derived field fails here.
        import inspect

        for fn in (query.answer_document,):
            src = inspect.getsource(fn)
            self.assertNotIn("os.environ", src)
        self.assertEqual(
            set(roundtrip(doc_for(candidate(), refused(), explicit_facts()))["provenance"]["request"]),
            {"command"},
        )

    def test_package_version_has_a_fallback_for_an_uninstalled_checkout(self) -> None:
        self.assertRegex(contract.package_version(), r"^\d+\.\d+\.\d+")


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class AgainstTheDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        ids = {}
        for kind, ext in (("issue", "992001"), ("decision", "prov-dec"), ("pull_request", "992002")):
            ids[kind] = self.conn.execute(
                "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, 'zz provenance fixture') "
                "RETURNING id", (kind, ext)).fetchone()["id"]
        self.conn.execute("INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 'f')",
                          (ids["decision"],))
        self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) "
                          "VALUES (%s, 992002, 'closed', now())", (ids["pull_request"],))
        self.edge_ids = []
        for src, dst, et in ((ids["decision"], ids["issue"], "motivated_by"),
                             (ids["decision"], ids["pull_request"], "implemented_by")):
            self.edge_ids.append(self.conn.execute(
                "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, "
                "valid_from, observed_at) VALUES (%s, %s, %s, 'explicit', 'explicit', 'test_fixture', "
                "now() - interval '1 day', now() - interval '2 days') RETURNING id",
                (src, dst, et)).fetchone()["id"])

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _doc(self) -> dict:
        c = retrieval.find_candidates(self.conn, "#992001", limit=1)
        return roundtrip(query.answer_document(
            self.conn, query="#992001", mode="why", candidates=c, as_of=None, max_depth=3))

    def test_two_runs_over_an_unchanged_database_have_identical_provenance(self) -> None:
        first, second = self._doc(), self._doc()
        self.assertEqual(validate(first, SCHEMA), [])
        self.assertEqual(first["provenance"], second["provenance"])
        self.assertEqual(first, second, "the whole document should be stable too")

    def test_it_lists_exactly_the_edges_the_paths_crossed_with_their_times(self) -> None:
        used = self._doc()["provenance"]["used"]
        self.assertTrue(set(e["edge_id"] for e in used["edges"]) <= set(self.edge_ids))
        self.assertTrue(used["edges"])
        for e in used["edges"]:
            self.assertEqual(e["evidence_tier"], "explicit")
            self.assertIsNotNone(e["observed_at"])
            self.assertIsNotNone(e["created_at"])

    def test_the_request_records_the_arguments(self) -> None:
        request = self._doc()["provenance"]["request"]
        self.assertEqual((request["command"], request["query"], request["mode"], request["max_depth"]),
                         ("query", "#992001", "why", 3))

    def test_a_migration_bump_changes_the_engine_block(self) -> None:
        before = self._doc()["provenance"]["engine"]["migrations"]
        self.conn.execute(
            "INSERT INTO schema_migration (filename, checksum) VALUES ('9999_provenance_probe.sql', 'abc')")
        after = self._doc()["provenance"]["engine"]["migrations"]
        self.assertEqual(after["applied"], before["applied"] + 1)
        self.assertEqual(after["last"], "9999_provenance_probe.sql")
        self.assertNotEqual(after["ledger_sha256"], before["ledger_sha256"])

    def test_a_changed_checksum_changes_the_ledger_digest(self) -> None:
        before = self._doc()["provenance"]["engine"]["migrations"]["ledger_sha256"]
        self.conn.execute(
            "UPDATE schema_migration SET checksum = 'tampered' WHERE filename = "
            "(SELECT min(filename) FROM schema_migration)")
        self.assertNotEqual(self._doc()["provenance"]["engine"]["migrations"]["ledger_sha256"], before)


if __name__ == "__main__":
    unittest.main()
