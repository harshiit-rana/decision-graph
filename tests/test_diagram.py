"""What a picture of an answer may and may not say (issue #71).

A diagram is read as the whole story, which makes two failures worse here than in the text
output: drawing an edge the engine did not walk, and rendering a guess and a record
identically. Both are silent — a Mermaid label that breaks renders as nothing at all rather
than as an error, and a dashed line that becomes solid looks entirely fine.

Standalone: builds `Answer` fixtures directly, no database.
"""

from __future__ import annotations

import unittest

from decision_graph import diagram
from decision_graph.reasoning import Answer, Mode, NodeRef, Path, Step


def step(edge_id=1, edge_type="motivated_by", tier="explicit", src=1, dst=2) -> Step:
    return Step(
        edge_id=edge_id,
        edge_type=edge_type,
        tag="inferred" if tier == "inferred" else "explicit",
        evidence_tier=tier,
        from_node_id=src,
        to_node_id=dst,
        extractor="synthesis_closes_cluster",
        source_ref=None,
    )


def answer(paths, mode=Mode.WHY) -> Answer:
    return Answer(
        mode=mode,
        start_node_id=1,
        paths=paths,
        used_inferred_fallback=any(
            s.evidence_tier == "inferred" for p in paths for s in p.steps
        ),
        explanation="fixture",
    )


def one_hop(tier="explicit", title="change default redirect code to 303") -> Answer:
    return answer([
        Path(
            node_ids=[1, 2],
            steps=[step(tier=tier)],
            nodes={
                1: NodeRef("decision", title, "thread:1:pr-5898"),
                2: NodeRef("issue", title, "5895",
                           "https://github.com/pallets/flask/issues/5895"),
            },
        )
    ])


class MermaidTest(unittest.TestCase):
    def test_nodes_and_edges_are_emitted(self) -> None:
        out = diagram.to_mermaid(one_hop())
        # The diagram now starts with the %%{init}%% theme directive; graph LR follows.
        self.assertIn("graph LR", out)
        self.assertIn("issue #5895", out)
        self.assertIn("n1 -->", out)

    def test_the_tier_is_drawn_and_written(self) -> None:
        # Line weight is a hint; the word is the claim. A reader who cannot see the
        # difference between a dashed and a solid line must still be told.
        inferred = diagram.to_mermaid(one_hop(tier="inferred"))
        self.assertIn("-.->" , inferred)
        self.assertIn("inferred", inferred)

        corroborated = diagram.to_mermaid(one_hop(tier="corroborated"))
        self.assertIn("==>", corroborated)

        explicit = diagram.to_mermaid(one_hop())
        self.assertIn("-->", explicit)
        self.assertNotIn("-.->" , explicit)

    def test_a_quote_in_a_title_cannot_break_the_label(self) -> None:
        # Mermaid signals a broken label by rendering nothing, so this fails silently and
        # in the worst possible way: an empty picture that looks like an empty answer.
        out = diagram.to_mermaid(one_hop(title='use "303" by default'))
        self.assertIn("#quot;303#quot;", out)
        self.assertNotIn('"303"', out)

    def test_brackets_and_hashes_are_left_alone_inside_quotes(self) -> None:
        # Mermaid's quoted form exists so these can appear literally. Escaping them anyway
        # renders correctly and reads like machine output.
        out = diagram.to_mermaid(one_hop(title="redirect defaults to 303 (#5898)"))
        self.assertIn("(#5898)", out)
        self.assertNotIn("&#35;", out)

    def test_the_line_break_markup_survives_escaping(self) -> None:
        self.assertIn("<br/>", diagram.to_mermaid(one_hop()))

    def test_a_repeated_edge_is_drawn_once(self) -> None:
        # Impact walks overlap heavily: four paths from one node share their prefix, and
        # per-path emission would draw the same arrow four times.
        shared = step(edge_id=7)
        paths = [
            Path(node_ids=[1, 2], steps=[shared],
                 nodes={1: NodeRef("decision", "d"), 2: NodeRef("issue", "i", "5895")}),
            Path(node_ids=[1, 2], steps=[shared],
                 nodes={1: NodeRef("decision", "d"), 2: NodeRef("issue", "i", "5895")}),
        ]
        self.assertEqual(diagram.to_mermaid(answer(paths)).count("n1 -->"), 1)

    def test_a_decision_is_styled_as_the_assertion_it_is(self) -> None:
        out = diagram.to_mermaid(one_hop())
        self.assertIn("classDef decision", out)
        self.assertIn("{{", out, "the decision should have the shape nothing else uses")

    def test_no_answer_draws_nothing_rather_than_an_empty_box(self) -> None:
        # `graph LR` with no nodes renders as a blank frame, which reads as a broken
        # diagram rather than as "the graph holds no answer".
        self.assertEqual(diagram.to_mermaid(answer([])), "")

    def test_every_node_type_gets_a_distinct_class(self) -> None:
        """Node types must not all render in the same default gray."""
        out = diagram.to_mermaid(one_hop())
        # At minimum: decision and issue are both in the one-hop fixture
        self.assertIn("classDef issue", out)
        self.assertIn("classDef decision", out)

    def test_light_theme_init_directive_is_present(self) -> None:
        """The init directive forces the base (light) theme so the diagram is readable
        regardless of whether mermaid.live or GitHub default to a dark theme."""
        out = diagram.to_mermaid(one_hop())
        self.assertIn("%%{init:", out)
        self.assertIn("'theme': 'base'", out)

    def test_decision_nodes_have_explicit_dark_text_color(self) -> None:
        """Decision classDef must set color so text is visible on the amber fill."""
        out = diagram.to_mermaid(one_hop())
        decision_line = next(ln for ln in out.splitlines() if "classDef decision" in ln)
        self.assertIn("color:", decision_line)




class DotTest(unittest.TestCase):
    def test_it_is_a_digraph_with_labelled_edges(self) -> None:
        out = diagram.to_dot(one_hop())
        self.assertTrue(out.startswith("digraph answer {"))
        self.assertTrue(out.rstrip().endswith("}"))
        self.assertIn("was motivated by (explicit)", out)

    def test_the_line_break_is_dots_own_and_not_a_literal_backslash_n(self) -> None:
        # _escape_dot doubles backslashes, so building the label before escaping emitted a
        # literal `\\n` and printed it inside the box.
        out = diagram.to_dot(one_hop())
        self.assertIn("\\n", out)
        self.assertNotIn("\\\\n", out)

    def test_a_quote_in_a_title_is_escaped(self) -> None:
        out = diagram.to_dot(one_hop(title='use "303" by default'))
        self.assertIn('\\"303\\"', out)

    def test_tiers_are_visually_distinct(self) -> None:
        self.assertIn("style=dashed", diagram.to_dot(one_hop(tier="inferred")))
        self.assertIn("penwidth=2.6", diagram.to_dot(one_hop(tier="corroborated")))

    def test_nodes_carry_their_url_so_an_svg_is_clickable(self) -> None:
        self.assertIn(
            'URL="https://github.com/pallets/flask/issues/5895"', diagram.to_dot(one_hop())
        )

    def test_no_answer_draws_nothing(self) -> None:
        self.assertEqual(diagram.to_dot(answer([])), "")


class OnlyWhatWasWalkedTest(unittest.TestCase):
    def test_the_diagram_contains_exactly_the_traversed_edges(self) -> None:
        # The rule the module exists to keep. The credited implementer is named in the text
        # output and is not on the path; drawing it would put a hop in the picture that the
        # engine never made, and a picture is read as the whole story.
        out = diagram.to_mermaid(one_hop())
        self.assertEqual(out.count("-->"), 1)
        # Count only node *definition* lines (they contain a shape-bracket pair like ([, {{, [)
        # not the per-node `class nXXX type;` assignment lines added for styling.
        node_def_lines = [
            ln for ln in out.splitlines()
            if any(marker in ln for marker in ("([", "{{", "))"))
            or (ln.strip().startswith("n") and "-->" not in ln and "class" not in ln and "classDef" not in ln)
        ]
        self.assertEqual(len(node_def_lines), 2, f"expected 2 node definitions, got: {node_def_lines}")



if __name__ == "__main__":
    unittest.main()
