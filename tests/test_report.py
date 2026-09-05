"""What the browsable report must contain, and must not claim (issue #73).

A page is read as a whole and a page of decisions is read as a history, so the risks here
are about omission rather than error: 15 cards with nothing said about the 223 clusters
that produced none is a coverage claim made by silence. These pin the parts that carry
that weight, plus the two mechanical things that fail invisibly — a page that cannot
render its diagrams when opened from disk, and one whose numbers disagree with the
measurement script.

Rendering is tested from fixture data, so no database is needed. `collect` reads the
graph and is exercised by the integration test at the bottom when DATABASE_URL is set.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from decision_graph import report

DSN = os.environ.get("DATABASE_URL")


def decision(node_id=1, status="reconstructed", title="change default redirect code to 303"):
    return {
        "node_id": node_id,
        "status": status,
        "title": title,
        "thread_key": "thread:1:pr-5898",
        "decided_at": datetime(2026, 1, 25, tzinfo=timezone.utc),
        "source_type": "release",
        "source_ref": "3.1.0",
        "source_url": "https://github.com/pallets/flask/releases/tag/3.1.0",
    }


def edge(edge_type="motivated_by", tier="explicit", outward=True, ref="5895"):
    return {
        "src_node_id": 1,
        "dst_node_id": 2,
        "edge_type": edge_type,
        "evidence_tier": tier,
        "extractor": "synthesis_closes_cluster",
        "other_type": "issue",
        "other_ref": ref,
        "other_title": 'use "303" by default',
        "other_url": f"https://github.com/pallets/flask/issues/{ref}",
        "outward": outward,
    }


def data(decisions=None, edges=None, clusters=238, decisions_count=15):
    d = decisions if decisions is not None else [decision()]
    return {
        "decisions": d,
        "evidence": {x["node_id"]: (edges if edges is not None else [edge()]) for x in d},
        "coverage": {
            "clusters": clusters,
            "decisions": decisions_count,
            "pull_requests": 224,
            "issues": 81,
            "commits": 387,
        },
        "tiers": [{"tier": "explicit", "edges": 1248}, {"tier": "corroborated", "edges": 76}],
        "annotations": {x["node_id"]: "implemented by PR #5898, merged 2026-01-25" for x in d},
        "generated_at": datetime(2026, 9, 5, 15, 14, tzinfo=timezone.utc),
    }


class HonestyTest(unittest.TestCase):
    def test_the_page_states_what_it_does_not_cover(self) -> None:
        # The failure this guards is silence, not error: a page listing 15 decisions and
        # saying nothing about the other 223 clusters is a coverage claim by omission.
        html = report.render_html(data(), "pallets/flask")
        self.assertIn("223 clusters produced no decision", html)
        self.assertIn("not the repository's history", html)

    def test_coverage_is_shown_as_a_ratio_not_just_a_count(self) -> None:
        html = report.render_html(data(), "pallets/flask")
        self.assertIn("6.3%", html)

    def test_the_cluster_key_is_labelled_as_a_cluster(self) -> None:
        # The thread_key names the cluster and for 6 of 15 Decisions names a pull request
        # that never merged (#19). Printed without that warning it reads as the work.
        html = report.render_html(data(), "pallets/flask")
        self.assertIn("names the cluster", html)

    def test_the_credited_implementer_and_merge_date_are_shown(self) -> None:
        self.assertIn("PR #5898, merged 2026-01-25", report.render_html(data(), "x"))

    def test_an_empty_graph_does_not_divide_by_zero(self) -> None:
        html = report.render_html(data(decisions=[], clusters=0, decisions_count=0), "x")
        self.assertIn("0.0%", html)


class EvidenceTest(unittest.TestCase):
    def test_every_edge_shows_its_tier(self) -> None:
        html = report.render_html(data(edges=[edge(tier="corroborated")]), "x")
        self.assertIn('class="tier corroborated"', html)

    def test_edge_direction_is_shown(self) -> None:
        # `motivated_by` out of a Decision and `implements` into it are different claims
        # about the same pair, and an edge-type column alone reads them as one.
        outward = report.render_html(data(edges=[edge(outward=True)]), "x")
        inward = report.render_html(data(edges=[edge(outward=False)]), "x")
        self.assertIn("&rarr;", outward)
        self.assertIn("&larr;", inward)

    def test_artifacts_link_to_github(self) -> None:
        html = report.render_html(data(), "x")
        self.assertIn('href="https://github.com/pallets/flask/issues/5895"', html)

    def test_titles_are_html_escaped(self) -> None:
        # Real titles contain quotes and angle brackets; one unescaped breaks the table
        # silently, and the page still renders looking merely odd.
        html = report.render_html(data(edges=[edge()]), "x")
        self.assertIn("&quot;303&quot;", html)


class DiagramTest(unittest.TestCase):
    def test_the_diagram_names_the_tier_as_well_as_drawing_it(self) -> None:
        mmd = report._decision_mermaid(decision(), [edge(tier="corroborated")])
        self.assertIn("==>", mmd)
        self.assertIn("corroborated", mmd)

    def test_a_quote_in_a_title_cannot_break_the_diagram(self) -> None:
        mmd = report._decision_mermaid(decision(), [edge()])
        self.assertIn("#quot;", mmd)
        self.assertNotIn('"303"', mmd)

    def test_it_renders_without_a_module_script(self) -> None:
        # THE bug this page would otherwise ship with: `dg report` writes a file that is
        # opened from disk, and Chrome refuses a module import from a remote origin on a
        # file:// page. The module form renders every diagram as nothing, silently, on
        # exactly the path the command produces.
        html = report.render_html(data(), "x")
        self.assertNotIn('type="module"', html)
        self.assertIn("<script src=", html)

    def test_the_diagram_source_survives_as_the_offline_fallback(self) -> None:
        # If the CDN is unreachable, mermaid never replaces the <pre>, so what stays on the
        # page is the diagram source -- readable, and better than an empty box.
        html = report.render_html(data(), "x")
        self.assertIn('<pre class="mermaid">graph LR', html)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class CollectTest(unittest.TestCase):
    """`collect` against the real schema — the queries are the half fixtures cannot check."""

    def setUp(self) -> None:
        from decision_graph import db

        self.conn = db.connect(DSN)

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    def _repo(self) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM node WHERE node_type = 'repository' ORDER BY id LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    def test_every_query_runs_against_the_real_schema(self) -> None:
        repo = self._repo()
        if repo is None:
            self.skipTest("no repository ingested")
        out = report.collect(self.conn, repo)
        self.assertEqual(
            set(out),
            {"decisions", "evidence", "coverage", "tiers", "annotations", "generated_at"},
        )
        self.assertIn("clusters", out["coverage"])

    def test_the_rendered_page_is_html(self) -> None:
        repo = self._repo()
        if repo is None:
            self.skipTest("no repository ingested")
        html = report.render_html(report.collect(self.conn, repo), "test/repo")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)


if __name__ == "__main__":
    unittest.main()
