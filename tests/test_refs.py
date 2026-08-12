"""Unit tests for the extraction-first parsers.

stdlib unittest, no test dependency: `python -m unittest discover tests`.

The CODEOWNERS parser matters most here. pallets/flask ships no CODEOWNERS file
(issue #1), so ingestion can never exercise it against the real target — these tests
are the only verification it will ever get.
"""

from __future__ import annotations

import unittest

from decision_graph import refs, threads


class TestClosingRefs(unittest.TestCase):
    def test_recognises_github_closing_keywords(self) -> None:
        for text in (
            "Fixes #123",
            "fixed #123",
            "Closes #123",
            "closed #123",
            "Resolves #123",
            "close #123",
            "Fixes: #123",
        ):
            with self.subTest(text=text):
                self.assertEqual(refs.closing_refs(text), {123})

    def test_plain_mention_is_not_a_closure(self) -> None:
        text = "Related to #99, see also #100"
        self.assertEqual(refs.closing_refs(text), set())
        self.assertEqual(refs.mentioned_refs(text), {99, 100})

    def test_mentions_exclude_closures(self) -> None:
        # A body that both closes one issue and mentions another must not produce a
        # duplicate `references` edge alongside the `closes` edge — the rubric treats
        # them differently, so double-counting would inflate Validation evidence.
        text = "Fixes #1. Context in #2."
        self.assertEqual(refs.closing_refs(text), {1})
        self.assertEqual(refs.mentioned_refs(text), {2})

    def test_ignores_urls_and_paths(self) -> None:
        self.assertEqual(refs.mentioned_refs("see docs/page#3 for detail"), set())

    def test_closing_keyword_rejects_url_fragments(self) -> None:
        """CLOSES_RE has no lookbehind; it relies on `\\s+` making whitespace before '#'
        mandatory. Relaxing that to `\\s*` would silently admit URL fragments, which
        could then upgrade the wrong Decision to explicit status via a release note."""
        for text in (
            "Changes: https://flask.palletsprojects.com/en/3.0.x/changes/#5448",
            "fixes https://github.com/pallets/flask/issues/5448",
            "closes flask#5448",
            "resolved by commit_abc#123",
        ):
            with self.subTest(text=text):
                self.assertEqual(refs.closing_refs(text), set())

    def test_closing_keyword_still_matches_real_forms(self) -> None:
        # The guard must not cost recall on the forms that actually occur in flask.
        self.assertEqual(refs.closing_refs("Fixes #5776."), {5776})
        self.assertEqual(refs.closing_refs("## Fixes\n#5825"), {5825})

    def test_handles_empty_and_none(self) -> None:
        self.assertEqual(refs.closing_refs(None), set())
        self.assertEqual(refs.mentioned_refs(""), set())


class TestCommitRefs(unittest.TestCase):
    def test_requires_full_forty_char_sha(self) -> None:
        full = "a" * 40
        self.assertEqual(refs.commit_refs(f"reverts {full}"), {full})

    def test_rejects_abbreviated_sha(self) -> None:
        # Abbreviated SHAs are indistinguishable from ordinary hex in prose; accepting
        # them produced false `references` edges.
        self.assertEqual(refs.commit_refs("see abc1234 for context"), set())


class TestCodeowners(unittest.TestCase):
    def test_parses_patterns_and_owners(self) -> None:
        content = "\n".join(
            [
                "# comment line",
                "",
                "*       @global-owner",
                "/docs/  @org/docs-team @alice",
                "*.js    @js-owner   # trailing comment",
            ]
        )
        rules = refs.parse_codeowners(content)
        self.assertEqual(
            rules,
            [
                (3, "*", ["global-owner"]),
                (4, "/docs/", ["org/docs-team", "alice"]),
                (5, "*.js", ["js-owner"]),
            ],
        )

    def test_skips_blank_comment_and_ownerless_lines(self) -> None:
        self.assertEqual(refs.parse_codeowners("# just a comment\n\n/orphan-pattern\n"), [])


class TestThreadKeyRanking(unittest.TestCase):
    def test_pull_request_key_beats_issue_key(self) -> None:
        # Determinism matters: if the winner depended on ingestion order, the same repo
        # ingested twice could produce different thread_keys, making rubric clause 3
        # order-dependent.
        pr = threads.pr_key(1, 500)
        issue = threads.issue_key(1, 2)
        self.assertEqual(sorted([pr, issue], key=threads._rank)[0], pr)
        self.assertEqual(sorted([issue, pr], key=threads._rank)[0], pr)

    def test_lower_number_wins_within_same_kind(self) -> None:
        low, high = threads.pr_key(1, 10), threads.pr_key(1, 99)
        self.assertEqual(sorted([high, low], key=threads._rank)[0], low)


if __name__ == "__main__":
    unittest.main()
