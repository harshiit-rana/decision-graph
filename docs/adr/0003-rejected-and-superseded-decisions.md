# 0003 — Representing rejected and superseded decisions

**Status:** Proposed — awaiting owner decision

## Facts

- §5.1 creates a Decision only where work **landed** (merged), enforced by
  `decision_rubric_guard` and `thread_landed()` (migrations 0002/0008).
- Declined work is common: on flask's first window, 28 of 54 closed issues were
  `not_planned`, and 7 of the first 20 qualifying clusters were declined (PRD §11 note).
  Of 263 artifacts a Why-walk refuses, 185 are PRs closed unmerged and 50 issues closed
  `not_planned` (README).
- Comments are ingested as artifacts (migrations 0014/0015). A refusal shows the last
  comment from an OWNER/MEMBER/COLLABORATOR and explicitly declines to call it the reason
  (#109). Sampled closing comments include withdrawals, duplicates and already-fixed bugs —
  not decisions.
- `supersedes` / `superseded_by` exist in the edge enum and `WHY_EDGES`
  (`reasoning.py:67-68`) and in the renderer, but **no extractor creates them**.
- Git records one supersession explicitly: `git revert` writes
  `This reverts commit <sha>.` into the message. Nothing reads it today.
- Who closed an issue **is in the payload but is not read.** The `issues` list response
  carries `closed_by` (checked against `pallets/flask` on 2026-10-16: populated on every one
  of five sampled closed issues), and `node.raw` stores the payload, but no extractor reads
  the field (`grep closed_by src/` is empty). `closed_by` carries no standing: an actor's
  `author_association` exists only per artifact they authored, so the closer's standing must
  come from their own comments or reviews in the repository, or from the collaborator
  permission endpoint (which needs push access to the repository — not available for a
  public repository we do not maintain).

## Hypotheses

- H1. "Have we tried this before, and what happened?" is one of the highest-value questions
  for both maintainers and coding agents, and closed-unmerged attempts answer it even
  without any Decision node.
- H2. A mechanical rule can separate a *project decision not to do X* from withdrawal,
  duplication and abandonment using only state, standing and links.

## Options — rejected outcomes

| | Option | Note |
|---|---|---|
| A | No node; a query-time view over closed-unmerged / `not_planned` artifacts ("prior attempts") | asserts nothing new; cannot be walked as a decision |
| B | `decision.outcome` (`landed` \| `declined`) with a **separate** rubric function and guard | reuses the Decision machinery; but every existing consumer assumes a Decision landed |
| C | A separate `declined_decision` node type with its own rubric and guard | duplicates some identity and rendering code; keeps "Decision ⇒ landed" structural |

A declined rubric (draft, for the owner to tighten) would need **all** of:
1. an issue closed `not_planned`;
2. closed by an actor with standing (OWNER/MEMBER/COLLABORATOR) — requires extracting
   `closed_by` and a mechanical rule for the closer's standing (see Facts);
3. at least one PR in the same thread, closed unmerged;
4. not marked duplicate.

It would still **not** assert the reason. The rationale stays "what was said", quoted,
exactly as #109 does.

## Options — supersession

| | Option | Note |
|---|---|---|
| A | Extract explicit reverts → `supersedes` edge from the reverting commit to the reverted commit, `tag='explicit'` | Git-native, mechanical, no judgment |
| B | Also infer supersession from later Decisions touching the same files | that is inference; belongs under ADR 0001 |

## Recommendation

1. **Ship A (prior attempts) first** — it needs no schema change and delivers H1's value
   immediately, with no new assertion.
2. **Supersession: A only.** Reverts are explicit and cheap; anything else is inference.
3. **Rejected outcome: C (a separate node type), only after** the closing actor is extracted
   and the draft rubric is checked by hand against a sample of declined threads on two
   repositories. If the sample shows the rule cannot separate project decisions from
   withdrawals, stop at option A and say so.

**Why C rather than B (revised after review).** The first draft recommended B. An
independent review pointed out that every existing consumer reads a `decision` row as
"work landed": `EVIDENCE_SQL`, the Why-walk's answer test (`reasoning.py:296-299`), the
report's concentration figures, `thread_landed()`, and `retract_unsupported_decisions()`.
An `outcome` column makes each of those silently wrong until someone remembers to filter
it. A separate type keeps the invariant structural — a `declined_decision` row cannot be
counted as landed by a query that never names it — at the cost of some duplicated identity
and rendering code. That cost is the better trade for a system whose cardinal failure is a
confident false statement.

## Evidence that would change this

- The hand check finds the draft rubric admits withdrawals or duplicates → do not ship B.
- Reverts are rare on both repositories (count them first; it is one query once commit
  messages are in the graph — they already are).

## Consequences

- A declined decision would be the first assertion the system makes about something that
  did **not** happen; it must be impossible to read as "landed" in any renderer (tests per
  renderer), which option C makes the default rather than a per-query obligation.
- Traversal edge sets need an explicit choice about whether Why-walks cross into declined
  decisions.

## Decision

_Pending._
