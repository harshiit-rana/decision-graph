# 0004 — Semantic retrieval

**Status:** Proposed — awaiting owner decision

## Facts

- Retrieval is exact title → identifier → prefix → trigram (`retrieval.py`), deliberately
  not ranked or embedding-based (PRD §5.2).
- The §9 set contains **no answered paraphrase query.** `eval/RESULTS.md:40` and PRD §9 call
  H5 a paraphrase, but H5's query is `#5898` and its recorded intent is "Bare identifier
  retrieval" (`eval/query_set.json`). The only fuzzy-wording query, H7, returns nothing
  because its Decision was correctly retracted.
- `dg ask` already turns a sentence into a search term with a model (`explain.parse_intent`),
  so plain-English input exists — but the term it extracts is then matched by trigram.
- Trigram fall-through "almost always returns *something*" and the round-trip work found
  four cases where it returned the wrong thing confidently (#96, #98, #102, #114).
- Postgres ships full-text search (`tsvector`, `ts_rank`) with no new dependency.
  `pgvector` is an extension; embeddings need a model and a cost per artifact.

## Hypotheses

- H1. Paraphrased questions ("why did redirects stop being 302?") fail to retrieve the
  right start node at a meaningful rate. **Unmeasured.**
- H2. Most of that failure is vocabulary mismatch that stemming + FTS over title *and body*
  would fix.
- H3. A wrong start node is worse than no start node, because traversal from it produces a
  confident, well-evidenced answer to a different question.

## Options

| | Option | Dependency | Risk |
|---|---|---|---|
| A | No change | none | H1 unknown |
| B | Add Postgres FTS (title + body) as a tier between prefix and trigram | none | ranking changes — must be snapshot-diffed (#98's 357-query order snapshot) |
| C | pgvector embeddings as an additional tier | extension + model + re-embedding on change | non-reproducible scores; artifact text leaves the machine unless the model is local |
| D | External vector database | new service | operational cost with no demonstrated need |

## Recommendation

**Measure, then B; C only if B leaves a measured gap; never D.**

Build the paraphrase benchmark first (issue: paraphrase retrieval benchmark): for a sample
of artifacts, write reformulated questions *without looking at what retrieval returns*,
record top-k hit rate and — because of H3 — the wrong-start rate. Only that benchmark can
justify any change here, and it is also the regression test for whichever option wins.

Retrieval output must also say which tier matched (it already labels `exact title match`,
`closest text match`); a semantic tier must label itself as such.

## Evidence that would change this

- Benchmark shows top-1 hit rate already high → stay at A.
- B closes most of the gap → stop at B.
- Remaining misses are conceptual, not lexical → evaluate C with a local embedding model
  first, so repository text does not leave the machine.

## Consequences

- No new dependency until a number says so.
- Any new tier is ordered below exact and identifier matches, so an exact match always
  wins regardless of score (existing invariant).

## Decision

_Pending._
