"""Documentation claims that the code can contradict, checked (issue #117).

Each of these was true once and went stale without any test noticing: a test count that
drifted from 209 to 394, a PRD filename that disagreed with its own header, a stub
described as a property of the target repository. They are checked mechanically, so the
next drift fails the build instead of waiting to be found.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from decision_graph import inference

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class DocsTruthTest(unittest.TestCase):
    def test_the_readme_names_a_prd_file_that_exists_and_whose_header_agrees(self) -> None:
        named = re.search(r"`(PRD_v[\d.]+_[A-Za-z_]+\.md)`", read("README.md"))
        self.assertIsNotNone(named)
        prd = ROOT / named.group(1)
        self.assertTrue(prd.exists(), f"README names {prd.name}, which does not exist")
        version = re.search(r"v(\d+\.\d+)", prd.name).group(1)
        self.assertIn(f"# PRD v{version}", prd.read_text(encoding="utf-8").splitlines()[0])

    def test_the_readme_tests_section_states_no_hand_typed_counts(self) -> None:
        text = read("README.md")
        start = text.index("\n## Tests")
        section = text[start:text.index("\n## ", start + 5)]
        self.assertIsNone(
            re.search(r"\b\d+\s+(?:tests?|checks?)\b", section),
            "a count in the README's Tests section will drift; the commands print their own",
        )

    def test_the_stub_is_stated_while_it_is_a_stub(self) -> None:
        # If propose() ever returns something, this fails and the three documents that say
        # "stub" have to be revisited together, not left to contradict the code.
        self.assertEqual(inference.propose.__doc__.split(":")[0], "STUB (Phase 1)")
        for doc in ("README.md", "eval/RESULTS.md", "PRD_v3.2_Organizational_Intelligence_Engine.md"):
            with self.subTest(doc=doc):
                self.assertRegex(read(doc), r"(?i)because the proposer is a stub|stub")

    def test_no_document_calls_the_inferred_absence_a_finding_about_flask(self) -> None:
        for doc in ("README.md", "eval/RESULTS.md"):
            with self.subTest(doc=doc):
                text = read(doc)
                for m in re.finditer(r"zero inferred edges", text):
                    tail = text[m.start():m.start() + 200]
                    self.assertIn("stub", tail, f"{doc}: 'zero inferred edges' without saying why")

    def test_h5_is_not_described_as_a_paraphrase(self) -> None:
        for doc in ("README.md", "eval/RESULTS.md", "PRD_v3.2_Organizational_Intelligence_Engine.md"):
            with self.subTest(doc=doc):
                for m in re.finditer(r"H5[^.\n]{0,120}", read(doc)):
                    self.assertNotRegex(m.group(0), r"through a paraphrase|paraphrase-retrieval")

    def test_the_prd_no_longer_lists_webhooks_as_a_live_non_goal(self) -> None:
        text = read("PRD_v3.2_Organizational_Intelligence_Engine.md")
        non_goals = text[text.index("## 4. Non-Goals"):text.index("## 5.")]
        for line in non_goals.splitlines():
            if "Webhook" in line:
                self.assertIn("~~", line, "a built feature is listed as a non-goal")

    def test_the_package_description_is_not_phase_1(self) -> None:
        meta = tomllib.loads(read("pyproject.toml"))["project"]
        self.assertNotIn("Phase 1", meta["description"])


if __name__ == "__main__":
    unittest.main()
