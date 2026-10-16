# 0002 — Evidence tiers, confidence, and where statistical signals live

**Status:** Proposed — awaiting owner decision

## Facts

- Every edge carries `tag` (`explicit` | `inferred`, provenance, never rewritten) and
  `evidence_tier` (`explicit` | `corroborated` | `inferred`, derived). A CHECK ties
  `tag='inferred' ⟺ evidence_tier='inferred'` (migration 0001/0006).
- `corroborated` is a mechanical rubric: evidence-carrying edge type, endpoints in one
  thread, ≥ 3 of 4 independent categories (DECLARED, STRUCTURAL, ATTESTED, PUBLISHED).
- A path's tier is its weakest edge.
- On flask, 8 of 239 threads are corroborated because flask rarely reviews formally
  (14 `reviewed` edges across 226 PRs, README "Known limitations").
- Tiers have never been **calibrated**: nobody has measured whether `corroborated` answers
  are in fact more often correct than `explicit` ones. 18/18 adjudicated correct gives no
  spread to measure against.
- The upcoming file-level work (ADR 0005) makes a new kind of signal available —
  co-change frequency ("these files changed together in 9 of 11 commits"). It is computed,
  not stated by anyone; it is neither explicit nor an inference about a specific link.

## Hypotheses

- H1. On a repository with routine review (e.g. 91% of sampled urllib3 merged PRs carry a
  review), `corroborated` will cover a large share of Decision threads — making the tier
  discriminating for the first time.
- H2. Users read any number next to an answer as a probability.

## Options — confidence

| | Option | Note |
|---|---|---|
| A | Keep categorical tiers only (status quo) | consistent with "mechanical rules over judgment" |
| B | Add a numeric confidence score | invites the reading H2 describes; nothing to calibrate it on |
| C | Keep tiers; **report measured precision per tier** in the evaluation, per repository | makes the tier an empirical claim without putting a number on every answer |

## Options — statistical signals (co-change)

| | Option | Problem |
|---|---|---|
| A | Store as `inferred` edges | conflates "a model guessed this link" with "these co-occur"; consumes the per-node inferred cap |
| B | New tier `statistical` on edges | widens the CHECK invariant and every renderer; persists a number that changes with every commit |
| C | **Compute at query time; never persist as an edge**; render in its own labelled section ("changed together N of M times") outside the evidence trail | not traversable — by design |

## Recommendation

**Confidence: C. Statistical signals: C.**

Tiers stay categorical and mechanical. What changes is that the evaluation must report,
per repository, how often each tier's answers were adjudicated correct — that is the only
honest form of "calibration" available, and it needs the second repository to have a
spread at all.

Co-change is useful for impact ("what else usually moves with this file"), but it is a
statistic about history, not evidence of a relationship. Keeping it out of the edge table
means it can never be walked as if it were a link, never inherit a tier, and never be
blended into a Why answer.

### Recorded dissent

An independent review disagreed with option C for co-change, and the disagreement is
substantive enough to put in front of the owner rather than resolve here:

> Keeping co-change out of the edge table creates a parallel trust label that bypasses the
> database-enforced inferred gate and the two-pass fallback. Co-change is exactly what that
> path was built for — a deterministic proposer with a threshold and a cap. Use it (option
> A), or justify why a cap of 4 does not fit.

The case for C despite that: the inferred path is a **fallback**, used only when no explicit
path exists (§5.3), so co-change stored there would never appear beside an explicit impact
answer — which is where it is most useful; and the per-node cap would keep an arbitrary four
of a file's co-change partners. The case for A: one inference channel, one gate, enforced
in the database, and ADR 0001's "deterministic proposer first" option could *be*
co-change. The owner should decide this together with ADR 0001.

## Evidence that would change this

- Per-tier precision on two repositories shows no difference between `explicit` and
  `corroborated` → the corroboration rubric is not discriminating and should be revisited.
- A use case that needs to traverse co-change (e.g. multi-hop "what is coupled to what is
  coupled to X") → reconsider B with its own invariant.

## Consequences

- No numeric confidence anywhere in output.
- Evaluation reports grow a per-tier precision table.
- Co-change appears in impact output and context packs as a separately labelled section.

## Decision

_Pending._
