"""Turning an Engine 2 answer into something a person can read (issue #69).

The old renderer printed the traversal and stopped: `issue:620`, `tier=explicit`,
`==<- motivated_by (synthesis_closes_cluster)`. Every one of those is true and none of them
is an answer. A reader had to know that 620 is a primary key, that `#5895` is the number
they actually wanted, that a `motivated_by` edge means the issue asked for the change, and
that `explicit` is the strongest tier rather than the weakest.

This module is the presentation layer that was missing. Three rules hold it in place:

1. **The summary is generated from the graph, never from a model.** A Decision with a
   `motivated_by` and an `implemented_by` edge determines an English sentence completely;
   producing it is templating, not inference. `dg ask` already follows this rule for
   refusals (#65), and the reason is the same — this system's whole value is that its
   claims are checkable, and a sentence nobody can trace back is not.
2. **Nothing is removed to make room.** Node ids, extractor names and edge directions are
   what an adjudicator uses; they move behind `--verbose` rather than disappearing, and the
   evidence tier moves *forward* because it is the most important thing on the page.
3. **A refusal gets the same care as an answer.** 8 of the 18 §9 outcomes are refusals. An
   answer that says only "no path found" tells the reader nothing about whether they asked
   the wrong question or found a real gap in the graph.
"""

from __future__ import annotations

import os
import sys

from . import trace
from .reasoning import Answer, Mode, Path
from .trace import TIER_STRENGTH


_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def _tier_colour(tier: str, text: str) -> str:
    # Inferred is the one a reader must not skim past, so it is the one that is yellow.
    return {"corroborated": _c("36", text), "inferred": _c("33", text)}.get(
        tier, _c("32", text)
    )


# How each edge type reads in a sentence, in both directions. The graph stores a direction
# and a type; a reader needs a verb. These are translations of the edge, not additions to
# it -- `motivated_by` from a Decision to an issue means that issue motivated the decision,
# which is what the edge says and all it says.
_EDGE_PHRASE = {
    ("motivated_by", "out"): "was motivated by",
    ("motivated_by", "in"): "motivated",
    ("implemented_by", "out"): "was implemented by",
    ("implemented_by", "in"): "implemented",
    ("implements", "out"): "implements",
    ("implements", "in"): "is implemented by",
    ("closes", "out"): "closes",
    ("closes", "in"): "was closed by",
    ("references", "out"): "references",
    ("references", "in"): "is referenced by",
    ("reviewed", "out"): "reviewed",
    ("reviewed", "in"): "was reviewed by",
    ("discussed_in", "out"): "was discussed in",
    ("discussed_in", "in"): "hosted discussion of",
    ("deployed_by", "out"): "shipped in",
    ("deployed_by", "in"): "shipped",
    ("supersedes", "out"): "supersedes",
    ("supersedes", "in"): "is superseded by",
    ("superseded_by", "out"): "is superseded by",
    ("superseded_by", "in"): "supersedes",
    ("created", "out"): "created",
    ("created", "in"): "was created by",
    ("depends_on", "out"): "depends on",
    ("depends_on", "in"): "is depended on by",
}


def phrase(edge_type: str, forward: bool) -> str:
    return _EDGE_PHRASE.get((edge_type, "out" if forward else "in"), edge_type)


def label(path: Path, node_id: int, *, verbose: bool = False) -> str:
    """One node on its own line, as `issue #5895 — change default redirect code to 303`."""
    ref = path.ref(node_id)
    head = trace.ref(ref.node_type, ref.external_id)
    title = _clip(ref.title, 62)
    text = f"{head} — {title}" if title else head
    return f"{text}  {dim(f'node:{node_id}')}" if verbose else text


def inline(path: Path, node_id: int) -> str:
    """One node inside a sentence, as `issue #5895 "change default redirect code to 303"`.

    Quoted rather than dash-separated, because the dash form reads as punctuation when it
    sits mid-sentence and the title runs into the verb that follows it.
    """
    ref = path.ref(node_id)
    if ref.node_type == "decision":
        # If this decision has the same title as the start node, it's the thread's own decision.
        # Otherwise, name the distinct decision reached via cross-references.
        start_ref = path.ref(path.node_ids[0])
        if start_ref.node_type == "decision" or (
            start_ref.title and ref.title and start_ref.title.strip() == ref.title.strip()
        ):
            return "the decision in this thread"
        title = _clip(ref.title, 52)
        return f'decision "{title}"' if title else "decision"
    head = trace.ref(ref.node_type, ref.external_id)
    title = _clip(ref.title, 52)
    return f'{head} "{title}"' if title else head


def plural(n: int, noun: str, suffix: str = "s") -> str:
    """`1 path` / `2 paths`. "1 path(s)" is what a tool prints when nobody read its output."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}{suffix}"


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text[: limit - 3] + "..." if len(text) > limit else text


def _clean_body_excerpt(body: str, max_lines: int = 5, max_chars: int = 420) -> list[str]:
    """Extract a clean, readable opening excerpt from a markdown issue/PR body."""
    import re
    import textwrap

    clean_lines = []
    for line in body.strip().splitlines():
        line_s = line.strip()
        if not line_s:
            if clean_lines and sum(len(l) for l in clean_lines) > 80:
                break
            continue
        if line_s.startswith(("#", "<!--", "![")):
            continue
        if set(line_s) <= {"-", "*", "="} and len(line_s) >= 3:
            continue
        clean_lines.append(line_s)
        if len(clean_lines) >= max_lines:
            break

    combined = " ".join(clean_lines).strip()
    if not combined:
        return []

    # Strip markdown link syntax: [label](url) -> label
    combined = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", combined)

    if len(combined) > max_chars:
        combined = combined[:max_chars].rsplit(" ", 1)[0] + "..."

    return textwrap.wrap(combined, width=72)



def summarize(answer: Answer, annotations: dict[int, str], statuses: dict[int, str]) -> list[str]:
    """The answer in sentences, built only from what the paths contain.

    Grouped by the node reached rather than by path, because two paths ending at the same
    artifact are one finding seen twice and reading them as two is how a trace overstates
    what it found.
    """
    if not answer.found:
        return []

    lines: list[str] = []
    seen: set[tuple[int, str]] = set()

    # Decisions directly motivated by the start node (1 hop)
    motivated_decisions: set[int] = {
        path.target_id
        for path in answer.paths
        if path.depth == 1 and path.steps and path.steps[0].edge_type == "motivated_by"
    }

    for path in sorted(answer.paths, key=lambda p: (p.depth, p.target_id)):
        target = path.target_id
        step = path.steps[-1] if path.steps else None
        if step is None:
            continue
        forward = step.to_node_id == target

        # If this decision was already motivated by the start node in Path 1, an indirect
        # multi-hop path reaching it via implemented_by should not state that the issue
        # implemented the decision.
        if path.depth > 1 and target in motivated_decisions and step.edge_type == "implemented_by":
            continue

        key = (target, step.edge_type)
        if key in seen:
            continue
        seen.add(key)

        if path.depth == 1:
            subject = inline(path, path.node_ids[0])
            verb = phrase(step.edge_type, forward)
            lines.append(f"{subject} {bold(verb)} {inline(path, target)}")
        else:
            # Multi-hop: the penultimate node executed the step reaching target
            start_node = inline(path, path.node_ids[0])
            actor_node = inline(path, path.node_ids[-2])
            verb = phrase(step.edge_type, forward)
            target_node = inline(path, target)
            lines.append(f"{start_node} links to {actor_node}, which {bold(verb)} {target_node}")

        note = annotations.get(target)
        status = statuses.get(target)
        if status or note:
            detail = ", ".join(x for x in (status, note) if x)
            lines.append(dim(f"    that decision is {detail}"))
    return lines



def render(
    answer: Answer,
    *,
    annotations: dict[int, str] | None = None,
    statuses: dict[int, str] | None = None,
    links: dict[str, str] | None = None,
    bodies: dict[int, str] | None = None,
    as_of=None,
    verbose: bool = False,
    out=None,
) -> None:
    out = out or sys.stdout
    annotations = annotations or {}
    statuses = statuses or {}
    links = links or {}
    bodies = bodies or {}
    p = lambda s="": print(s, file=out)  # noqa: E731

    if not answer.found:
        _render_refusal(answer, p, as_of)
        return

    if answer.used_inferred_fallback:
        # Before the answer, not after it. A reader who stops at the first confident
        # sentence must already have been told it is a guess.
        p(_c("33", bold("  ! INFERRED — no explicit path existed; this is not a record")))

    p(bold("  What the graph says"))
    p(dim("    (a summary built only from the links below — every sentence is traceable to an edge)"))
    for line in summarize(answer, annotations, statuses):
        p(f"    {line}")
    p()

    if bodies:
        context_nid = None
        if answer.start_node_id in bodies and bodies[answer.start_node_id]:
            context_nid = answer.start_node_id
        else:
            for path in answer.paths:
                for nid in path.node_ids:
                    if nid in bodies and bodies[nid] and path.ref(nid).node_type in ("issue", "pull_request"):
                        context_nid = nid
                        break
                if context_nid:
                    break

        if context_nid and bodies.get(context_nid):
            ref_obj = None
            for path in answer.paths:
                if context_nid in path.node_ids:
                    ref_obj = path.ref(context_nid)
                    break
            if ref_obj:
                excerpt_lines = _clean_body_excerpt(bodies[context_nid])
                if excerpt_lines:
                    artifact_label = trace.ref(ref_obj.node_type, ref_obj.external_id)
                    p(bold(f"  Motivation & Context  (from {artifact_label})"))
                    for eline in excerpt_lines:
                        p(f"    {dim(eline)}")
                    p()

    tiers = sorted({path.tier for path in answer.paths}, key=lambda t: len(t))

    for tier in tiers:
        strength = TIER_STRENGTH.get(tier, "")
        strength_label = f"  ({strength})" if strength else ""
        p(f"  evidence: {_tier_colour(tier, bold(tier))}{dim(strength_label)} — {dim(trace.TIER_MEANING[tier])}")
    p()

    p(bold(f"  Evidence trail  ({plural(len(answer.paths), 'path')})"))
    p(dim("    Each path is an independent chain of links. More paths = more kinds of evidence;"))
    p(dim("    a corroborated answer has at least 3 independent kinds all pointing the same way."))

    for i, path in enumerate(sorted(answer.paths, key=lambda x: (x.depth, x.target_id)), 1):
        _render_path(path, i, annotations, statuses, verbose, p)

    urls = _urls(answer)
    # Artifacts the answer NAMES but no path passes through -- chiefly the credited
    # implementer, which a Why-walk never reaches because it stops at the Decision. Named
    # and unopenable is the worse half of the problem #19 was about.
    already = dict(urls)
    urls += [(text, url) for text, url in links.items() if text not in already]
    if urls:
        p(bold("  Check it on GitHub"))
        for text, url in urls:
            p(f"    {text:<24} {dim(url)}")
        p()


def _render_path(path, index, annotations, statuses, verbose, p) -> None:
    mark = "" if _COLOR else trace.TIER_MARK.get(path.tier, "??") + " "
    p(f"  [{index}] {mark}{_tier_colour(path.tier, path.tier)}  {plural(path.depth, 'hop')}")
    p(f"      {label(path, path.node_ids[0], verbose=verbose)}")
    # Including the start node. A query that matched a Decision directly begins there, and
    # skipping it meant the one node whose label is deliberately uninformative -- see
    # `trace.ref` -- was also the one node with nothing to explain it.
    _annotate(path.node_ids[0], annotations, statuses, p)

    for step, node_id in zip(path.steps, path.node_ids[1:]):
        forward = step.to_node_id == node_id
        detail = f"  {dim('via ' + step.extractor)}" if verbose else ""
        p(f"       └─ {phrase(step.edge_type, forward)}  "
          f"{_tier_colour(step.evidence_tier, '[' + step.evidence_tier + ']')}{detail}")
        p(f"      {label(path, node_id, verbose=verbose)}")
        _annotate(node_id, annotations, statuses, p)
    p()


def _annotate(node_id: int, annotations: dict, statuses: dict, p) -> None:
    detail = ", ".join(x for x in (statuses.get(node_id), annotations.get(node_id)) if x)
    if detail:
        p(dim(f"          {detail}"))


def _urls(answer: Answer) -> list[tuple[str, str]]:
    """Every artifact in the answer that can be opened, deduplicated, in path order.

    This is the whole §9 adjudication loop: read the claim, open the artifact, decide
    whether the repository actually says that. It was previously a manual lookup of a
    number the trace did not print.
    """
    seen: dict[str, str] = {}
    for path in sorted(answer.paths, key=lambda p: (p.depth, p.target_id)):
        for node_id in path.node_ids:
            ref = path.ref(node_id)
            if ref.url:
                seen.setdefault(trace.ref(ref.node_type, ref.external_id), ref.url)
    return list(seen.items())


def _render_refusal(answer: Answer, p, as_of=None) -> None:
    """Say what was looked for and what that means -- refusals are a primary output.

    The distinction that matters is between "you asked the wrong question" and "the graph
    genuinely holds no evidence", and only the second is a finding. Neither the engine's
    one-line explanation nor a bare "no answer" separates them.

    A point-in-time query adds a third case, and it is the one most easily misread: the
    evidence exists but did not exist YET. Saying "nothing records why this happened" to
    someone who asked as of last June states something false about the graph, so the
    timestamp is named before anything else.
    """
    p(bold("  No answer — and that is a result, not a failure"))
    p()
    if as_of is not None:
        p(f"    Asked as of {as_of:%Y-%m-%d}. Nothing that answers this existed in the")
        p("    graph at that moment — edges are time-versioned, so this says when the")
        p("    evidence appeared, not that it is absent now. Drop --as-of to ask about today.")
        p()
        p(dim(f"    engine: {answer.explanation}"))
        p()
        return
    if answer.mode is Mode.WHY:
        p("    Nothing in the ingested window records why this happened. The rubric needs")
        p("    a motivating issue and merged work in the same conversation; a change made")
        p("    without an issue leaves nothing to reconstruct a decision from.")
    else:
        p("    Nothing in the ingested window records what this affects. Impact is walked")
        p("    forward over recorded links, so an artifact nothing references downstream")
        p("    has no impact to report rather than an unknown one.")
    p()
    p(dim(f"    engine: {answer.explanation}"))
    if answer.used_inferred_fallback:
        p(dim("    the inferred fallback was tried and also found nothing"))
    p()
    p(bold("  What to try"))
    p("    - widen the walk:      --depth 3")
    p("    - check it was ingested: dg status")
    p("    - the window is 12 months by default; older artifacts are deliberately absent")
    p()
