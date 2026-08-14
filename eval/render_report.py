"""Render eval/results.json into a reviewable adjudication worksheet.

Generated from results.json rather than hand-written, so re-running the evaluation
regenerates the report and the two cannot drift.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results.json"
OUT = ROOT / "report.html"

CATEGORY_LABEL = {
    "flagship": "Flagship",
    "causal": "Causal reconstruction",
    "impact": "Impact analysis",
    "retrieval": "Retrieval",
    "negative": "Correct refusal",
    "temporal": "Point-in-time",
}

MAX_VISIBLE_PATHS = 5


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


# Anything the annotation flags as a broken claim. Styled as a warning rather than as
# ordinary detail so it cannot be skimmed past.
WARN_MARKERS = ("NOT MERGED", "no current implementer")


def render_node(text, extra_class: str = "") -> str:
    """Render a node label, splitting off the trailing `[...]` annotation if present.

    Split from the RIGHT and only when the string closes with `]`: real flask titles
    contain brackets (`typing.IO[bytes]`), and a left split would tear one of those in
    half. The annotation is always appended last, so the rightmost match is the only
    candidate that can be one.
    """
    label = str(text or "")
    head, sep, tail = label.rpartition("  [")
    note = ""
    if sep and tail.endswith("]"):
        body = tail[:-1]
        cls = "node-note warn" if any(m in body for m in WARN_MARKERS) else "node-note"
        note = f'<span class="{cls}">{e(body)}</span>'
    else:
        head = label
    classes = " ".join(filter(None, ("node", extra_class)))
    return f'<div class="{classes}">{e(head)}{note}</div>'


def render_ref(source_ref) -> str:
    if not source_ref:
        return ""
    # A bare `thread:...` is the union-find cluster id, and the PR number inside it is
    # whichever attempt sorted first -- not necessarily the one that shipped. Labelling it
    # stops it reading as an attribution (issue #19).
    if str(source_ref).startswith("thread:"):
        return f'<span class="ref"><span class="ref-kind">cluster</span>{e(source_ref)}</span>'
    return f'<span class="ref">{e(source_ref)}</span>'


def render_step(step: dict) -> str:
    arrow = "→" if step["direction"] == "forward" else "←"
    return f"""
      <div class="step">
        <span class="conn">{arrow}</span>
        <span class="etype">{e(step["edge_type"])}</span>
        <span class="tier tier-{e(step["tier"])}">{e(step["tier"])}</span>
        <span class="extractor">{e(step["extractor"])}</span>
        {render_ref(step.get("source_ref"))}
      </div>
      {render_node(step["to_node"])}"""


def render_path(path: dict, index: int) -> str:
    steps = "".join(render_step(s) for s in path["steps"])
    return f"""
    <li class="path">
      <div class="path-head">
        <span class="path-n">path {index}</span>
        <span class="tier tier-{e(path["tier"])}">{e(path["tier"])}</span>
        <span class="depth">{path["depth"]} hop{"s" if path["depth"] != 1 else ""}</span>
      </div>
      <div class="trace">
        {render_node(path["start"], "node-start")}{steps}
      </div>
    </li>"""


def render_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return '<p class="empty">No candidate start node matched.</p>'
    rows = "".join(
        f"""<tr{' class="chosen"' if i == 0 else ''}>
              <td class="mono">{e(c["node_type"])}:{e(c["node_id"])}</td>
              <td>{e(c["title"] or c["external_id"])}</td>
              <td class="mono">{e(c["match"])}</td>
              <td class="num">{c["score"]:.2f}</td>
            </tr>"""
        for i, c in enumerate(candidates)
    )
    return f"""
      <table class="cands">
        <thead><tr><th>node</th><th>title</th><th>match</th><th>score</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>"""


def render_query(r: dict) -> str:
    paths = r["paths"]
    visible = paths[:MAX_VISIBLE_PATHS]
    hidden = paths[MAX_VISIBLE_PATHS:]

    path_html = "".join(render_path(p, i + 1) for i, p in enumerate(visible))
    if hidden:
        rest = "".join(
            render_path(p, i + 1 + MAX_VISIBLE_PATHS) for i, p in enumerate(hidden)
        )
        path_html += f"""
        <li class="more">
          <details>
            <summary>{len(hidden)} further path{"s" if len(hidden) != 1 else ""}</summary>
            <ul class="paths">{rest}</ul>
          </details>
        </li>"""

    if not paths:
        path_html = f'<li class="empty">No path returned. <span class="mono">{e(r["explanation"])}</span></li>'

    contract = ""
    if r.get("contract"):
        check = r.get("contract_check") or ""
        cls = {"HELD": "held", "VIOLATED": "violated", "COMPARE": "compare"}.get(check, "")
        contract = f"""
        <div class="contract {cls}">
          <span class="contract-label">Engine contract</span>
          <span class="contract-state">{e(check)}</span>
          <p>{e(r["contract"])}</p>
        </div>"""

    as_of = (
        f'<span class="meta-chip">as of {e(r["as_of"][:10])}</span>' if r.get("as_of") else ""
    )

    verify = (
        f'<div class="verify"><span class="verify-label">Check against flask</span><p>{e(r["verify"])}</p></div>'
        if r.get("verify")
        else ""
    )

    qid = e(r["id"])
    return f"""
  <article class="q" id="q-{qid}">
    <header class="q-head">
      <div class="q-id">{qid}</div>
      <div class="q-meta">
        <span class="cat">{e(CATEGORY_LABEL.get(r["category"], r["category"]))}</span>
        <span class="meta-chip mode-{e(r["mode"])}">{e(r["mode"])}</span>
        <span class="meta-chip">depth {r["depth"]}</span>
        {as_of}
        <span class="meta-chip {'ans' if r['answered'] else 'noans'}">{"answered" if r["answered"] else "no answer"}</span>
      </div>
    </header>

    <p class="query"><span class="q-label">Query</span> <code>{e(r["query"])}</code></p>
    <p class="intent">{e(r["intent"])}</p>

    {contract}

    <section class="block">
      <h4>Retrieval candidates</h4>
      {render_candidates(r["candidates"])}
    </section>

    <section class="block">
      <h4>Traversal <span class="count">{len(paths)} path{"s" if len(paths) != 1 else ""}</span></h4>
      <ul class="paths">{path_html}</ul>
    </section>

    {verify}

    <footer class="verdict" data-q="{qid}">
      <span class="v-label">Verdict</span>
      <label><input type="radio" name="v-{qid}" value="correct"> correct</label>
      <label><input type="radio" name="v-{qid}" value="partial"> partial</label>
      <label><input type="radio" name="v-{qid}" value="incorrect"> incorrect</label>
      <input class="v-note" type="text" placeholder="note (optional)" data-note="{qid}">
    </footer>
  </article>"""


def build() -> str:
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    s = data["summary"]
    results = data["results"]

    f1 = next(r for r in results if r["id"] == "F1")
    t1 = next(r for r in results if r["id"] == "T1")
    f2 = next(r for r in results if r["id"] == "F2")
    t2 = next(r for r in results if r["id"] == "T2")

    tiers: dict[str, int] = {}
    for r in results:
        for p in r["paths"]:
            tiers[p["tier"]] = tiers.get(p["tier"], 0) + 1

    cards = "".join(render_query(r) for r in results)

    tier_bar = "".join(
        f'<span class="tb tier-{k}" style="flex:{v}"><span>{k} {v}</span></span>'
        for k, v in sorted(tiers.items(), key=lambda kv: -kv[1])
    )

    return TEMPLATE.format(
        cards=cards,
        total=s["total"],
        answered=s["answered"],
        refused=s["returned_no_answer"],
        contracts_held=s["contracts_held"],
        contracts_checked=s["contracts_checked"],
        f1_paths=len(f1["paths"]),
        t1_paths=len(t1["paths"]),
        f2_paths=len(f2["paths"]),
        t2_paths=len(t2["paths"]),
        tier_bar=tier_bar,
        generated=data["generated_at"][:16].replace("T", " "),
    )


TEMPLATE = """<title>Decision Graph — Evaluation Worksheet</title>
<style>
:root {{
  --ground:   #F6F7F9;
  --surface:  #FFFFFF;
  --sunk:     #EEF1F4;
  --ink:      #171A1F;
  --muted:    #5C6672;
  --faint:    #8A939E;
  --rule:     #DCE1E7;
  --accent:   #1B6B63;
  --accent-soft: #E4F0EE;
  --explicit:     #4A6C88;
  --corroborated: #2E7D5B;
  --inferred:     #A6742E;
  --held:     #2E7D5B;
  --violated: #B04A3E;
  --halt-soft: #FBEDEA;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --serif: ui-serif, "Iowan Old Style", Georgia, "Times New Roman", serif;
  --mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:   #101317;
    --surface:  #171B21;
    --sunk:     #1D222A;
    --ink:      #E4E8ED;
    --muted:    #98A2AE;
    --faint:    #6B7683;
    --rule:     #272E37;
    --accent:   #58B5A8;
    --accent-soft: #16302E;
    --explicit:     #7FA3C2;
    --corroborated: #5DB98C;
    --inferred:     #D2A055;
    --held:     #5DB98C;
    --violated: #D97A6C;
    --halt-soft: #2A1C1A;
  }}
}}
:root[data-theme="dark"] {{
  --ground:#101317; --surface:#171B21; --sunk:#1D222A; --ink:#E4E8ED;
  --muted:#98A2AE; --faint:#6B7683; --rule:#272E37; --accent:#58B5A8;
  --accent-soft:#16302E; --explicit:#7FA3C2; --corroborated:#5DB98C;
  --inferred:#D2A055; --held:#5DB98C; --violated:#D97A6C; --halt-soft:#2A1C1A;
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: var(--sans); font-size: 15px; line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 62rem; margin: 0 auto; padding: 3.5rem 1.5rem 6rem; }}
.lede {{ max-width: 38rem; }}

h1 {{
  font-family: var(--serif); font-size: clamp(1.9rem, 4vw, 2.6rem);
  line-height: 1.15; margin: 0 0 .4rem; text-wrap: balance; font-weight: 600;
}}
.sub {{ color: var(--muted); margin: 0 0 2rem; font-size: 1.02rem; }}
h2 {{
  font-family: var(--serif); font-size: 1.35rem; font-weight: 600;
  margin: 3.5rem 0 1rem; padding-bottom: .5rem; border-bottom: 1px solid var(--rule);
}}
h4 {{
  font-size: .72rem; text-transform: uppercase; letter-spacing: .09em;
  color: var(--faint); margin: 0 0 .55rem; font-weight: 600;
}}
h4 .count {{ text-transform: none; letter-spacing: 0; color: var(--muted); font-weight: 400; }}
p {{ margin: 0 0 .9rem; }}
code, .mono {{ font-family: var(--mono); font-size: .86em; }}

.note {{
  background: var(--accent-soft); border-left: 3px solid var(--accent);
  padding: 1rem 1.15rem; margin: 1.5rem 0 0; max-width: 38rem;
}}
.note p {{ margin: 0; font-size: .93rem; }}
.note strong {{ color: var(--accent); }}
.note.fixed {{ background: var(--sunk); border-left-color: var(--held); }}
.note.fixed strong {{ color: var(--held); }}

.stats {{ display: flex; flex-wrap: wrap; gap: 1px; background: var(--rule);
  border: 1px solid var(--rule); margin: 2rem 0 .75rem; }}
.stat {{ background: var(--surface); padding: .9rem 1.15rem; flex: 1 1 8rem; }}
.stat .n {{ font-size: 1.6rem; font-family: var(--serif); font-variant-numeric: tabular-nums;
  line-height: 1.1; display: block; }}
.stat .l {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .07em; color: var(--faint); }}
.stat.good .n {{ color: var(--held); }}

.tiers {{ display: flex; height: 2rem; border: 1px solid var(--rule); overflow: hidden; }}
.tb {{ display: flex; align-items: center; justify-content: center; min-width: 0; }}
.tb span {{ font-size: .7rem; color: #fff; letter-spacing: .04em; white-space: nowrap;
  padding: 0 .4rem; overflow: hidden; text-overflow: ellipsis; }}
.tb.tier-explicit {{ background: var(--explicit); }}
.tb.tier-corroborated {{ background: var(--corroborated); }}
.tb.tier-inferred {{ background: var(--inferred); }}
.caption {{ font-size: .82rem; color: var(--faint); margin: .5rem 0 0; }}

.q {{
  background: var(--surface); border: 1px solid var(--rule);
  padding: 1.5rem 1.6rem 1.1rem; margin: 0 0 1.25rem;
}}
.q-head {{ display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  padding-bottom: .85rem; margin-bottom: .95rem; border-bottom: 1px solid var(--rule); }}
.q-id {{ font-family: var(--mono); font-size: 1.05rem; font-weight: 700; color: var(--accent); }}
.q-meta {{ display: flex; gap: .45rem; flex-wrap: wrap; align-items: center; margin-left: auto; }}
.cat {{ font-size: .82rem; color: var(--muted); }}
.meta-chip {{
  font-family: var(--mono); font-size: .7rem; padding: .16rem .5rem;
  background: var(--sunk); color: var(--muted); border-radius: 2px; white-space: nowrap;
}}
.meta-chip.mode-why {{ color: var(--accent); }}
.meta-chip.mode-impact {{ color: var(--explicit); }}
.meta-chip.ans {{ color: var(--held); }}
.meta-chip.noans {{ color: var(--muted); }}

.query {{ margin-bottom: .45rem; }}
.q-label, .v-label, .verify-label, .contract-label {{
  font-size: .68rem; text-transform: uppercase; letter-spacing: .09em;
  color: var(--faint); font-weight: 600; margin-right: .5rem;
}}
.query code {{ background: var(--sunk); padding: .18rem .45rem; border-radius: 2px; }}
.intent {{ color: var(--muted); font-size: .93rem; max-width: 46rem; }}

.contract {{ background: var(--sunk); padding: .75rem .95rem; margin: 0 0 1.1rem;
  border-left: 3px solid var(--faint); }}
.contract.held {{ border-left-color: var(--held); }}
.contract.violated {{ border-left-color: var(--violated); }}
.contract p {{ margin: .3rem 0 0; font-size: .88rem; color: var(--muted); }}
.contract-state {{ font-family: var(--mono); font-size: .74rem; font-weight: 700; }}
.contract.held .contract-state {{ color: var(--held); }}
.contract.violated .contract-state {{ color: var(--violated); }}

.block {{ margin: 0 0 1.15rem; }}
table.cands {{ width: 100%; border-collapse: collapse; font-size: .86rem; }}
table.cands th {{ text-align: left; font-weight: 600; font-size: .7rem;
  text-transform: uppercase; letter-spacing: .06em; color: var(--faint);
  padding: .25rem .5rem .25rem 0; border-bottom: 1px solid var(--rule); }}
table.cands td {{ padding: .32rem .5rem .32rem 0; border-bottom: 1px solid var(--rule);
  vertical-align: top; }}
table.cands tr.chosen td {{ font-weight: 600; }}
table.cands tr.chosen td:first-child {{ box-shadow: inset 2px 0 0 var(--accent); padding-left: .5rem; }}
td.num {{ font-variant-numeric: tabular-nums; text-align: right; }}

ul.paths {{ list-style: none; margin: 0; padding: 0; display: flex;
  flex-direction: column; gap: .75rem; }}
.path-head {{ display: flex; gap: .5rem; align-items: center; margin-bottom: .3rem; }}
.path-n {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .07em; color: var(--faint); }}
.depth {{ font-size: .74rem; color: var(--faint); font-family: var(--mono); }}
.trace {{ font-family: var(--mono); font-size: .8rem; background: var(--sunk);
  padding: .7rem .85rem; overflow-x: auto; border-radius: 2px; }}
.node {{ white-space: nowrap; padding: .1rem 0; }}
.node-start {{ font-weight: 700; }}
.node-note {{ color: var(--accent); font-weight: 400; margin-left: .5rem; }}
.node-note::before {{ content: "· "; color: var(--faint); }}
.node-note.warn {{ color: var(--violated); font-weight: 700; }}
.step {{ display: flex; gap: .5rem; align-items: center; padding: .12rem 0 .12rem 1.1rem;
  border-left: 1px solid var(--rule); margin-left: .35rem; white-space: nowrap; }}
.conn {{ color: var(--faint); }}
.etype {{ color: var(--ink); font-weight: 600; }}
.extractor {{ color: var(--faint); font-size: .92em; }}
.ref {{ color: var(--faint); font-size: .88em; opacity: .75; }}
.ref-kind {{ text-transform: uppercase; letter-spacing: .06em; font-size: .82em;
  padding: 0 .3rem; margin-right: .35rem; background: var(--rule); border-radius: 2px; }}

.tier {{ font-size: .66rem; text-transform: uppercase; letter-spacing: .05em;
  padding: .08rem .4rem; border-radius: 2px; color: #fff; font-weight: 600;
  font-family: var(--mono); }}
.tier-explicit {{ background: var(--explicit); }}
.tier-corroborated {{ background: var(--corroborated); }}
.tier-inferred {{ background: var(--inferred); }}

.empty {{ color: var(--muted); font-size: .89rem; font-style: italic; list-style: none; }}
.more summary {{ cursor: pointer; font-size: .82rem; color: var(--accent); padding: .2rem 0; }}
.more ul {{ margin-top: .7rem; }}

.verify {{ border-top: 1px solid var(--rule); padding-top: .8rem; margin-top: .3rem; }}
.verify p {{ margin: .25rem 0 0; font-size: .9rem; color: var(--muted); max-width: 46rem; }}

.verdict {{ display: flex; align-items: center; gap: .9rem; flex-wrap: wrap;
  margin-top: 1rem; padding-top: .85rem; border-top: 2px solid var(--rule); }}
.verdict label {{ font-size: .87rem; display: flex; align-items: center; gap: .3rem;
  cursor: pointer; color: var(--muted); }}
.verdict input[type=radio] {{ accent-color: var(--accent); }}
.verdict.done {{ border-top-color: var(--accent); }}
.v-note {{ flex: 1 1 12rem; min-width: 8rem; font-family: var(--sans); font-size: .85rem;
  padding: .3rem .5rem; border: 1px solid var(--rule); background: var(--ground);
  color: var(--ink); border-radius: 2px; }}
input:focus-visible, summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

.bar {{ position: sticky; bottom: 0; background: var(--surface); border-top: 1px solid var(--rule);
  padding: .8rem 1.5rem; display: flex; gap: 1rem; align-items: center; flex-wrap: wrap;
  margin: 2rem -1.5rem -6rem; }}
.bar .prog {{ font-size: .88rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
button {{ font-family: var(--sans); font-size: .86rem; padding: .42rem .9rem;
  background: var(--accent); color: #fff; border: none; border-radius: 2px; cursor: pointer; }}
button.ghost {{ background: transparent; color: var(--muted); border: 1px solid var(--rule); }}
button:hover {{ opacity: .9; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>

<div class="wrap">
  <div class="lede">
    <h1>Evaluation worksheet</h1>
    <p class="sub">Decision Graph against <code>pallets/flask</code> — PRD v3.1 §9.
    18 queries: 2 flagship plus 16 harder cases. Generated {generated}.</p>

    <div class="note fixed">
      <p><strong>Regenerated after a defect found in adjudication.</strong>
      A spot-check of issue 5912 against GitHub found it <em>closed as not planned</em>
      with no linked pull requests. The extraction was correct — all five sources really
      do contain <code>fixes #5912</code> — but the §5.1 rubric never checked whether the
      work <em>landed</em>, and a closing keyword survives rejection intact.
      <strong>12 of 20 Decisions rested on pull requests that were never merged.</strong>
      Validation now requires a merged pull request; 7 Decisions were retracted and 13
      remain, every one with a merged implementer. Issues #16 and #17.
      Query H1 is now the regression guard for that defect.</p>
    </div>

    <div class="note">
      <p><strong>Read the annotation, not the cluster id.</strong>
      A Decision's <code>thread:…</code> key names its cluster, and that name is chosen
      order-independently — PR-preferring, then lowest number — so when a change took two
      attempts the cluster is named after the <em>abandoned</em> one. 3 of 13 Decisions
      here are. Every Decision node in a trace now carries the pull request the graph
      actually credits it to and that PR's merge date, so a stale label and a stale edge
      no longer look alike. <code>NOT MERGED</code> or a missing implementer renders in
      red; neither should appear. Issue #19 — display only, the keys themselves are
      unchanged.</p>
    </div>

    <div class="note">
      <p><strong>Nothing here is graded.</strong> §9 requires answers checked against the
      repository's real history, and a system scoring its own output against its own graph
      would be measuring self-consistency, not correctness. Every verdict below is blank
      and waiting for you. The one thing the runner does assert is the
      <em>engine contract</em> — mechanical properties like “must return no answer for an
      artifact outside the window” — because those are claims about the code, not about flask.</p>
    </div>
  </div>

  <div class="stats">
    <div class="stat"><span class="n">{total}</span><span class="l">queries</span></div>
    <div class="stat"><span class="n">{answered}</span><span class="l">answered</span></div>
    <div class="stat"><span class="n">{refused}</span><span class="l">returned nothing</span></div>
    <div class="stat good"><span class="n">{contracts_held}/{contracts_checked}</span><span class="l">contracts held</span></div>
  </div>

  <div class="tiers">{tier_bar}</div>
  <p class="caption">Evidence tier across every path returned. No path used the inferred
  fallback — the graph holds no inferred edges, so that branch is exercised only by the
  seeded fixture in the test suite.</p>

  <h2>Point-in-time control</h2>
  <p>T1 and T2 re-run the two flagship queries through the <code>valid_from</code>/<code>valid_to</code>
  window. If the time filter were silently ignored, both would be unchanged.</p>
  <div class="stats">
    <div class="stat"><span class="n">{f1_paths} → {t1_paths}</span><span class="l">F1 → T1, as of 2025-06</span></div>
    <div class="stat"><span class="n">{f2_paths} → {t2_paths}</span><span class="l">F2 → T2, as of 2026-12</span></div>
  </div>
  <p class="caption">The past query collapses to nothing; the future query is unchanged.
  The filter restricts in one direction only, which is the expected behaviour.</p>

  <h2>Queries</h2>
  {cards}

  <div class="bar">
    <span class="prog" id="prog">0 of {total} adjudicated</span>
    <button id="export">Copy verdicts</button>
    <button class="ghost" id="clear">Clear all</button>
  </div>
</div>

<script>
(function () {{
  var KEY = "dg-eval-verdicts-v1";
  var store = {{}};
  try {{ store = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (err) {{ store = {{}}; }}

  function persist() {{
    try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (err) {{ /* private mode */ }}
  }}

  function refresh() {{
    var ids = Object.keys(store).filter(function (k) {{ return store[k] && store[k].v; }});
    document.querySelectorAll(".verdict").forEach(function (f) {{
      var rec = store[f.dataset.q];
      f.classList.toggle("done", !!(rec && rec.v));
    }});
    document.getElementById("prog").textContent =
      ids.length + " of " + document.querySelectorAll(".verdict").length + " adjudicated";
  }}

  document.querySelectorAll(".verdict").forEach(function (f) {{
    var id = f.dataset.q;
    var rec = store[id] || {{}};
    if (rec.v) {{
      var hit = f.querySelector('input[value="' + rec.v + '"]');
      if (hit) hit.checked = true;
    }}
    var note = f.querySelector(".v-note");
    if (rec.n) note.value = rec.n;

    f.addEventListener("change", function (ev) {{
      if (ev.target.type !== "radio") return;
      store[id] = Object.assign({{}}, store[id], {{ v: ev.target.value }});
      persist(); refresh();
    }});
    note.addEventListener("input", function () {{
      store[id] = Object.assign({{}}, store[id], {{ n: note.value }});
      persist();
    }});
  }});

  document.getElementById("export").addEventListener("click", function () {{
    var lines = ["Decision Graph — §9 adjudication", ""];
    document.querySelectorAll(".verdict").forEach(function (f) {{
      var id = f.dataset.q, rec = store[id] || {{}};
      lines.push(id + ": " + (rec.v || "—") + (rec.n ? "  // " + rec.n : ""));
    }});
    var text = lines.join("\\n");
    var btn = document.getElementById("export");
    function done() {{ btn.textContent = "Copied"; setTimeout(function () {{ btn.textContent = "Copy verdicts"; }}, 1600); }}
    if (navigator.clipboard) {{
      navigator.clipboard.writeText(text).then(done, function () {{ window.prompt("Copy:", text); }});
    }} else {{ window.prompt("Copy:", text); }}
  }});

  document.getElementById("clear").addEventListener("click", function () {{
    store = {{}}; persist();
    document.querySelectorAll(".verdict input[type=radio]").forEach(function (r) {{ r.checked = false; }});
    document.querySelectorAll(".v-note").forEach(function (n) {{ n.value = ""; }});
    refresh();
  }});

  refresh();
}})();
</script>
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
