# 0001 — Inferred relationships: whether and how to build the proposer

**Status:** Proposed — awaiting owner decision

## Facts

- `inference.propose()` returns `[]` unconditionally (`src/decision_graph/inference.py:55`).
  No model is called. `run.py:115` calls it on every ingestion.
- Everything *around* the proposer is built and enforced in the database, not in calling
  code: relevance ≥ 0.75, at most 4 inferred edges per node, and no inferred edge may touch a
  Decision (`edge_inferred_gate`, migration 0001).
- The traversal fallback (explicit pass, then inferred pass only if the first is
  disconnected) is implemented and tested against a seeded fixture
  (`tests/test_traversal_fallback.py`), because the real graph holds no inferred edges.
- The §9 run entered the fallback 6 times and found nothing each time. That is a
  consequence of the stub, not a property of flask. README and PRD §9 present it without
  saying so; only `eval/RESULTS.md:126` does.
- The recall audit (`eval/RECALL_AUDIT.md`) classified every non-Decision thread and found
  each rejection traceable to evidence the graph does not contain (no issue, nothing merged,
  singleton). It did **not** test whether a human would find a link the extractors missed.
- The 0.75 threshold and the cap of 4 were chosen, not measured.

## Hypotheses (unmeasured)

- H1. Some real "why" questions are unanswerable only because the motivating link is
  written in prose ("as discussed in the redirect thread") rather than as `#N`.
- H2. A proposer would mostly bridge gaps that a human does not consider real links,
  lowering trust more than it raises coverage.
- H3. A deterministic proposer (shared rare terms, same files touched, same author within
  days) recovers most of what an LLM would, at no cost and reproducibly.

None of these has any evidence for or against it in this repository.

## Options

| | Option | Cost | Risk |
|---|---|---|---|
| A | Keep the stub; disclose it plainly; remove "inferred" from user-facing claims until built | none | the third tier is permanently theoretical |
| B | Build an LLM proposer now, behind the existing gate | model cost per ingestion; prompt-injection surface from repository text; non-reproducible runs | builds the PRD's named failure mode ("inferred edges treated as fact") on unmeasured need |
| C | Build a **deterministic** proposer first (lexical + structural signals), evaluate it, add an LLM scorer only where it beats C | moderate; reproducible; no external service | may find little — which is itself the answer |
| D | **Gate the decision on evidence**: run the human-labelled recall audit first; build C only if it finds explicit-extraction misses; consider B only if C's measured precision is too low | delays the feature | none beyond delay |

## Recommendation

**D, then C if the evidence supports it.** The project's premise is that an assertion
needs evidence; the same applies to building a feature whose value nobody has measured.
The recall audit (issue: random-sample recall with human labels) produces exactly the
data needed: every sampled thread a human links but the graph does not is a training and
evaluation case for a proposer, and if there are none, the proposer is not needed on these
repositories.

If built, the proposer must:

1. Consider only pairs with **no** explicit path between them (`inference.py`'s own
   docstring contract).
2. Treat repository text as untrusted data if a model ever sees it (see the prompt-boundary
   issue).
3. Ship with a labelled precision figure and a threshold chosen from it — not 0.75 by
   default.

## Evidence that would change this

- The recall audit finds ≥ N human-confirmed links that are missing only because they are
  written in prose (owner to set N; a handful across two repositories would justify C).
- A user request where the explicit-only refusal was the wrong outcome.

## Consequences

- Short term, documentation must stop implying the fallback was exercised on real data.
- The inferred tier stays valid as a contract (tested on fixtures) but unclaimed as a
  capability.
- An LLM is not added to ingestion, which keeps ingestion offline-reproducible.

## Decision

_Pending._
