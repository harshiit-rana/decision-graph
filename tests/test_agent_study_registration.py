"""The agent study's pre-registration is internally consistent and reproducible (#140).

No database and no network. These check the committed files against each other, so the
registration can't quietly lose a property:

- the registered protocol, tasks, census and pool are pinned by hash, so editing any of them
  appears in this file's diff;
- the census reproduces from the committed pool by the committed code;
- every task is a cluster in the census, its ground truth cites checkable GitHub URLs, and each
  artifact it requires the agent to cite is one its own history section describes;
- `as_of` is before the decision point and the item was filed before it;
- the significance claims in the protocol are the ones an exact sign test gives.

What this cannot check is the property that makes it a pre-registration: that the commit is
earlier than any agent run. That is the git history, recorded in the issue.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import unittest
from datetime import datetime
from math import comb
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "eval" / "agent_study"


def load_select():
    spec = importlib.util.spec_from_file_location("agent_select_tasks", DIR / "select_tasks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _read(name: str) -> str:
    return (DIR / name).read_text(encoding="utf-8")


MARKER = "\n## Annotations\n"  # the heading line, not the phrase quoted in the protocol's own intro

TASKS = json.loads(_read("tasks.json"))["tasks"]
DRAW = json.loads(_read("draw.json"))
POOL = json.loads(_read("pool.json"))

# The registered content, pinned. Editing the protocol above `## Annotations`, the task set, the
# census or the pool changes a hash, so the edit has to appear in this file's diff. Annotations go
# below the marker and do not affect the first hash.
REGISTERED = {
    "PROTOCOL.md (above ## Annotations)": "147e8ad2c4951a29ce3024f23c1c438851c3098112e2d7c8f71a555355d577e8",
    "tasks.json": "1ffca092d3c3266747f74b258f6646feceb2cd5c72172d13e92d26414997cba1",
    "draw.json": "1518299c9e2130ac5031b10c345824d18cca8c2d9e0a5326390454fb7437293d",
    "pool.json": "44a79e29dc1965d50568c6f13ea29f4ba289647315945f7ac2d6f49d0987457d",
}


class RegistrationIsImmutableTest(unittest.TestCase):
    def test_the_registered_protocol_is_unchanged(self) -> None:
        text = _read("PROTOCOL.md").replace("\r\n", "\n")
        self.assertEqual(_digest(text[:text.index(MARKER)]), REGISTERED["PROTOCOL.md (above ## Annotations)"],
                         "the registered protocol was edited; annotate it below the marker instead")

    def test_the_tasks_census_and_pool_are_unchanged(self) -> None:
        for name in ("tasks.json", "draw.json", "pool.json"):
            with self.subTest(file=name):
                self.assertEqual(_digest(_read(name)), REGISTERED[name], f"{name} was edited after registration")

    def test_annotations_exist_only_below_the_marker(self) -> None:
        self.assertEqual(_read("PROTOCOL.md").replace("\r\n", "\n").count(MARKER), 1)


class CensusTest(unittest.TestCase):
    def test_the_census_reproduces_from_the_committed_pool(self) -> None:
        sel = load_select()
        pool = json.loads(_read("pool.json"))
        for repo, (lo, _) in sel.TASK_WINDOWS.items():
            p = pool["repos"][repo]
            p["verdicts"] = [sel.judge(i, repo, lo, p["linked_info"]) for i in p["issues"]]
        self.assertEqual(sel.draw(pool), DRAW)

    def test_no_precedent_in_the_census_was_merged_before_its_decision_point_alone(self) -> None:
        # The rule that keeps a task from being answerable by reading the checkout.
        for repo, r in DRAW["repos"].items():
            for n in r["eligible_numbers"]:
                item = {i["number"]: i for i in POOL["repos"][repo]["issues"]}[n]
                v = load_select().judge(item, repo, load_select().TASK_WINDOWS[repo][0], POOL["repos"][repo]["linked_info"])
                with self.subTest(repo=repo, item=n):
                    self.assertTrue(any(not p["merged_before_decision_point"] for p in v["precedents"]))

    def test_the_junk_filter_never_removes_an_item_that_has_no_marker(self) -> None:
        sel = load_select()
        for repo, p in POOL["repos"].items():
            by = {i["number"]: i for i in p["issues"]}
            for v in (sel.judge(i, repo, sel.TASK_WINDOWS[repo][0], p["linked_info"]) for i in p["issues"]):
                if v["reason"] == "maintainer comment marks it junk/spam/machine-generated":
                    text = " ".join(c["body"].lower() for c in by[v["number"]]["comments"]["nodes"]
                                    if c["authorAssociation"] in sel.MAINTAINER)
                    self.assertTrue(any(m in text for m in sel.JUNK_MARKERS), v["number"])


class TaskSetTest(unittest.TestCase):
    def test_there_are_nine_tasks_with_distinct_ids_and_the_protocol_says_nine(self) -> None:
        self.assertEqual(len(TASKS), 9)
        self.assertEqual(len({t["id"] for t in TASKS}), 9)
        self.assertIn("That leaves 9 tasks. The issue asked for at least 12", _read("PROTOCOL.md"))

    def test_every_task_is_a_cluster_in_the_census(self) -> None:
        for t in TASKS:
            clusters = {c["task_item"] for c in DRAW["repos"][t["repo"]]["clusters"]}
            with self.subTest(task=t["id"]):
                self.assertIn(t["item"]["number"], clusters)

    def test_tasks_are_unique_per_history_not_per_proposal(self) -> None:
        also = {(t["repo"], n) for t in TASKS for n in t["selection"]["census_cluster"]["also_reproposed_in"]}
        self.assertFalse(also & {(t["repo"], t["item"]["number"]) for t in TASKS})

    def test_the_agent_sees_the_text_as_filed_and_nothing_after_as_of(self) -> None:
        for t in TASKS:
            with self.subTest(task=t["id"]):
                self.assertFalse(t["item"]["edited_since_filing"])
                filed = datetime.fromisoformat(t["item"]["filed_at"].replace("Z", "+00:00"))
                as_of = datetime.fromisoformat(t["as_of"].replace("Z", "+00:00"))
                point = datetime.fromisoformat(t["decision_point"]["at"].replace("Z", "+00:00"))
                self.assertLess(filed, as_of)
                self.assertLess(as_of, point)
                self.assertEqual((point - as_of).total_seconds(), 1)

    def test_ground_truth_has_every_field_and_checkable_urls(self) -> None:
        for t in TASKS:
            g = t["ground_truth"]
            with self.subTest(task=t["id"]):
                for key in ("outcome", "outcome_url", "history", "contradicted_claims", "must_cite", "history_silent_on"):
                    self.assertIn(key, g)
                self.assertTrue(g["contradicted_claims"] and g["history"] and g["must_cite"])
                self.assertRegex(g["outcome_url"], rf"^https://github\.com/{re.escape(t['repo'])}/pull/{t['item']['number']}#issuecomment-\d+$")
                for h in g["history"]:
                    self.assertRegex(h["url"], r"^https://github\.com/[\w.-]+/[\w.-]+/(pull|issues)/\d+$")
                    self.assertTrue(h["records"].strip() and h["state_at_as_of"].strip())

    def test_every_artifact_the_plan_must_cite_is_described_in_the_history(self) -> None:
        for t in TASKS:
            described = {h["artifact"] for h in t["ground_truth"]["history"]}
            with self.subTest(task=t["id"]):
                self.assertTrue(set(t["ground_truth"]["must_cite"]) <= described)

    def test_the_decision_point_is_a_comment_on_the_task_item_itself(self) -> None:
        for t in TASKS:
            with self.subTest(task=t["id"]):
                self.assertRegex(t["decision_point"]["url"],
                                 rf"/pull/{t['item']['number']}#issuecomment-\d+$")
                self.assertTrue(t["selection"]["precedents"])

    def test_the_metric_m3_applies_exactly_to_tasks_with_a_silent_history(self) -> None:
        silent = {t["id"] for t in TASKS if t["ground_truth"]["history_silent_on"]}
        self.assertEqual(silent, {"A1", "A5", "A6"})
        self.assertIn("only A1, A5, A6", _read("PROTOCOL.md"))

    def test_the_protocol_table_matches_the_tasks(self) -> None:
        text = _read("PROTOCOL.md")
        for t in TASKS:
            with self.subTest(task=t["id"]):
                self.assertRegex(text, rf"\| {t['id']} \| {t['repo'].split('/')[1]} \| #{t['item']['number']} \|")


class SignTestClaimsTest(unittest.TestCase):
    """The protocol's list of significant outcomes, recomputed."""

    @staticmethod
    def p(b: int, a: int) -> float:
        n = b + a
        k = min(a, b)
        return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)

    def test_the_significant_patterns_are_exactly_those_registered(self) -> None:
        sig = {(b, a) for n in range(1, 10) for a in range(n + 1) for b in [n - a] if b > a and self.p(b, a) < 0.05}
        self.assertEqual(sig, {(6, 0), (7, 0), (8, 0), (9, 0), (8, 1)})

    def test_the_near_misses_the_protocol_names_are_not_significant(self) -> None:
        for b, a in ((7, 1), (6, 1), (5, 0)):
            with self.subTest(b=b, a=a):
                self.assertGreaterEqual(self.p(b, a), 0.05)


class SelectionRuleTest(unittest.TestCase):
    def test_the_two_exclusions_and_the_shortfall_are_disclosed(self) -> None:
        text = _read("PROTOCOL.md")
        for needle in ("`urllib3#5078`", "`urllib3#5109`", "2 eligible items across both repositories", "11 clusters"):
            self.assertIn(needle, text)

    def test_the_two_vetted_out_items_are_clusters_in_the_census_and_not_tasks(self) -> None:
        clusters = {c["task_item"] for c in DRAW["repos"]["urllib3/urllib3"]["clusters"]}
        self.assertTrue({5078, 5109} <= clusters)
        self.assertFalse({5078, 5109} & {t["item"]["number"] for t in TASKS})
        self.assertEqual(sum(len(r["clusters"]) for r in DRAW["repos"].values()), 11)


if __name__ == "__main__":
    unittest.main()
