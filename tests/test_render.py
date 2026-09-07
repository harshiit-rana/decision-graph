"""What the reader is shown, and what must never disappear from it (issue #69).

The renderer is presentation, so the risk is not that it computes something wrong — it is
that it quietly stops showing something load-bearing. An answer whose evidence tier is
missing, or whose inferred fallback is not flagged, reads exactly like a stronger answer;
that is the failure mode §5.3 and §5.4 exist to prevent, arriving at the last possible
moment. These tests pin the things that must survive every later edit to the layout.

Standalone: `Answer` and `Path` are plain dataclasses, so none of this needs a database.
"""

from __future__ import annotations

import io
import unittest

from decision_graph import render, trace
from decision_graph.reasoning import Answer, Mode, NodeRef, Path, Step


def step(edge_type="motivated_by", tier="explicit", src=1, dst=2) -> Step:
    return Step(
        edge_id=1,
        edge_type=edge_type,
        tag="inferred" if tier == "inferred" else "explicit",
        evidence_tier=tier,
        from_node_id=src,
        to_node_id=dst,
        extractor="synthesis_closes_cluster",
        source_ref=None,
    )


DECISION = NodeRef("decision", "change default redirect code to 303", "thread:1:pr-5898")
ISSUE = NodeRef(
    "issue",
    "change default redirect code to 303",
    "5895",
    "https://github.com/pallets/flask/issues/5895",
)


def answer(*, found=True, tier="explicit", fallback=False, mode=Mode.WHY) -> Answer:
    if not found:
        return Answer(
            mode=mode,
            start_node_id=1,
            paths=[],
            used_inferred_fallback=fallback,
            explanation="no path found, explicit or inferred",
        )
    path = Path(
        node_ids=[1, 2],
        steps=[step(tier=tier)],
        nodes={1: DECISION, 2: ISSUE},
    )
    return Answer(
        mode=mode,
        start_node_id=1,
        paths=[path],
        used_inferred_fallback=fallback,
        explanation="1 explicit path(s) found; inferred edges not consulted",
    )


def render_to_string(ans: Answer, **kwargs) -> str:
    out = io.StringIO()
    render.render(ans, out=out, **kwargs)
    return out.getvalue()


class IdentityTest(unittest.TestCase):
    def test_artifacts_are_named_the_way_github_names_them(self) -> None:
        printed = render_to_string(answer())
        self.assertIn("issue #5895", printed)
        self.assertNotIn("issue:2", printed, "a database primary key reached the reader")

    def test_the_url_is_offered_so_the_claim_can_be_checked(self) -> None:
        # §9's method is a human opening the artifact and comparing. The URL was stored on
        # 988 of 1003 nodes and printed on none of them.
        self.assertIn(
            "https://github.com/pallets/flask/issues/5895", render_to_string(answer())
        )

    def test_a_decision_is_not_labelled_with_its_cluster_key(self) -> None:
        # A Decision's external_id is its thread_key, which names the cluster and for 6 of
        # 15 Decisions names a pull request that never merged (#19).
        self.assertNotIn("thread:1:pr-5898", render_to_string(answer()))

    def test_node_ids_are_still_available_under_verbose(self) -> None:
        # Moved, not removed: they are what an adjudicator uses to query the graph directly.
        printed = render_to_string(answer(), verbose=True)
        self.assertIn("node:2", printed)


class EvidenceTest(unittest.TestCase):
    def test_the_tier_is_shown_and_explained(self) -> None:
        printed = render_to_string(answer(tier="corroborated"))
        self.assertIn("corroborated", printed)
        self.assertIn(trace.TIER_MEANING["corroborated"], printed)

    def test_an_inferred_fallback_is_flagged_before_the_answer(self) -> None:
        printed = render_to_string(answer(tier="inferred", fallback=True))
        self.assertIn("INFERRED", printed)
        self.assertLess(
            printed.index("INFERRED"),
            printed.index("What the graph says"),
            "a reader who stops at the first sentence must already know it is a guess",
        )

    def test_the_tier_survives_without_colour(self) -> None:
        # Piped output has no colour, and CI logs and `dg query > file` are always piped.
        # The ASCII mark is what carries the tier there.
        printed = render_to_string(answer(tier="inferred", fallback=True))
        self.assertIn(trace.TIER_MARK["inferred"], printed)

    def test_the_extractor_is_available_under_verbose(self) -> None:
        self.assertIn("synthesis_closes_cluster", render_to_string(answer(), verbose=True))


class SentenceTest(unittest.TestCase):
    def test_edge_direction_changes_the_verb(self) -> None:
        # The same edge read the other way round is a different English claim, and getting
        # this backwards would invert what the answer says while looking correct.
        self.assertEqual(render.phrase("motivated_by", True), "was motivated by")
        self.assertEqual(render.phrase("motivated_by", False), "motivated")

    def test_an_unmapped_edge_type_falls_back_to_its_name(self) -> None:
        # Better a bare edge type than a wrong verb; new edge types must not silently
        # acquire prose that misstates them.
        self.assertEqual(render.phrase("relates_to", True), "relates_to")

    def test_the_summary_is_built_only_from_the_paths(self) -> None:
        lines = render.summarize(answer(), {}, {})
        self.assertEqual(len(lines), 1)
        self.assertIn("issue #5895", lines[0])

    def test_the_decision_annotation_reaches_the_reader(self) -> None:
        printed = render_to_string(
            answer(),
            annotations={1: "implemented by PR #5898, merged 2026-01-25"},
            statuses={1: "reconstructed"},
        )
        self.assertIn("PR #5898", printed)
        self.assertIn("reconstructed", printed)

    def test_an_artifact_named_but_not_walked_is_still_linkable(self) -> None:
        # The credited implementer is named in the answer and is not on the path, so it is
        # the one artifact a reader was told about and could not open.
        printed = render_to_string(
            answer(), links={"pull request #5898": "https://github.com/pallets/flask/pull/5898"}
        )
        self.assertIn("https://github.com/pallets/flask/pull/5898", printed)

    def test_counts_are_not_written_as_path_s(self) -> None:
        printed = render_to_string(answer())
        self.assertIn("1 path", printed)
        self.assertNotIn("path(s)", printed.split("engine:")[0])


class RefusalTest(unittest.TestCase):
    def test_a_refusal_says_it_is_a_result(self) -> None:
        printed = render_to_string(answer(found=False))
        self.assertIn("not a failure", printed)
        self.assertIn("What to try", printed)

    def test_a_refusal_keeps_the_engines_own_verdict(self) -> None:
        # The prose explains; it does not replace. An adjudicator needs the engine's words.
        self.assertIn(
            "no path found, explicit or inferred", render_to_string(answer(found=False))
        )

    def test_impact_and_why_refusals_explain_different_things(self) -> None:
        why = render_to_string(answer(found=False, mode=Mode.WHY))
        impact = render_to_string(answer(found=False, mode=Mode.IMPACT))
        self.assertIn("records why this happened", why)
        self.assertIn("records what this affects", impact)

    def test_a_point_in_time_refusal_names_the_moment(self) -> None:
        # The most easily misread refusal: the evidence exists but did not exist YET.
        # "Nothing records why this happened" is false when the caller asked about a date
        # before the work landed, and it is the answer a temporal control depends on.
        from datetime import datetime

        printed = render_to_string(answer(found=False), as_of=datetime(2025, 6, 1))
        self.assertIn("as of 2025-06-01", printed)
        self.assertIn("not that it is absent now", printed)

    def test_an_ordinary_refusal_does_not_mention_time(self) -> None:
        self.assertNotIn("as of", render_to_string(answer(found=False)))

    def test_a_tried_fallback_is_reported(self) -> None:
        # "Nothing found" and "nothing found even after admitting guesses" are different
        # statements about how hard the engine looked.
        self.assertIn(
            "inferred fallback was tried",
            render_to_string(answer(found=False, fallback=True)),
        )


class ContextAndMultiHopTest(unittest.TestCase):
    def test_multi_hop_does_not_claim_issue_implemented_decision(self) -> None:
        """An issue never implements a decision; a pull request does.
        Multi-hop paths must attribute the implementation to the actor node, not the start node."""
        path = Path(
            node_ids=[1, 2, 3],
            steps=[
                Step(1, "references", "explicit", "explicit", 1, 2, "extractor", None),
                Step(2, "implemented_by", "explicit", "explicit", 3, 2, "extractor", None),
            ],
            nodes={
                1: NodeRef("issue", "pass context internally", "5815"),
                2: NodeRef("pull_request", "merge app and request context", "5812"),
                3: NodeRef("decision", "merge app and request contexts", "thread:1:pr-5812"),
            },
        )
        ans = Answer(
            mode=Mode.WHY,
            start_node_id=1,
            paths=[path],
            used_inferred_fallback=False,
            explanation="test",
        )
        lines = render.summarize(ans, {}, {})
        self.assertEqual(len(lines), 1)
        # Must NOT claim issue 5815 implemented anything
        self.assertNotIn('issue #5815 "pass context internally" implemented', lines[0])
        # Must clearly indicate the linking relationship
        self.assertIn("links to pull request #5812", lines[0])
        self.assertIn("which implemented", lines[0])

    def test_motivation_and_context_block_is_printed_when_bodies_provided(self) -> None:
        ans = answer()
        body_text = (
            "Flask and Werkzeug redirect currently defaults to a 302. Routing uses 307 "
            "since that preserves method consistently. We didn't change redirect default to 307 "
            "since that would break the common pattern of GET form, POST form, redirect to GET result."
        )
        printed = render_to_string(ans, bodies={2: body_text})
        self.assertIn("Motivation & Context", printed)
        self.assertIn("Flask and Werkzeug redirect currently defaults to a 302", printed)



if __name__ == "__main__":
    unittest.main()

