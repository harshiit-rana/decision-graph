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
unmeasured. It needs sampled human review, and this audit did not perform it.

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

The binding constraint on this system's usefulness is how much evidence GitHub carries in
the first place — and, right now, how much of it we have actually fetched.

## Reproducing

Bucket classification is a single query over `node`, `edge` and `thread_landed()`; the
landing checks used `GET /repos/pallets/flask/compare/main...{sha}` and read `.status`.
Cursor state is in `ingestion_cursor`.
