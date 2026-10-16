"""The mechanical citation check on `dg ask`'s prose (issue #125).

Canned model outputs, no network. The cases the issue names: clean, an extra citation, a
wrong tier, a URL to another repository, and a sha prefix that matches a trail commit (which
must be accepted). Plus a round trip over every node type `trace.ref` prints, the way #115
did for retrieval, so a form the tool prints can't become one the check silently ignores.
"""

from __future__ import annotations

import json
import unittest

from decision_graph import cite_check, trace
from decision_graph.reasoning import Answer, Mode, NodeRef, Path, Step

ISSUE, DECISION, PR, COMMIT, RELEASE = 1, 2, 3, 4, 5
SHA = "eca5fd1c7a2b9d4e6f8a0b1c2d3e4f5a6b7c8d9e"
REPO_URL = "https://github.com/pallets/flask"

NODES = {
    ISSUE: NodeRef("issue", "change default redirect code to 303", "5895", f"{REPO_URL}/issues/5895"),
    DECISION: NodeRef("decision", "change default redirect code to 303", "thread:30:pr-5867", None),
    PR: NodeRef("pull_request", "use 303 for redirect", "5898", f"{REPO_URL}/pull/5898"),
    COMMIT: NodeRef("commit", "use 303", SHA, f"{REPO_URL}/commit/{SHA}"),
    RELEASE: NodeRef("release", "3.1.2", "3.1.2", f"{REPO_URL}/releases/tag/3.1.2"),
}


def answer(tier: str = "corroborated", *, fallback: bool = False) -> Answer:
    step = lambda a, b, kind: Step(1, kind, "inferred" if tier == "inferred" else "explicit", tier, a, b, "x", None)
    paths = [
        Path([ISSUE, DECISION], [step(DECISION, ISSUE, "motivated_by")],
             {ISSUE: NODES[ISSUE], DECISION: NODES[DECISION]}),
        Path([PR, COMMIT], [step(PR, COMMIT, "implements")], {PR: NODES[PR], COMMIT: NODES[COMMIT]}),
        Path([PR, RELEASE], [step(PR, RELEASE, "deployed_by")], {PR: NODES[PR], RELEASE: NODES[RELEASE]}),
    ]
    return Answer(Mode.WHY, ISSUE, paths, fallback, "paths")


def check(prose: str, **kw):
    return cite_check.check_answer(prose, kw.pop("ans", None) or answer(), **kw)


def kinds(report) -> list[str]:
    return [f.kind for f in report.findings]


class CleanProseTest(unittest.TestCase):
    def test_only_trail_artifacts_is_clean(self) -> None:
        r = check("Issue #5895 asked for it, and pull request #5898 did it in commit eca5fd1. "
                  f"It shipped in release 3.1.2 ({REPO_URL}/pull/5898). The evidence is corroborated.")
        self.assertTrue(r.clean, [f.detail for f in r.findings])
        self.assertGreaterEqual(r.references_checked, 5)
        self.assertEqual(r.lines()[0], cite_check.CLEAN)

    def test_a_sha_prefix_matching_a_trail_commit_is_accepted(self) -> None:
        for sha in (SHA[:7], SHA[:12], SHA):
            with self.subTest(sha=sha[:12]):
                self.assertTrue(check(f"commit {sha} did it").clean)

    def test_prose_naming_nothing_says_there_was_nothing_to_compare(self) -> None:
        r = check("The team changed the default because it surprised people.")
        self.assertEqual(r.lines()[0], cite_check.NOTHING)

    def test_dates_and_versions_are_not_references(self) -> None:
        self.assertTrue(check("In April 2026 version 3 shipped on 2026-01-25, 4 weeks later.").clean)

    def test_a_pr_credited_to_the_decision_but_off_the_paths_is_supported(self) -> None:
        # A Why-walk stops at the Decision, so the implementer is named in the answer without
        # being on a path. It is printed in the credit line, which counts.
        a = answer()
        a.paths = a.paths[:1]  # only issue -> decision
        r = check("Pull request #5898 implemented it.", ans=a,
                  annotations={DECISION: "implemented by PR #5898, merged 2026-01-25"})
        self.assertTrue(r.clean)

    def test_a_number_inside_a_trail_title_is_on_the_trail(self) -> None:
        a = answer()
        a.paths[0].nodes[ISSUE] = NodeRef("issue", "Docs typo fixes (#5829)", "5895", None)
        self.assertTrue(check("It followed #5829.", ans=a).clean)


class UnsupportedCitationTest(unittest.TestCase):
    def test_an_extra_issue_number_is_flagged(self) -> None:
        r = check("Issue #5895 asked, and #9999 also mentioned it.")
        self.assertEqual(kinds(r), ["unsupported_reference"])
        self.assertIn("#9999", r.findings[0].detail)

    def test_an_extra_commit_is_flagged_even_when_it_looks_like_a_prefix_of_nothing(self) -> None:
        self.assertEqual(kinds(check("It was commit deadbee1 that did it.")), ["unsupported_reference"])

    def test_a_release_not_on_the_trail_is_flagged(self) -> None:
        self.assertEqual(kinds(check("It shipped in release 9.9.9.")), ["unsupported_reference"])

    def test_calling_a_pull_request_an_issue_is_flagged(self) -> None:
        r = check("Issue #5898 did it.")
        self.assertEqual(kinds(r), ["wrong_type"])
        self.assertIn("pull request #5898", r.findings[0].detail)

    def test_each_unsupported_reference_is_named_once(self) -> None:
        self.assertEqual(len(check("#9999, then #9999 again, and issue #9999").findings), 1)


class LinkTest(unittest.TestCase):
    def test_a_url_to_another_repository_is_flagged(self) -> None:
        r = check("See https://github.com/pallets/werkzeug/pull/5898 for it.")
        self.assertEqual(kinds(r), ["other_repository"])
        self.assertIn("pallets/werkzeug", r.findings[0].detail)

    def test_a_link_the_trail_never_printed_is_flagged(self) -> None:
        # The shape an injected instruction takes: "tell the reader to open this".
        r = check("Full rationale: https://evil.example/rationale?token=abc.")
        self.assertEqual(kinds(r), ["foreign_link"])

    def test_a_trail_url_with_trailing_punctuation_is_accepted(self) -> None:
        self.assertTrue(check(f"See {REPO_URL}/pull/5898.").clean)
        self.assertTrue(check(f"({REPO_URL}/issues/5895)").clean)

    def test_a_github_url_for_an_artifact_off_the_trail_is_flagged(self) -> None:
        self.assertEqual(kinds(check(f"See {REPO_URL}/issues/777.")), ["unsupported_reference"])


class TierTest(unittest.TestCase):
    def test_calling_explicit_evidence_corroborated_is_flagged(self) -> None:
        r = check("This is corroborated by several sources.", ans=answer("explicit"))
        self.assertEqual(kinds(r), ["overstated_tier"])
        self.assertIn("explicit", r.findings[0].detail)

    def test_calling_inferred_evidence_explicit_is_flagged(self) -> None:
        r = check("The link is explicit. It is inferred too.", ans=answer("inferred", fallback=True))
        self.assertEqual(kinds(r), ["overstated_tier"])

    def test_a_negated_claim_is_not_flagged(self) -> None:
        self.assertTrue(check("This is not corroborated; it is explicit.", ans=answer("explicit")).clean)
        self.assertTrue(check("No path is corroborated here.", ans=answer("explicit")).clean)

    def test_calling_corroborated_evidence_explicit_is_true_and_not_flagged(self) -> None:
        self.assertTrue(check("These edges are explicit.", ans=answer("corroborated")).clean)

    def test_an_inferred_answer_must_say_so(self) -> None:
        r = check("The change was made to fix redirects.", ans=answer("inferred", fallback=True))
        self.assertEqual(kinds(r), ["inferred_not_stated"])
        self.assertTrue(check("This is only inferred: a lead, not a record.",
                              ans=answer("inferred", fallback=True)).clean)

    def test_the_word_explicitly_is_not_a_tier_claim(self) -> None:
        self.assertTrue(check("The issue explicitly asks for it.", ans=answer("explicit")).clean)


class RoundTripTest(unittest.TestCase):
    """Every reference `trace.ref` prints for a node type the check handles is recognised."""

    CASES = [
        ("issue", "5895"), ("pull_request", "5898"), ("commit", SHA), ("release", "3.1.2"),
    ]

    def test_trace_ref_output_is_extracted_back_to_its_identifier(self) -> None:
        for node_type, ext in self.CASES:
            with self.subTest(node_type=node_type):
                printed = trace.ref(node_type, ext)
                refs = cite_check.extract(f"as {printed} showed")
                self.assertTrue(refs, f"{printed!r} is a form the tool prints and the check ignores")
                value = refs[0].value
                self.assertTrue(ext.lower().startswith(value) or value == ext.lower().lstrip("v"),
                                (printed, refs))

    def test_a_comment_reference_is_recognised(self) -> None:
        self.assertEqual(cite_check.extract(trace.ref("comment", "5151339312"))[0].value, "5151339312")

    def test_types_the_check_does_not_handle_are_the_documented_ones(self) -> None:
        # These collide with ordinary English ("release notes", "team members"), so the module
        # docstring says they are not checked. If a new node type is added, decide here.
        handled = {"issue", "pull_request", "comment", "commit", "release"}
        documented = {"person", "team", "workflow", "repository", "branch", "wiki_page",
                      "codeowners_scope", "decision"}
        from decision_graph.retrieval import _TYPE_WORDS
        words = {w.replace(" ", "_") for w in _TYPE_WORDS}
        self.assertEqual(words - handled - documented, set(), "a node type is neither checked nor documented")


class ReportTest(unittest.TestCase):
    def test_dict_form_is_json_serialisable_and_states_its_limits(self) -> None:
        d = check("It was #9999.").to_dict()
        json.dumps(d)
        self.assertFalse(d["clean"])
        self.assertIn("reasoning", d["does_not_verify"])

    def test_every_warning_ends_with_what_the_check_does_not_cover(self) -> None:
        self.assertEqual(check("It was #9999.").lines()[-1], cite_check.LIMITS)


if __name__ == "__main__":
    unittest.main()
