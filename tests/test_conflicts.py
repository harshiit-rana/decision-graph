"""Conflicting evidence is returned, never resolved (#175).

TraceMind's contract: when the graph holds evidence pointing two ways, the answer lists both
sides with their tiers, sources and timestamps, and names no winner. What's checked:

- every conflict type has a seeded database fixture, and the committed golden carries one of
  each type;
- **no field anywhere in a conflict names a preferred side**, checked over every type, in the
  data and in the schema;
- sides are ordered chronologically and neutrally;
- the text renderer shows a "Conflicting evidence" block before the trail, with the same
  sides, and nothing in it says which is right;
- an answer with no conflict has an empty list, so the absence is a statement.

The database fixtures seed their own rows and roll back; CI's database is empty.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from datetime import datetime, timezone

from decision_graph import conflicts, contract, query, render, retrieval

from tests.test_contract import CASES, SCHEMA, canned_conflicts, roundtrip, validate

DSN = os.environ.get("DATABASE_URL")
T1 = datetime(2026, 1, 20, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 25, tzinfo=timezone.utc)


def keys_of(value):
    """Every dict key anywhere in a value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys_of(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys_of(v)


class NoWinnerTest(unittest.TestCase):
    def test_no_key_in_any_conflict_names_a_preferred_side(self) -> None:
        for c in canned_conflicts():
            for key in keys_of(c):
                with self.subTest(type=c["type"], key=key):
                    for word in conflicts.FORBIDDEN_KEYS:
                        self.assertNotIn(word, key.lower())

    def test_the_schema_defines_no_such_property_either(self) -> None:
        names = set(keys_of(SCHEMA["$defs"]["conflict"])) | set(keys_of(SCHEMA["$defs"]["conflict_side"]))
        properties = {p for d in (SCHEMA["$defs"]["conflict"], SCHEMA["$defs"]["conflict_side"])
                      for p in d["properties"]}
        self.assertTrue(properties)
        for word in conflicts.FORBIDDEN_KEYS:
            self.assertFalse([p for p in properties if word in p.lower()], word)
        self.assertTrue(names)

    def test_the_fixed_sentences_name_no_winner(self) -> None:
        for kind, sentence in conflicts.DETAILS.items():
            with self.subTest(type=kind):
                for word in ("correct", "wins", "should use", "prefer the", "newer is", "older is"):
                    self.assertNotIn(word, sentence.lower())

    def test_sides_are_ordered_chronologically_and_not_by_tier(self) -> None:
        # The fixture gives the LATER side the stronger tier. It must still come second.
        multiple = next(c for c in canned_conflicts() if c["type"] == "multiple_implementers")
        observed = [s["edge"]["observed_at"] for s in multiple["sides"]]
        self.assertEqual(observed, sorted(observed))
        self.assertEqual([s["edge"]["evidence_tier"] for s in multiple["sides"]],
                         ["explicit", "corroborated"])

    def test_every_type_has_a_sentence_and_is_in_the_schema(self) -> None:
        schema_types = set(SCHEMA["$defs"]["conflict"]["properties"]["type"]["enum"])
        self.assertEqual(set(conflicts.TYPES), schema_types)
        self.assertEqual({c["type"] for c in canned_conflicts()}, schema_types)


class ContractTest(unittest.TestCase):
    def test_the_golden_carries_every_type_and_validates(self) -> None:
        doc = roundtrip(CASES["answer_conflicts"]())
        self.assertEqual(validate(doc, SCHEMA), [])
        found = {c["type"] for c in doc["results"][0]["conflicts"]}
        self.assertEqual(found, set(conflicts.TYPES))

    def test_an_answer_without_conflicts_says_so_with_an_empty_list(self) -> None:
        doc = roundtrip(CASES["answer_explicit"]())
        self.assertEqual(doc["results"][0]["conflicts"], [])

    def test_the_schema_requires_the_field(self) -> None:
        self.assertIn("conflicts", SCHEMA["$defs"]["result"]["required"])


class RendererTest(unittest.TestCase):
    def _text(self, conflicts_) -> str:
        from tests.test_contract import explicit_answer

        out = io.StringIO()
        render.render(explicit_answer(), conflicts=conflicts_, out=out)
        return out.getvalue()

    def test_the_block_comes_before_the_trail_and_lists_every_side(self) -> None:
        text = self._text(canned_conflicts())
        self.assertIn("Conflicting evidence", text)
        self.assertLess(text.index("Conflicting evidence"), text.index("Evidence trail"))
        self.assertIn("pull request #5867", text)
        self.assertIn("pull request #5898", text)
        self.assertIn("NOT MERGED", text)

    def test_the_heading_says_shown_not_resolved_and_names_no_winner(self) -> None:
        text = self._text(canned_conflicts())
        self.assertIn("shown, not resolved", text)
        # "none is preferred" is the guarantee itself; anything else naming a preference is not.
        text = text.lower().replace("none is preferred", "")
        self.assertNotRegex(text, r"\b(winner|preferred|the correct one|use the newer)\b")

    def test_no_block_when_there_is_no_conflict(self) -> None:
        self.assertNotIn("Conflicting evidence", self._text([]))
        self.assertNotIn("Conflicting evidence", self._text(None))


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class DetectionTest(unittest.TestCase):
    """One seeded fixture per conflict type. Each test rolls back."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self._n = 993000

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _node(self, node_type, suffix=""):
        self._n += 1
        return self.conn.execute(
            "INSERT INTO node (node_type, external_id, title) VALUES (%s, %s, %s) RETURNING id",
            (node_type, str(self._n), f"zz conflict {node_type}{suffix}")).fetchone()["id"], self._n

    def _issue(self, state_reason=None):
        nid, number = self._node("issue")
        self.conn.execute("INSERT INTO issue (node_id, number, state, state_reason) VALUES (%s, %s, "
                          "'open', %s)", (nid, number, state_reason))
        return nid, number

    def _decision(self):
        nid, _ = self._node("decision")
        self.conn.execute("INSERT INTO decision (node_id, status, summary) VALUES (%s, 'reconstructed', 'f')", (nid,))
        return nid

    def _pr(self, merged=True):
        nid, number = self._node("pull_request")
        self.conn.execute("INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (%s, %s, "
                          "'closed', %s)", (nid, number, T2 if merged else None))
        return nid

    def _edge(self, src, dst, edge_type, observed=T1):
        return self.conn.execute(
            "INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, "
            "valid_from, observed_at) VALUES (%s, %s, %s, 'explicit', 'explicit', 'test_fixture', "
            "now() - interval '1 day', %s) RETURNING id", (src, dst, edge_type, observed)).fetchone()["id"]

    def _detect(self, start_issue_number):
        c = retrieval.find_candidates(self.conn, f"#{start_issue_number}", limit=1)
        from decision_graph import reasoning

        answer = reasoning.reason(self.conn, c[0].node_id, reasoning.Mode.WHY, max_depth=3)
        return answer, conflicts.detect(self.conn, answer)

    def _thread(self, *, implementers=1, closers=0, issue_reason=None):
        issue, number = self._issue(issue_reason)
        decision = self._decision()
        self._edge(decision, issue, "motivated_by")
        prs = [self._pr() for _ in range(implementers)]
        for i, pr in enumerate(prs):
            self._edge(decision, pr, "implemented_by", observed=T1 if i == 0 else T2)
        closing = [self._pr() for _ in range(closers)]
        for pr in closing:
            self._edge(pr, issue, "closes")
        return issue, number, decision, prs, closing

    def test_a_clean_thread_has_no_conflict(self) -> None:
        _, number, *_ = self._thread()
        answer, found = self._detect(number)
        self.assertTrue(answer.found)
        self.assertEqual(found, [])

    def test_multiple_implementers_are_both_listed_in_recorded_order(self) -> None:
        _, number, decision, prs, _ = self._thread(implementers=2)
        _, found = self._detect(number)
        [c] = [x for x in found if x["type"] == "multiple_implementers"]
        self.assertEqual(c["subject"]["node_id"], decision)
        self.assertEqual([s["node"]["node_id"] for s in c["sides"]], prs)
        self.assertEqual(c["detail"], conflicts.DETAILS["multiple_implementers"])
        self.assertTrue(all(s["edge"]["evidence_tier"] == "explicit" for s in c["sides"]))
        self.assertEqual(validate_conflict(c), [])

    def test_two_merged_closing_prs_are_a_conflict(self) -> None:
        _, number, _, _, closing = self._thread(closers=2)
        _, found = self._detect(number)
        [c] = [x for x in found if x["type"] == "multiple_closing_prs"]
        self.assertEqual({s["node"]["node_id"] for s in c["sides"]}, set(closing))
        self.assertTrue(all(s["facts"]["merged"] for s in c["sides"]))

    def test_one_closing_pr_is_not(self) -> None:
        _, number, *_ = self._thread(closers=1)
        _, found = self._detect(number)
        self.assertFalse([x for x in found if x["type"] == "multiple_closing_prs"])

    def test_an_unmerged_closing_pr_does_not_count_as_a_second_closer(self) -> None:
        issue, number, *_ = self._thread(closers=1)
        self._edge(self._pr(merged=False), issue, "closes")
        _, found = self._detect(number)
        self.assertFalse([x for x in found if x["type"] == "multiple_closing_prs"])

    def test_a_reopened_issue_with_a_decision_is_a_conflict(self) -> None:
        _, number, decision, *_ = self._thread(issue_reason="reopened")
        _, found = self._detect(number)
        [c] = [x for x in found if x["type"] == "reopened"]
        self.assertEqual(c["sides"][0]["facts"]["state_reason"], "reopened")
        self.assertIn(decision, [s["node"]["node_id"] for s in c["sides"]])

    def test_an_implementer_that_never_merged_is_shown(self) -> None:
        issue, number = self._issue()
        decision = self._decision()
        self._edge(decision, issue, "motivated_by")
        pr = self._pr(merged=False)
        self._edge(decision, pr, "implemented_by")
        _, found = self._detect(number)
        [c] = [x for x in found if x["type"] == "implementer_not_merged"]
        self.assertFalse(c["sides"][0]["facts"]["merged"])

    def test_a_current_supersession_link_is_shown_and_an_ended_one_is_not(self) -> None:
        _, number, decision, *_ = self._thread()
        older = self._decision()
        edge = self._edge(decision, older, "supersedes", observed=T2)
        _, found = self._detect(number)
        [c] = [x for x in found if x["type"] == "superseded"]
        self.assertEqual({s["node"]["node_id"] for s in c["sides"]}, {decision, older})
        self.conn.execute("UPDATE edge SET valid_to = now() WHERE id = %s", (edge,))
        _, found = self._detect(number)
        self.assertFalse([x for x in found if x["type"] == "superseded"])

    def test_end_to_end_the_document_carries_them_and_validates(self) -> None:
        _, number, *_ = self._thread(implementers=2)
        c = retrieval.find_candidates(self.conn, f"#{number}", limit=1)
        doc = roundtrip(query.answer_document(self.conn, query=f"#{number}", mode="why",
                                              candidates=c, as_of=None, max_depth=3))
        self.assertEqual(validate(doc, SCHEMA), [])
        self.assertIn("multiple_implementers", {x["type"] for x in doc["results"][0]["conflicts"]})
        self.assertFalse({w for w in conflicts.FORBIDDEN_KEYS
                          for k in keys_of(doc["results"][0]["conflicts"]) if w in k.lower()})

    def test_the_text_output_shows_the_block(self) -> None:
        _, number, *_ = self._thread(implementers=2)
        answer, found = self._detect(number)
        out = io.StringIO()
        render.render(answer, conflicts=found, out=out)
        self.assertIn("Conflicting evidence", out.getvalue())


def validate_conflict(c) -> list:
    # Nodes inside a conflict are labelled with their author by the same pass that labels
    # everything else (`finish_from_db`), so label before validating a bare conflict.
    labelled = json.loads(contract.dumps(c))
    contract.label_authors(labelled, {})
    return validate(labelled, {"$ref": "#/$defs/conflict"})


if __name__ == "__main__":
    unittest.main()
