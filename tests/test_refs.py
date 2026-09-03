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
        could then upgrade the wrong Decision to explicit status via a release note.

        This used to also pin `fixes https://github.com/pallets/flask/issues/5448` as
        rejected. That was wrong -- GitHub closes an issue written that way -- and it cost
        two Decisions before anyone noticed (#50). The docs-anchor case below is the one
        this mechanism is genuinely for; the GitHub-URL case moved to its own tests.
        """
        for text in (
            "Changes: https://flask.palletsprojects.com/en/3.0.x/changes/#5448",
            "closes flask#5448",
            "resolved by commit_abc#123",
        ):
            with self.subTest(text=text):
                self.assertEqual(refs.closing_refs(text, "pallets/flask"), set())

    def test_closing_reference_as_a_full_github_url(self) -> None:
        """The form that cost PR 5736 -> issue 5729 and PR 6096 -> issue 6093 (#50)."""
        self.assertEqual(
            refs.closing_refs(
                "fixes https://github.com/pallets/flask/issues/5729", "pallets/flask"
            ),
            {5729},
        )

    def test_github_url_slug_is_matched_case_insensitively(self) -> None:
        """GitHub slugs are case-insensitive, so `PALLETS/Flask` is the same repository."""
        self.assertEqual(
            refs.closing_refs(
                "Fixes https://github.com/PALLETS/Flask/issues/6093", "pallets/flask"
            ),
            {6093},
        )

    def test_another_repositorys_url_is_not_resolved_against_our_numbers(self) -> None:
        """The trap in the fix. werkzeug#3219 is not flask#3219, and reading it as one
        would invent an edge to whatever unrelated issue holds that number here."""
        for text in (
            "fixes https://github.com/pallets/werkzeug/issues/3219",
            "Added `client.query` in https://github.com/pallets/werkzeug/pull/3219",
        ):
            with self.subTest(text=text):
                self.assertEqual(refs.closing_refs(text, "pallets/flask"), set())

    def test_pull_urls_are_not_closing_references(self) -> None:
        """A PR closing a PR is not the Motivation-Implementation shape clause 3 is about."""
        self.assertEqual(
            refs.closing_refs(
                "fixes https://github.com/pallets/flask/pull/5729", "pallets/flask"
            ),
            set(),
        )

    def test_url_form_needs_a_repo_to_compare_against(self) -> None:
        """A caller that does not know its repo keeps the old behaviour rather than
        guessing that the URL's slug must be the right one."""
        self.assertEqual(
            refs.closing_refs("fixes https://github.com/pallets/flask/issues/5729"),
            set(),
        )

    def test_a_url_closing_reference_is_not_also_a_bare_mention(self) -> None:
        """Otherwise the same artifact earns both `closes` and `references`, and the
        weaker edge muddies the provenance of the stronger one."""
        text = "fixes https://github.com/pallets/flask/issues/5729"
        self.assertEqual(refs.mentioned_refs(text, "pallets/flask"), set())

    def test_closing_keyword_still_matches_real_forms(self) -> None:
        # The guard must not cost recall on the forms that actually occur in flask.
        self.assertEqual(refs.closing_refs("Fixes #5776."), {5776})
        self.assertEqual(refs.closing_refs("## Fixes\n#5825"), {5825})

    def test_handles_empty_and_none(self) -> None:
        self.assertEqual(refs.closing_refs(None), set())
        self.assertEqual(refs.mentioned_refs(""), set())


class TestHtmlComments(unittest.TestCase):
    """Closing keywords inside `<!-- -->` are template scaffolding, not claims (#59).

    GitHub does not honour them. flask PR 6106 is `<!-- Fixes #11 -->` above a docs typo
    fix, and issue 11's timeline carries no reference to 6106 at all — while we queued a
    `closes`, which would have become a false edge had issue 11 been inside the window.
    """

    #: PR 6106's body, verbatim.
    TEMPLATE_BOILERPLATE = (
        "<!-- Fixes #11 -->\n\n## Summary\nFix typo in docs\n\n"
        "## Changes\n- Formatting update in README.md\n\n## Testing\nN/A\n"
    )

    def test_the_flask_6106_body_yields_no_closing_reference(self) -> None:
        self.assertEqual(refs.closing_refs(self.TEMPLATE_BOILERPLATE, "pallets/flask"), set())

    def test_it_is_not_demoted_to_a_bare_mention_either(self) -> None:
        """Stripping only the closing set would leave `#11` looking like a `references`
        edge — a weaker false edge is still a false edge."""
        self.assertEqual(refs.mentioned_refs(self.TEMPLATE_BOILERPLATE, "pallets/flask"), set())

    def test_a_commented_url_reference_is_ignored_too(self) -> None:
        text = "<!-- fixes https://github.com/pallets/flask/issues/5729 -->"
        self.assertEqual(refs.closing_refs(text, "pallets/flask"), set())

    def test_visible_text_around_a_comment_is_still_read(self) -> None:
        """The comment must be removed, not the body. This is the case that would make the
        fix a recall regression rather than a precision win."""
        text = "Fixes #5776\n<!-- template note: closes #11 -->\nAlso see #5825"
        self.assertEqual(refs.closing_refs(text, "pallets/flask"), {5776})
        self.assertEqual(refs.mentioned_refs(text, "pallets/flask"), {5825})

    def test_a_multiline_comment_is_stripped_whole(self) -> None:
        text = "<!--\nFixes #11\nCloses #12\n-->\nFixes #5776"
        self.assertEqual(refs.closing_refs(text, "pallets/flask"), {5776})

    def test_two_comments_do_not_swallow_the_text_between_them(self) -> None:
        """A greedy `.*` would treat the first `<!--` and the last `-->` as one comment and
        eat the real reference in the middle."""
        text = "<!-- a --> Fixes #5776 <!-- b -->"
        self.assertEqual(refs.closing_refs(text, "pallets/flask"), {5776})

    def test_a_comment_does_not_supply_the_whitespace_closes_re_requires(self) -> None:
        """The comment is deleted, not replaced with a space. Replacing would turn this
        into `closes #11` and match — relaxing across a comment boundary the mandatory
        `\\s+` that keeps CLOSES_RE off URL fragments. GitHub sees `closes#11` here and
        closes nothing, so neither should we."""
        self.assertEqual(refs.closing_refs("closes<!-- x -->#11", "pallets/flask"), set())

    def test_an_unclosed_comment_marker_is_not_treated_as_a_comment(self) -> None:
        """`<!--` with no terminator is malformed; dropping the rest of the body on it
        would lose real references to a stray angle bracket."""
        self.assertEqual(refs.closing_refs("<!-- oops\nFixes #5776", "pallets/flask"), {5776})


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
