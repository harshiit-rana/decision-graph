# Recall audit — why 148 threads produced no Decision

§9 measured precision: are the 13 Decisions correct. It could not ask whether the rubric is
*missing* decisions. This audit asks that, by classifying every non-Decision thread by the
reason it failed.

**Two findings, and the second is the one that matters.**

1. The rubric is not losing decisions. Every rejection traces to evidence the graph does
   not contain, and the residual bucket — passes every mechanical check yet produced no
   Decision — is empty.
2. **The graph is two-thirds of the intended window.** The `issues` cursor is still in
   `phase = backfill`. Ingestion stopped mid-window and was never resumed, so every
   coverage number reported in §9 is measured on a partial corpus.

## Bucket classification — 148 threads

> Measured on the **partial** corpus, before the backfill was resumed (145 pull requests,
> 54 issues, 161 threads). Re-measured on the completed window further down — the shape
> holds, the counts do not. Re-run it yourself with `eval/recall_audit.sql`.

| bucket | threads | verdict |
|---|---|---|
| A — no issue in the thread | 107 | correct: no Motivation exists to find |
| D — singleton issue, nothing linked | 34 | correct: no Implementation exists |
| B — in-thread `closes`, nothing merged | 7 | correct: the #17 landing gate |
| C — issue present, no in-thread `closes` | 0 | — |
| E — `closes` crossing a thread boundary | 0 | **vacuous, see below** |
| F — residual (should be empty) | 0 | no rubric bug |

### A — 107 threads with no issue

116 pull requests and 179 commits across these threads. **Only 2 of the 116 PR bodies
contain a closing keyword at all**, and only 9 contain any `#N` reference. These are
overwhelmingly changes that record no "why" anywhere GitHub can see — dependency bumps,
CI adjustments, typo fixes. §5.1 makes Motivation mandatory, so refusing them is the rule
working, not failing.

The 4 artifacts that *do* claim a closure point at targets absent from the graph:

| artifact | claims | target status |
|---|---|---|
| commit `3586884`, commit `8058f14` | `Fix #5199` | closed 2023 — outside the 12-month window |
| PR 6110 | `Fix #5200` | closed 2023 — outside the window |
| PR 6066 | `fix #6065` | **in window, closed 2026-08-11 — never ingested** |

The first three are documented limitation #5, working as designed and queued in
`pending_reference` rather than silently dropped. The fourth is the thread that led to
finding #2.

### D — 34 singleton threads

All 34 are issues, every one of them alone in its thread with nothing linked. No PR, no
commit, no implementation of any kind to point at. Correct refusal.

### B — 7 threads where nothing merged

Four issues were closed `not_planned` (5907, 5912, 5948, 5965) — declined work, exactly
what #16/#17 was built to reject.

The other three (5729, 5836, 5863) were closed **`completed`**, which looked like a genuine
recall loss: perhaps the fix landed as a direct commit and `thread_landed()` only checks
`pull_request.merged_at`. That hypothesis was wrong. Each of those threads' commits was
compared against `main`:

```
2c331f6  diverged    ec942ef  diverged    54aaa01  diverged    08f2779  diverged
```

Not one is an ancestor of `main`. The commits arrived via `link_pr_commits`, so they live on
the unmerged PR's branch — GitHub's `referenced` timeline event fires for any branch and does
not imply landing. **The landing gate was right to reject all three.** The work that closed
those issues landed somewhere the graph does not contain, which points back at finding #2.

### E is vacuous, not reassuring

Zero `closes` edges cross a thread boundary — but they never can. `_link_body_refs` unions
the two threads at the moment it creates the edge, so the condition is unsatisfiable by
construction. This bucket proves nothing about whether `thread_key` is a good stand-in for
§5.1's bounded time window.

The real risk it was meant to probe — two threads a human would call one decision, with no
explicit edge between them — is **not mechanically detectable at all** and remains
unmeasured. It cannot be: if an explicit edge connected the two clusters, they would already
be one cluster. It needs sampled human review, and this audit did not perform it.

**This is the largest open question about `thread_key` and it is not resolved by anything
here.** Tracked as issue #26 so it is not read as settled once this cycle closes.

## Finding 2 — the graph is incomplete

```
ingestion_cursor:  issues   phase=backfill   steady_watermark=2026-07-30
```

The backfill never reached its floor. Comparing the graph against GitHub for the same
window (`updated:>=2025-08-17`):

| | graph | GitHub | coverage |
|---|---|---|---|
| closed issues | 54 | 80 | **68%** |
| pull requests | 145 | 219 | **66%** |

106 items have been updated since the watermark and were never seen. Issue 6065 — closed
`completed` on 2026-08-11, referenced by PR 6066 — is one concrete example.

This is the ingestion design behaving correctly: the client stops cleanly at its rate-limit
floor and commits its cursor, so nothing is corrupted and nothing is lost. It simply was
never re-run. **Resume was built as the primary mechanism and has not yet been exercised
against a real interrupted backfill.**

### What it means for the §9 record

The precision result stands — the 13 Decisions were verified against real history, and
nothing about them changes. What is now known to be understated:

- "13 Decisions from 161 threads" is measured on ~two-thirds of the window.
- The three `completed`-but-unlanded threads most plausibly have their merged PR in the
  unfetched third.
- The corroborated tier's sparsity (6 of 161 threads) was attributed to flask's review
  culture. That remains the likeliest explanation, but it was measured on a partial corpus
  and should be re-checked, not assumed.

The §9 disclosures already state that recall was unmeasured. They did not state that the
corpus itself was partial, which is a stronger caveat and belongs alongside them.

## Conclusion

**Coverage is bounded by absent evidence, not by the rubric.** 141 of the 148 rejections
are threads with no motivating issue or no implementation to point at; the remaining 7 are
the landing gate firing on work that demonstrably never reached `main`. No rubric bug, no
residual, no misclassification found.

> Those three figures are the partial corpus. On the completed window the same sentence reads
> **207 of 220** and **13** — and the conclusion is unchanged. See *Re-measured on the
> completed corpus*.

The binding constraint on this system's usefulness is how much evidence GitHub carries in
the first place — and, right now, how much of it we have actually fetched.

## Resolution — the backfill was completed

Finding 2 is fixed. `dg-ingest` was resumed and ran to completion in 167 API requests; the
graph now matches GitHub exactly for the window.

| | before | after | GitHub |
|---|---|---|---|
| issues | 54 | **80** | 80 |
| pull requests | 145 | **219** | 219 |
| commits | 213 | **381** | — |
| nodes | 620 | **971** | — |
| threads | 161 | **235** | — |
| corroborated edges | 33 | **61** | — |
| **Decisions** | 13 | **13** | — |

**Resume worked as designed.** The cursor had committed exactly where it stopped, and the
second run picked up from the watermark without re-fetching or double-writing. A third run
was a no-op: 0 Decisions created, 13 refreshed, 6 API requests. This is the first time
resume-as-primary-mechanism has been exercised against a genuinely interrupted backfill
rather than a smoke test.

### The completed window produced no new Decisions

74 more pull requests, 26 more issues, 74 more threads — and the same 13 Decisions. The one
apparent change is a relabelling: `thread:30:pr-6095` became `thread:30:pr-6072` when the
new data merged the clusters. Same decision, same implementer (PR 6095), same verdict.

This *strengthens* the audit's first finding rather than complicating it. Coverage was never
being suppressed by the missing third; the added artifacts fall into exactly the same
buckets as before — no motivating issue, or nothing merged. 13 Decisions from 235 threads
is 5.5%, and the constraint is what flask records, not what the rubric accepts.

### Completing the window exposed a real bug

Ingesting the remaining third merged two clusters that already held a Decision, and
synthesis promoted a **second** Decision node for the same decision (issue #25). Synthesis
matched clusters on `external_id` — a snapshot of `thread_key` frozen at creation — while
`threads.union` rewrites `thread_key` and leaves `external_id` stale.

Both duplicates satisfied §5.1 individually, so neither the rubric guard nor
`edge_current_uidx` could see it; the violated invariant — *at most one Decision per
cluster* — was one nothing asserted. Migration 0009 repairs the duplicate, re-syncs the
stale key, and enforces the invariant with a partial unique index.

**No amount of re-running against the partial corpus would have found this.** It requires a
merge to occur *after* a Decision exists, which is precisely what more data caused.

## Re-measured on the completed corpus

Everything above the Resolution section was measured on 161 threads. The window has been
complete since that resume, but the classification was never re-run, so the Conclusion kept
quoting a corpus that no longer existed. Re-run now (#46), against repo 1 at its persisted
`window_floor` of 2025-08-24:

| bucket | partial | complete | verdict |
|---|---|---|---|
| A — no issue in the thread | 107 | **155** | correct: no Motivation exists to find |
| D — singleton issue, nothing linked | 34 | **52** | correct: no Implementation exists |
| B — in-thread `closes`, nothing merged | 7 | **13** | correct: the #17 landing gate |
| C — issue present, no in-thread `closes` | 0 | 0 | — |
| E — `closes` crossing a thread boundary | 0 | 0 | still vacuous, see above |
| F — residual (should be empty) | 0 | **0** | no rubric bug |
| non-Decision threads | 148 | **220** | |
| Decisions | 13 | **13** | |

**F is still empty on 45% more corpus.** That is the result worth having: the residual bucket
is where a rubric bug would surface, and growing the corpus by half did not produce one.
Every added thread fell into A, D or B — absent evidence, not rejected evidence.

### Two hypotheses from this document, now testable — both wrong

The audit wrote that the three `completed`-but-unlanded threads "most plausibly have their
merged PR in the unfetched third". There is no unfetched third any more, and 5729, 5836 and
5863 are still in bucket B. GitHub answers it directly: PRs 5735, 5846 and 5864 all report
`merged = false`. The speculation was wrong and the landing gate was right.

Issue **6065** was this document's one concrete example of a real recall loss — "in window,
closed 2026-08-11 — never ingested". It is ingested now, and it produced no Decision: it
lands in bucket B, with all four of its pull requests (6064, 6066, 6090, 6127) closed and
unmerged. It was never a recall loss.

Bucket B is now 13 threads, 5 `not_planned` and 8 `completed`. All 13 commits across the
`completed` ones were compared against `main`:

```
0d2d49e diverged   568c2e9 diverged   5f21252 diverged   8b0d81d diverged
a43b0e4 diverged   cc4bc94 diverged   d522702 diverged   9ff11f2 diverged
b582518 diverged   d4b8600 diverged   f95f83b diverged   6d5b099 diverged
f6b0eb6 diverged
```

Not one is an ancestor of `main`. The gate's refusal holds on the complete corpus.

### Corroborated sparsity — re-checked, as this document asked for

The audit flagged the corroborated tier's sparsity as "measured on a partial corpus and
should be re-checked, not assumed". Re-checked: **7 corroborating threads of 233**, against 6
of 161 before. A third more corpus bought one more. Proportionally it got *sparser* — 3.7% to
3.0% — so flask's review culture remains the explanation, and the partial corpus was not
hiding a denser tier.

### What this still does not establish

Bucket A is 155 threads, and every one is classified by a mechanical check for the *presence*
of an issue node. Whether a human would say those threads embody a decision is exactly what
#26 says cannot be measured this way, and re-running the buckets on a bigger corpus does not
touch it. A bigger A is not evidence of a better rubric; it is 48 more threads that record no
"why" anywhere the graph can see. The `thread_key` question stays open.

Nor does any of this measure precision on the added corpus. The 13 Decisions were verified by
hand in §9 and are unchanged — same 13 nodes, all still passing `decision_rubric` — but no
new Decision appeared to verify, so nothing here re-tests whether the rubric admits a wrong
one.

## Reproducing

```
psql -U postgres -d dg -v repo=1 -f eval/recall_audit.sql
```

`eval/recall_audit.sql` is the bucket classification, the E check, and the bucket A and B
detail queries — the ones this document was originally written from by hand. It was added
after the fact (#46), because the numbers above could not be *checked*, only re-derived, and
that is how the Conclusion went stale without anyone noticing.

Bucket order is load-bearing and the script says so: a singleton issue satisfies both C and
D, and only testing D first reproduces the original's `C = 0, D = 34`.

The landing checks are not in the script — they need the network. They used
`GET /repos/pallets/flask/compare/main...{sha}`, reading `.status`; anything other than
`identical` or `behind` means the commit is not an ancestor of `main`. Cursor state is in
`ingestion_cursor`, but note that `phase` is inert for `issues` and `pulls` (#47) — read the
watermark, not the phase.
