"""`dg report` — the graph as a page you can browse (issue #73).

`dg query` answers one question and `--format mermaid` draws one answer. Neither shows the
graph: what was reconstructed, what each Decision rests on, how the evidence tiers fall, or
what the system did *not* find. For a tool whose entire product is a graph, that was the
missing surface, and the only way to see it was a psql prompt and knowledge of the schema.

Three constraints, and the third is the one that makes this honest:

1. **Self-contained.** One file, opened from disk, no server. Mermaid is the single script
   it fetches, and the page degrades to the same content as text when that fetch fails — a
   diagram that silently disappears offline would be worse than never drawing one.
2. **Generated from the graph, now.** Not from `eval/results.json`, which is the record of
   an evaluation run and must stay one.
3. **It states what it does not cover.** A page showing 15 Decisions and saying nothing
   about the 223 clusters that produced none is a coverage claim made by omission. The
   ratio and the reason are on the page, at the top, not in a footnote.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from . import trace

# The UMD build specifically: it defines a global and works from a file:// page, where
# the ESM build cannot be imported at all. Pinned, so a future release cannot change
# what a saved report renders.
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

# One query per section, kept here rather than inline so the page is obviously a view over
# the graph and every number on it can be traced to the SQL that produced it.
DECISIONS_SQL = """
SELECT d.node_id,
       d.status::text                         AS status,
       n.title,
       n.thread_key,
       d.decided_at,
       src.node_type::text                    AS source_type,
       src.external_id                        AS source_ref,
       src.url                                AS source_url
FROM decision d
JOIN node n ON n.id = d.node_id
LEFT JOIN node src ON src.id = d.source_artifact_node_id
WHERE n.repo_node_id = %s
ORDER BY d.decided_at DESC NULLS LAST, d.node_id
"""

# Everything the Decision links to, in both directions, with the tier. This is the evidence
# the rubric accepted, which is the only thing that makes a reconstructed Decision more than
# an assertion.
EVIDENCE_SQL = """
SELECT e.src_node_id, e.dst_node_id, e.edge_type::text AS edge_type,
       e.evidence_tier::text AS evidence_tier, e.extractor,
       o.node_type::text AS other_type, o.external_id AS other_ref,
       o.title AS other_title, o.url AS other_url,
       (e.src_node_id = ANY(%(ids)s)) AS outward
FROM edge e
JOIN node o ON o.id = CASE WHEN e.src_node_id = ANY(%(ids)s)
                           THEN e.dst_node_id ELSE e.src_node_id END
WHERE (e.src_node_id = ANY(%(ids)s) OR e.dst_node_id = ANY(%(ids)s))
  AND e.valid_to IS NULL
ORDER BY e.edge_type, o.external_id
"""

COVERAGE_SQL = """
SELECT
    (SELECT count(DISTINCT thread_key) FROM node
      WHERE repo_node_id = %(repo)s AND thread_key IS NOT NULL
        AND node_type <> 'decision')                                   AS clusters,
    (SELECT count(*) FROM decision d JOIN node n ON n.id = d.node_id
      WHERE n.repo_node_id = %(repo)s)                                 AS decisions,
    (SELECT count(*) FROM node WHERE repo_node_id = %(repo)s
        AND node_type = 'pull_request')                                AS pull_requests,
    (SELECT count(*) FROM node WHERE repo_node_id = %(repo)s
        AND node_type = 'issue')                                       AS issues,
    (SELECT count(*) FROM node WHERE repo_node_id = %(repo)s
        AND node_type = 'commit')                                      AS commits
"""

# Either endpoint, not just the source. A `reviewed` edge runs person -> pull_request and a
# person belongs to no repository, so scoping by the source alone dropped every review from
# the count -- and made this page disagree with eval/figures.sql, which is the script whose
# whole purpose is that the numbers can be checked.
TIERS_SQL = """
SELECT e.evidence_tier::text AS tier, count(*) AS edges
FROM edge e
JOIN node s ON s.id = e.src_node_id
JOIN node d ON d.id = e.dst_node_id
WHERE (s.repo_node_id = %(repo)s OR d.repo_node_id = %(repo)s)
  AND e.valid_to IS NULL
GROUP BY 1 ORDER BY 2 DESC
"""


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def collect(conn, repo_node_id: int) -> dict:
    """Everything the page shows, read in four queries.

    Returned as plain data rather than rendered directly so the tests can assert what the
    page will contain without parsing HTML, and so a future JSON export is the same read.
    """
    decisions = [dict(r) for r in conn.execute(DECISIONS_SQL, (repo_node_id,)).fetchall()]
    ids = [d["node_id"] for d in decisions]

    evidence: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for row in conn.execute(EVIDENCE_SQL, {"ids": ids}).fetchall():
            owner = row["src_node_id"] if row["outward"] else row["dst_node_id"]
            if owner in evidence:
                evidence[owner].append(dict(row))

    coverage = dict(conn.execute(COVERAGE_SQL, {"repo": repo_node_id}).fetchone())
    tiers = [dict(r) for r in conn.execute(TIERS_SQL, {"repo": repo_node_id}).fetchall()]

    annotations = trace.decision_annotations(conn, ids)
    return {
        "decisions": decisions,
        "evidence": evidence,
        "coverage": coverage,
        "tiers": tiers,
        "annotations": annotations,
        "generated_at": datetime.now(timezone.utc),
    }


def _decision_mermaid(decision: dict, edges: list[dict]) -> str:
    """One Decision and the artifacts it links to, as a diagram.

    Built from the stored edges rather than from a traversal: this is not an answer to a
    question, it is what the rubric accepted, and those are different claims. Only edges
    that exist are drawn, which is the same rule `diagram.py` follows for a walk.
    """
    lines = ["graph LR", f'  d{decision["node_id"]}{{{{"decision"}}}}']
    lines.append("  classDef decision fill:#fde68a,stroke:#b45309,stroke-width:2px;")
    lines.append(f'  class d{decision["node_id"]} decision;')
    for i, e in enumerate(edges):
        name = trace.ref(e["other_type"], e["other_ref"])
        title = (e["other_title"] or "")[:40].replace('"', "#quot;")
        label = f'{name}<br/>{title}' if title else name
        node = f"a{decision['node_id']}_{i}"
        shape = ("([", "])") if e["other_type"] == "issue" else ("[", "]")
        lines.append(f'  {node}{shape[0]}"{label}"{shape[1]}')
        arrow = {"corroborated": "==>", "inferred": "-.->"}.get(e["evidence_tier"], "-->")
        # Both the shape and the word, as in diagram.py: a reader who cannot tell a thick
        # line from a thin one must still be told which tier this edge carries.
        verb = f'{e["edge_type"].replace("_", " ")} · {e["evidence_tier"]}'
        if e["outward"]:
            lines.append(f'  d{decision["node_id"]} {arrow}|"{verb}"| {node}')
        else:
            lines.append(f'  {node} {arrow}|"{verb}"| d{decision["node_id"]}')
    return "\n".join(lines)


def _link(url: str | None, text: str) -> str:
    """A GitHub link when there is a URL, plain text when there is not.

    Kept as a function rather than an inline conditional inside an f-string: the version
    that was inline had to dodge its own quoting and became unreadable, which is exactly
    how an escaping bug gets in unnoticed.
    """
    return f'<a href="{_esc(url)}">{_esc(text)}</a>' if url else _esc(text)


def _evidence_row(e: dict) -> str:
    """One edge of a Decision's evidence, as a table row.

    The direction arrow is not decoration. `motivated_by` outward from the Decision and
    `implements` inward toward it are different claims about the same pair of artifacts,
    and a table that showed only the edge type would read them as one.
    """
    arrow = "&rarr;" if e["outward"] else "&larr;"
    name = _link(e["other_url"], trace.ref(e["other_type"], e["other_ref"]))
    title = _esc((e["other_title"] or "")[:70])
    tier = _esc(e["evidence_tier"])
    return (
        f"<tr><td>{_esc(e['edge_type'])}</td>"
        f"<td>{arrow}</td>"
        f'<td>{name} <span class="t">{title}</span></td>'
        f'<td><span class="tier {tier}">{tier}</span></td>'
        f'<td class="x">{_esc(e["extractor"])}</td></tr>'
    )


def render_html(data: dict, repo: str) -> str:
    cov = data["coverage"]
    clusters = cov["clusters"] or 0
    pct = (100.0 * cov["decisions"] / clusters) if clusters else 0.0
    tier_counts = {t["tier"]: t["edges"] for t in data["tiers"]}

    cards = []
    for d in data["decisions"]:
        edges = data["evidence"].get(d["node_id"], [])
        tiers = {e["evidence_tier"] for e in edges}
        badge = "corroborated" if "corroborated" in tiers else "explicit"
        note = data["annotations"].get(d["node_id"], "no current implementer")
        source = ""
        if d["source_ref"]:
            name = trace.ref(d["source_type"], d["source_ref"])
            source = (
                f'<a href="{_esc(d["source_url"])}">{_esc(name)}</a>'
                if d["source_url"]
                else _esc(name)
            )

        rows = "".join(_evidence_row(e) for e in edges)

        cards.append(f"""
<article class="card">
  <header>
    <span class="status {_esc(d['status'])}">{_esc(d['status'])}</span>
    <h3>{_esc(d['title'])}</h3>
  </header>
  <p class="meta">{note and _esc(note)}
     {' · decided ' + d['decided_at'].strftime('%Y-%m-%d') if d.get('decided_at') else ''}
     {' · from ' + source if source else ''}</p>
  <p class="key">cluster <code>{_esc(d['thread_key'])}</code>
     <span class="warn">names the cluster, not necessarily the work that landed</span></p>
  <table>
    <thead><tr><th>edge</th><th></th><th>artifact</th><th>tier</th><th>extractor</th></tr></thead>
    <tbody>{rows or '<tr><td colspan="5">no edges</td></tr>'}</tbody>
  </table>
  <details><summary>diagram</summary>
    <pre class="mermaid">{_esc(_decision_mermaid(d, edges))}</pre>
  </details>
</article>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>decision graph — {_esc(repo)}</title>
<style>
  :root {{ --bg:#fff; --fg:#1a1a1a; --dim:#666; --line:#e2e2e2; --card:#fafafa;
           --explicit:#166534; --corroborated:#155e75; --inferred:#92400e; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#111; --fg:#e8e8e8; --dim:#9a9a9a; --line:#333; --card:#1a1a1a;
             --explicit:#4ade80; --corroborated:#67e8f9; --inferred:#fbbf24; }}
  }}
  body {{ background:var(--bg); color:var(--fg); margin:0 auto; max-width:60rem; padding:2rem 1rem;
         font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:1.5rem; margin:0 0 .2rem; }}
  .sub {{ color:var(--dim); margin:0 0 1.5rem; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:1.5rem; padding:1rem; background:var(--card);
            border:1px solid var(--line); border-radius:8px; margin-bottom:1rem; }}
  .stat b {{ display:block; font-size:1.6rem; }}
  .stat span {{ color:var(--dim); font-size:.85rem; }}
  .caveat {{ border-left:3px solid var(--inferred); padding:.6rem .9rem; background:var(--card);
             margin:0 0 2rem; }}
  .card {{ border:1px solid var(--line); border-radius:8px; padding:1rem 1.2rem; margin:0 0 1rem;
           background:var(--card); }}
  .card header {{ display:flex; gap:.6rem; align-items:baseline; }}
  .card h3 {{ font-size:1.05rem; margin:0 0 .4rem; font-weight:600; }}
  .status {{ font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; padding:.15rem .45rem;
             border-radius:4px; border:1px solid var(--line); color:var(--dim); white-space:nowrap; }}
  .status.explicit {{ color:var(--explicit); border-color:currentColor; }}
  .meta {{ margin:.2rem 0; }}
  .key {{ color:var(--dim); font-size:.85rem; margin:.2rem 0 .8rem; }}
  .warn {{ color:var(--inferred); }}
  table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
  th {{ text-align:left; color:var(--dim); font-weight:500; border-bottom:1px solid var(--line); }}
  td {{ padding:.25rem .4rem .25rem 0; border-bottom:1px solid var(--line); vertical-align:top; }}
  .t {{ color:var(--dim); }}
  .x {{ color:var(--dim); font-family:ui-monospace,monospace; font-size:.75rem; }}
  .tier {{ font-size:.72rem; padding:.1rem .35rem; border-radius:3px; border:1px solid currentColor; }}
  .tier.explicit {{ color:var(--explicit); }}
  .tier.corroborated {{ color:var(--corroborated); }}
  .tier.inferred {{ color:var(--inferred); }}
  a {{ color:inherit; }}
  code {{ font-family:ui-monospace,monospace; font-size:.8em; }}
  details {{ margin-top:.6rem; }} summary {{ cursor:pointer; color:var(--dim); font-size:.85rem; }}
  /* Mermaid replaces the contents of pre.mermaid. Until (or unless) it does, this is the
     diagram source, which is readable on its own -- that is the offline fallback. */
  pre.mermaid {{ overflow-x:auto; font-size:.75rem; color:var(--dim); white-space:pre; }}
  footer {{ color:var(--dim); font-size:.8rem; border-top:1px solid var(--line); margin-top:2rem;
            padding-top:1rem; }}
</style></head>
<body>
<h1>decision graph — {_esc(repo)}</h1>
<p class="sub">{cov['decisions']} decisions reconstructed from {clusters} conversation
clusters · generated {data['generated_at']:%Y-%m-%d %H:%M} UTC</p>

<div class="stats">
  <div class="stat"><b>{cov['decisions']}</b><span>decisions</span></div>
  <div class="stat"><b>{clusters}</b><span>thread clusters</span></div>
  <div class="stat"><b>{pct:.1f}%</b><span>coverage</span></div>
  <div class="stat"><b>{cov['pull_requests']}</b><span>pull requests</span></div>
  <div class="stat"><b>{cov['issues']}</b><span>issues</span></div>
  <div class="stat"><b>{cov['commits']}</b><span>commits</span></div>
  <div class="stat"><b>{tier_counts.get('corroborated', 0)}</b><span>corroborated edges</span></div>
  <div class="stat"><b>{tier_counts.get('inferred', 0)}</b><span>inferred edges</span></div>
</div>

<p class="caveat"><strong>This is not the repository's history.</strong> It is what the §5.1
rubric could evidence: a motivating issue and merged work in one conversation.
{clusters - cov['decisions']} clusters produced no decision, most because no issue is
referenced from the work at all, and a refusal to assert is the intended outcome there
rather than a gap. Coverage, not precision, is this system's binding limit.</p>

{''.join(cards)}

<footer>Generated by <code>dg report</code> from the graph, not from an evaluation record.
Every artifact links to GitHub so any claim here can be checked against the repository.
Diagrams render with mermaid; if the script cannot load, the diagram source stays readable
in place.</footer>

<!-- A classic script tag, not an ES module. This file is opened from disk, and Chrome
     refuses a module import from a remote origin on a file:// page (origin "null"), so the
     module form renders every diagram as nothing on exactly the path the command produces.
     The UMD build defines a global and loads fine there. -->
<script src="{MERMAID_CDN}"></script>
<script>
  if (window.mermaid) {{
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({{
      startOnLoad: true,
      securityLevel: "strict",
      theme: dark ? "dark" : "default",
      // The dark theme still plates edge labels on near-white, which is unreadable here.
      themeVariables: dark
        ? {{ edgeLabelBackground: "#1a1a1a", lineColor: "#9a9a9a" }}
        : {{ edgeLabelBackground: "#fafafa" }},
    }});
  }}
</script>
</body></html>
"""
