# §9 Evaluation — results

Target: `pallets/flask`, 12-month window. Adjudicated by hand against flask's real GitHub
history. Closes Phases 1–4.

## The figure

**18 of 18 correct. No failures.**

That number is worth less than it looks, and the rest of this document is the reason why.
It is reported with its disclosures attached because a bare 100% on an 18-query set
curated by the person who built the system is not evidence of accuracy — it is evidence
that the system behaves as its author expected on cases its author chose.

### What the denominator actually contains

| outcome | queries | note |
|---|---|---|
| answered, verified correct | 10 | the discriminating subset |
| correctly returned nothing | 8 | 5 designed refusals + 3 on their merits |

**A system that always returned nothing would score 8 of 18 here.** Refusal is a real
property and four of those cases exist specifically to test it — but it is not the same
achievement as a correct answer, and collapsing both into one ratio hides that.

### How each verdict was reached

| basis | queries | |
|---|---|---|
| checked directly against GitHub history | 11 | F1, F2, H1, H2, H3, H4, H7, H8, H9, H10, N1 |
| correct by construction | 7 | retrieval consistency, the known #14 gap, mechanical contracts |

Of the 10 *answered* queries, 8 were checked directly. The remaining two are controls
rather than independent evidence: H5 reaches the same Decision as F1 through a paraphrase,
and T2 re-runs F2 through the time filter. They confirm consistency, not correctness.

## Per-query outcome

| id | category | mode | result | paths | tiers returned |
|---|---|---|---|---|---|
| F1 | flagship | why | answered | 1 | explicit |
| F2 | flagship | impact | answered | 17 | 11 corroborated, 6 explicit |
| H1 | negative | why | nothing | 0 | — (regression guard for #16/#17) |
| H2 | causal | why | answered | 1 | explicit |
| H3 | causal | why | answered | 1 | corroborated |
| H4 | causal | why | answered | 1 | explicit |
| H5 | retrieval | why | answered | 1 | explicit |
| H6 | retrieval | why | nothing | 0 | — |
| H7 | retrieval | why | nothing | 0 | — (thread retracted by #17) |
| H8 | impact | impact | answered | 13 | 7 corroborated, 6 explicit |
| H9 | impact | impact | answered | 10 | explicit |
| H10 | impact | impact | answered | 5 | explicit |
| N1 | negative | why | nothing | 0 | contract HELD |
| N2 | negative | impact | nothing | 0 | contract HELD |
| N3 | negative | why | nothing | 0 | contract HELD — no candidate retrieved |
| N4 | negative | why | nothing | 0 | contract HELD — no candidate retrieved |
| T1 | temporal | why | nothing | 0 | point-in-time control |
| T2 | temporal | impact | answered | 17 | 11 corroborated, 6 explicit |

**5 of 5 engine contracts held.** Contracts are mechanical properties of the code — "must
return no answer for an artifact outside the window" — not judgments about flask, which is
why the runner is allowed to assert them.

The point-in-time control is the load-bearing one: F1 returns 1 path and T1 returns 0 when
asked as of 2025-06, while F2 and T2 both return 17 as of 2026-12. The filter restricts in
one direction only. Had it been silently ignored, both pairs would have matched.

## The finding that matters most

**Neither defect found during this evaluation cycle was caught by the evaluation set.**

Both were found by a human opening a trace and comparing it to GitHub:

- **#16/#17 — merge-state blindness.** Issue 5912 was *closed as not planned* with no
  linked PRs, but five artifacts said `fixes #5912` and the rubric accepted the keyword as
  proof the work landed. 12 of 20 Decisions rested on pull requests that were never merged.
  At that moment the evaluation reported 12 answered, 4 refusals, contracts held — it
  looked healthy.
- **#19 — mislabelled implementer.** Traces displayed the `thread_key`, which names the
  cluster after the lowest-numbered PR. Where a change took two attempts it named the
  abandoned one. The graph was correct; the report was not.

This is precisely what §9 exists for, and it worked. But it establishes the limit
directly: **the automatic portion of the runner has no power over correctness.** It checks
contracts and reports what the engines returned. Every claim about whether an answer is
*true* came from a person reading GitHub. The right conclusion is not that the eval set
should have caught these — it is that the figure above is a product of manual review, and
degrades to nothing without it.

## Graph the figure was measured on

| | |
|---|---|
| nodes | 620 |
| current edges | 797 (+5 superseded, retained as history) |
| thread clusters | 161 |
| **Decisions** | **13** — 3 explicit, 10 reconstructed |
| rubric failures | 0 |
| pull requests | 145 (27 merged in-window) |
| issues / commits / releases | 54 / 213 / 38 |
| corroborated edges | 33, across 6 threads |
| **inferred edges** | **0** |

**13 Decisions from 161 threads is 8% coverage.** The system is precise and narrow by
construction: the rubric refuses anything it cannot ground, and no significance filter was
added because filtering for "important" decisions is exactly the model judgment §5.1
exists to exclude. Coverage, not precision, is the binding constraint — and this
evaluation measures precision only.

## What the figure does not license

- **Recall.** Nothing here measures decisions the system failed to reconstruct. 148 threads
  produced no Decision and none were audited to establish whether they should have.
- **Inference quality.** The graph holds zero inferred edges. §5.3's fallback was entered
  6 times and returned nothing every time, so its *entry condition* is exercised by flask
  while its answer-producing behaviour is verified only by the seeded fixture in
  `tests/test_traversal_fallback.py`. The LLM path remains stubbed and gated.
- **Corroborated-tier calibration.** 33 edges across 6 of 161 threads, and only 3 queries
  returned a corroborated path. flask merges largely without formal GitHub review — 11
  `reviewed` edges across 145 PRs. The tier is under-exercised on this repo, which is a
  property of the target, not evidence the rubric is right.
- **Generalization.** One repository, one language, one 12-month window, one maintainer
  culture. flask's conventions — closing keywords in PR bodies, changelog in `CHANGES.rst`
  rather than release notes, no CODEOWNERS, no wiki — shaped what could be extracted at all.

## Known limitations carried forward

Accepted deliberately rather than fixed by switching to a more convenient repository:

1. **No CODEOWNERS** anywhere in flask. The `owns` extractor is built and no-ops; the §9
   set contains no ownership queries because it cannot.
2. **`has_wiki: false`.** `motivated_by` resolves only to issues and PR bodies here.
3. **Corroborated tier sparse** — 6 of 161 threads, for the reasons above.
4. **Explicit-status Decisions limited to release-note citations** (3 of 13). flask's
   changelog lives in a repo file and file-content ingestion is not built.
5. **Cross-references outside the 12-month window are skipped**, not fetched — fetching
   them would make the window unbounded by the back door. Counted in run summaries under
   `*_target_not_ingested` rather than hidden.

## Open issues at close of Phase 4

Deliberately not addressed, each tracked:

- **#14** — Why-from-a-commit. `implements` is absent from `WHY_EDGES`, so a commit cannot
  walk back to its Decision. H6 is the query that exposes it; its "nothing" is a genuine
  gap, counted as correct-by-construction rather than as a passing case.
- **#12** — split `body_closing_keyword` into PR-body vs commit-message provenance.
  Justified on precision grounds only.
- **#4** — the workflows endpoint returns a paginated object rather than a bare list.

## Verification

- 38 Python tests, 30 SQL behavioural checks — all green.
- Reproduce: `DATABASE_URL=... python -m decision_graph.evaluation` then
  `python eval/render_report.py`.
