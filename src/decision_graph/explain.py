"""NLP Explanation Layer — plain-English answers over Engine 2's output (§7).

This module writes prose. It does not decide anything. Everything a `dg ask` answer
asserts comes from the graph: which paths exist, what tier each one carries, and whether
there is an answer at all. The model's only job is to say that fluently.

That division is not stylistic. The rest of this system is built to refuse rather than
guess — the §5.1 rubric, the two-pass fallback, the inferred-edge gate — and an LLM
placed at the end of it can undo all of that in one sentence by rendering a bridged,
inferred, single-hop guess in the same confident register as a corroborated chain of four
explicit edges. Three rules keep it from doing so, and they are the point of the module
(issue #65):

1. **A refusal is never generated.** When the graph found nothing, `summarize_answer`
   returns a fixed sentence and never calls the model. 8 of the §9 evaluation set's 18
   correct outcomes are refusals; they are the system's most load-bearing output, and a
   sentence produced by a language model asked to "politely state that the graph does not
   contain the answer" is not the same claim as the engine reporting that it found none.
2. **The tier travels with the path** into the prompt, and the prompt is told to name it.
3. **The trace is printed next to the prose**, by the caller, from the same `Answer`. §7
   requires every answer to carry its traversal path; prose that cannot be checked against
   the graph it came from is the one output this repository should not ship.

The client is a parameter with a lazy default rather than a module global, so the parsing
and serialisation here are testable without a network call or an API key.
"""

from __future__ import annotations

import json
import os
import re
import sys

from . import trace
from .reasoning import Answer, Mode

# The default model, overridable with DG_NLP_MODEL in .env.
#
# It is configurable because the previous value stopped existing: NIM retired
# meta/llama-3.1-70b-instruct on 2026-08-26 and every `dg ask` began failing with a 410
# and a traceback. A hosted model id is not a constant -- it has an end-of-life date the
# code cannot see -- so pinning one in source guarantees the same failure again, and the
# fix should not require editing Python and rebuilding an image.
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def model() -> str:
    return os.environ.get("DG_NLP_MODEL") or DEFAULT_MODEL


# Kept as a module attribute because the CLI prints it as the byline on a generated
# paragraph, and a reader deciding how much to trust that paragraph should be told which
# model wrote it -- especially now that it can be changed without touching the code.
MODEL = model()

# Returned verbatim when the graph found nothing. Deliberately a constant: see rule 1.
NO_ANSWER = (
    "The graph holds no path that answers this. That is a result, not a failure — it means "
    "no artifact in the ingested window carries evidence linking these things, and the "
    "engine declines to assert a connection it cannot show."
)


class ExplainError(RuntimeError):
    """Configuration or model-output problem the caller should report, not crash on."""


def get_client():
    """The Nvidia NIM client, or an ExplainError explaining which setup step is missing.

    The install hint differs by where this is running. Inside the container the package is
    baked into the image, so a missing import means a stale image and the fix is
    `dg rebuild`; outside it, `openai` is an optional extra and the fix is to install it.
    Telling a host user to rebuild a Docker image they are not using is the advice this
    used to give (issue #65).
    """
    in_container = bool(os.environ.get("DG_PROJECT_DIR"))
    try:
        from openai import OpenAI
    except ImportError as exc:
        fix = (
            "run `dg rebuild` — the image predates this feature"
            if in_container
            else "install the optional extra: pip install 'decision-graph[nlp]'"
        )
        raise ExplainError(f"the 'openai' package is not installed. {fix}") from exc

    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise ExplainError(
            "NVIDIA_API_KEY is not set. `dg ask` sends your question to Nvidia NIM; "
            "add the key to .env, or use `dg query`, which needs no external service."
        )

    return OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)


def _diagnose(exc: Exception) -> str:
    """Turn a provider error into something the reader can act on.

    Every one of these was previously a 40-line traceback out of the CLI. The status codes
    are read by attribute rather than by catching openai's exception classes, because
    `openai` is an optional extra here and this module must import without it.
    """
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    current = model()
    if status == 410:
        return (
            f"Nvidia has retired the model '{current}'. Pick a current one from "
            "https://build.nvidia.com and set DG_NLP_MODEL=<id> in .env -- no rebuild "
            "needed. Every other command works without this."
        )
    if status in (401, 403):
        return (
            "Nvidia rejected the API key. Check NVIDIA_API_KEY in .env, or get a new one "
            "at https://build.nvidia.com"
        )
    if status == 404:
        return (
            f"Nvidia has no model '{current}' available to this key. Set DG_NLP_MODEL in "
            ".env to one listed at https://build.nvidia.com"
        )
    if status == 429:
        return "Nvidia rate-limited this key. Wait a moment and try again."
    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return f"the request to Nvidia failed: {first_line}"


def _complete(client, **kwargs):
    """One chat completion, with provider failures reported rather than raised raw."""
    try:
        return client.chat.completions.create(**kwargs)
    except ExplainError:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see _diagnose
        raise ExplainError(_diagnose(exc)) from exc


# A model asked for "JSON only" routinely returns JSON with prose around it -- the model
# this now defaults to answers `We need to output JSON only: {"query": ...}` even with
# response_format set, and a reasoning model puts a whole plan first. Requiring the entire
# reply to parse made the feature depend on a courtesy no provider actually guarantees.
#
# Greedy from the first brace to the last: the schema here is flat and single-object, so
# the widest span is the object. A reply with no braces at all is still an error, loudly.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_intent(question: str, client=None) -> tuple[str, str]:
    """Turn a question into (search term, mode).

    Both failure modes here were unhandled tracebacks (issue #65): a reply that is not
    JSON, and a `mode` outside the two `reasoning.Mode` accepts. Neither is exotic — the
    first is what every model does occasionally, and the second is what one does when a
    question is neither causal nor forward-looking.

    A mode the engine does not have becomes `why`, and the caller is expected to say so
    rather than silently substituting. An empty search term is an error: retrieval would
    match everything, and the first candidate of everything is not an answer.
    """
    client = client or get_client()

    prompt = f"""You are a query parser for a GitHub repository decision graph.
The user will ask a natural language question about the codebase.
Your job is to extract:
1. The exact search entity: A title, pull request number (e.g. #123), or commit sha.
2. The mode: 'why' (if asking for motivation/reason) or 'impact' (if asking what it affects/breaks downstream).

Output valid JSON ONLY in this format:
{{"query": "extract search term", "mode": "why" or "impact"}}

User question: {question}
"""
    response = _complete(
        client,
        model=model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""

    match = _JSON_OBJECT_RE.search(content)
    if not match:
        raise ExplainError(
            f"the model did not return JSON: {content.strip()[:120]!r}"
        )
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ExplainError(
            f"the model did not return JSON: {content.strip()[:120]!r}"
        ) from exc
    if not isinstance(result, dict):
        raise ExplainError(f"the model returned {type(result).__name__}, not an object")

    query = str(result.get("query") or "").strip()
    if not query:
        raise ExplainError(
            "the model extracted no search term from that question. Try naming the change "
            "directly — a title, #number, or commit sha."
        )

    mode = str(result.get("mode") or "").strip().lower()
    valid = {m.value for m in Mode}
    return query, mode if mode in valid else Mode.WHY.value


def render_paths(answer: Answer) -> str:
    """The traversal, as text for the prompt. Tier-carrying by construction.

    Every line that names an edge names its tier, and the header names the path's own —
    the weakest link along it. A prompt that showed the shape of a path without its
    evidence grade would be asking the model to describe a claim while withholding how
    well founded it is.
    """
    def name(path, node_id: int) -> str:
        ref = path.ref(node_id)
        # The GitHub identity, not the database key. A summary is only checkable if the
        # artifacts it cites are the ones a reader can open, and a model handed `issue:620`
        # can only write `issue:620` back (issue #69).
        return f"{trace.ref(ref.node_type, ref.external_id)} \"{ref.title or ''}\""

    blocks: list[str] = []
    for i, path in enumerate(answer.paths, 1):
        lines = [f"Path {i} (evidence tier={path.tier}, depth={path.depth}):"]
        lines.append(f"  Start: {name(path, path.node_ids[0])}")
        for step, node_id in zip(path.steps, path.node_ids[1:]):
            direction = "->" if step.to_node_id == node_id else "<-"
            lines.append(
                f"    {direction} {step.edge_type} [{step.evidence_tier}] ({step.extractor})"
            )
            lines.append(f"    Node: {name(path, node_id)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_context(answer: Answer, bodies: dict[int, str] | None = None) -> str:
    """The source artifact descriptions / problem statements for the prompt."""
    if not bodies:
        return ""
    blocks: list[str] = []
    seen: set[int] = set()
    for path in answer.paths:
        for nid in path.node_ids:
            if nid in seen:
                continue
            seen.add(nid)
            body = bodies.get(nid)
            if body and body.strip():
                ref = path.ref(nid)
                title = f' "{ref.title}"' if ref.title else ""
                label = f"{trace.ref(ref.node_type, ref.external_id)}{title}"
                text = body.strip()
                if len(text) > 1500:
                    text = text[:1500].rsplit(" ", 1)[0] + "..."
                blocks.append(f"Artifact {label}:\n\"\"\"\n{text}\n\"\"\"")
    return "\n\n".join(blocks)


def summarize_answer(
    question: str,
    answer: Answer,
    client=None,
    bodies: dict[int, str] | None = None,
    annotations: dict[int, str] | None = None,
) -> str:
    """Explain what the engine found, in prose. Refusals are not sent to the model.

    `answer.found` is the engine's own verdict, and when it is false there is nothing to
    summarise — the correct output is a statement that the graph holds no path, which the
    code can make exactly and a model can only approximate. Returning NO_ANSWER here also
    means an unconfigured or unreachable NIM endpoint cannot turn a correct refusal into a
    crash.
    """
    if not answer.found:
        return NO_ANSWER

    client = client or get_client()
    tiers = sorted({p.tier for p in answer.paths})
    caveat = (
        "\nIMPORTANT: no explicit path existed, so these paths include INFERRED edges. "
        "Say so plainly in your first sentence; an inferred link is a suggestion the "
        "system could not evidence, not something the repository records.\n"
        if answer.used_inferred_fallback
        else ""
    )

    context_text = render_context(answer, bodies)
    context_section = (
        f"\nHere is the context and problem description written by the author in the repository artifacts:\n{context_text}\n"
        if context_text
        else ""
    )

    facts_section = ""
    if annotations:
        facts_lines = []
        for nid, note in annotations.items():
            for path in answer.paths:
                if nid in path.node_ids:
                    ref = path.ref(nid)
                    title = f' "{ref.title}"' if ref.title else ""
                    facts_lines.append(f"- Decision{title}: {note}")
                    break
        if facts_lines:
            facts_section = f"\nRecorded implementation facts from the repository:\n" + "\n".join(facts_lines) + "\n"

    prompt = f"""You are an Organizational Intelligence Engine explaining a decision graph to an engineer.
The user asked: "{question}"

Here are the raw graph traversal paths found in the database:
{render_paths(answer)}
{facts_section}{context_section}
The evidence tiers present are: {', '.join(tiers)}.
{caveat}
Task:
Explain the result of this query clearly, thoroughly, and informatively in plain English.
- Synthesize both the traversal graph and the artifact context (issue/PR descriptions) above.
- Clearly explain the underlying MOTIVATION or PROBLEM that prompted this change (what was wrong or what was requested).
- Explain WHAT was decided and HOW it was implemented (identifying key pull requests or commits).
- If multiple paths lead to the same decision, group them logically.
- Base your explanation strictly on the evidence and artifact text provided above.
- Name the evidence tier of what you assert. "explicit" and "corroborated" are recorded
  facts; "inferred" is the system's own guess and must be labelled as one.
"""

    response = _complete(
        client,
        model=model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()



def _fail(exc: ExplainError) -> int:
    print(f"error: {exc}", file=sys.stderr)
    return 2
