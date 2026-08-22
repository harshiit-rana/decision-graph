# The `thread_key` census — how often the cluster boundary costs a Decision

Issue #26 asked a question the recall audit could not: are there two clusters a human would
call one decision, with no explicit edge between them? Bucket E was supposed to probe it and
was vacuous by construction. This is the sampled human review the issue asked for instead —
run as a **census**, because the population that matters turned out to be small enough to
read in full.

**Result: 2 of 14.** Two threads in the census have their motivating issue sitting in another
cluster, with no explicit link anywhere — not in the bodies, not in GitHub's own timeline. In
this corpus the `thread_key` stand-in costs **2 Decisions out of 17 achievable**, about 12%.

## The frame, and why it is 14 and not 153

The obvious frame is "threads that failed for want of Motivation" — bucket A, 153 threads.
Most of that is wasted reading. A thread with **no merged pull request** fails clause 2
(landing) whatever motivation is found for it; finding its issue would change nothing. The
threads where the cluster boundary is actually the binding constraint are the ones that
already have a merged implementation and lack only the why:

```
no issue in the thread  AND  thread_landed() = true
```

That is **14 threads**. Fourteen can be read by hand in full, so this has no sampling error
to disclose — every member of the population was examined.

Candidates for each thread come from `eval/thread_key_census.sql`: issues ranked by pg_trgm
title similarity, restricted to those **opened before the PR merged**, since an issue filed
afterwards cannot be what motivated the work. That restriction is not a nicety — 6 of the
first 12 candidates failed it, including the highest-scoring pair in the entire set (PR 5928
/ issue 6044 at `sim = 0.301`, filed 105 days *after* the merge). Ranking on text alone puts
an impossible pair at the top of the list.

## Adjudication — 6 candidates over 4 threads

| PR | candidate issue | sim | verdict |
|---|---|---|---|
| 6133 `add app.query route decorator` | 6065 `Add a query() route shortcut and MethodView support for HTTP QUERY (RFC 10008)` | 0.224 | **genuine** |
| 5928 `all teardown callbacks are called despite errors` | 5870 `Teardown handler chain exception handling` | 0.159 | **genuine** |
| 6133 | 5915 `Support for all methods in @app.route` | 0.204 | rejected — `methods=None` handling, a different ask in the same area |
| 5808 `fix annotation for select_jinja_autoescape` | 5776 `Looser type annotations for send_file() path_or_file` | 0.240 | rejected — both say "type annotation", different functions |
| 5865 `Increase required flit_core version to 3.11` | 5804 `3.1.2 regression: stream_with_context` | 0.159 | rejected — build config vs a runtime regression |
| 5865 | 5839 `Really, I can't close connection???` | 0.156 | rejected — unrelated |

Four of six are false pairs. Title similarity is a reasonable way to *surface* candidates and
a bad way to judge them: `0.240` is a rejection here and `0.159` is a confirmation.

### 6133 / 6065 — the clearest case

Issue 6065 asks for "a `query()` route shortcut and MethodView support for HTTP QUERY (RFC
10008)". PR 6133 adds exactly that, opening with "RFC 10008 isn't accepted yet, but it's been
kicking around for years... I'm adding it now". Same RFC, same feature, issue opened 49 days
before the merge and closed `completed`.

They are in different clusters. Issue 6065 sits in `thread:1:pr-6094` with PRs 6064, 6066,
6090 and 6127 — **all closed unmerged** — so its own thread lands in bucket B, "nothing
merged", while the PR that actually shipped the feature sits alone in bucket A with no issue.
The graph holds both halves of one decision and cannot join them.

### 5928 / 5870 — genuine, with a wrinkle

Issue 5870 reports that `do_teardown_request` does not wrap individual handlers, so one
raising handler skips all the rest. PR 5928 makes "all teardown callbacks... called despite
errors", collecting them into an `ExceptionGroup`. That is the fix for that report.

The wrinkle: 5870 is closed **`not_planned`**. Migration 0008 reasoned that `not_planned`
issues are excluded from Decisions as a side effect, because "all four such clusters contain
no merged PR at all". That reasoning does not hold here — the merged PR exists, it is just in
another cluster. Were the link made, this would promote a Decision motivated by an issue the
maintainers formally declined and then effectively fixed. Whether that *should* be a Decision
is a rubric question this census does not settle.

## Neither is detectable mechanically — checked, not assumed

The obvious objection is that the graph simply missed a link that was written down somewhere.
It was not. GitHub's own timeline for both issues records no connection to the merged PR:

- **6065** — cross-references 6064 and 6066 (its own thread's unmerged PRs) and three PRs in
  unrelated repositories. Nothing pointing to 6133. Closed with no closing commit or PR.
- **5870** — no cross-references at all. Closed with nothing attached.

Neither PR body names its issue. So this is not the #50 failure mode (a link written in a
form the parser could not read); it is the failure mode #26 predicted — a connection that
exists only in a human's head.

## The 10 threads with no candidate

```
5723  5742  5795  5800  5829  5844  5903  5924  5945  6013
```

Reading them: two are release PRs (`release version 3.1.2`, `3.1.3`), four are docs or
tooling (`Docs typo/markup fixes`, `pre-commit: Add codespell`, `add zizmor to scan
workflows`, `Update GitHub Actions workflow for artifact handling`), and the rest are small
self-describing fixes. These are the "changes that record no why anywhere GitHub can see"
that the recall audit's bucket A is mostly made of, and refusing them is §5.1 working.

**But an empty candidate list is not evidence.** These threads were surfaced by title
similarity and nothing else; a decision split across two clusters whose titles do not resemble
each other is invisible to this method, and would sit in this list looking exactly like a
release PR. The 10 are unexplained, not cleared.

## What this establishes, and what it does not

It establishes that the `thread_key` stand-in **does** lose real decisions, at a measured rate
of 2 in 14 census threads — the first number ever attached to that risk. The README's stated
cost, "a decision genuinely spanning two threads cannot reach `reconstructed`", is now
observed rather than hypothesised.

It does not establish a rate for the repository as a whole. The census is 14 threads in one
repository over one 12-month window, adjudicated once.

**The adjudication is not independent.** #26 asked for human review; this pass was performed
by the same agent that built the candidate generator, which is precisely the arrangement §9
warns about elsewhere ("the query set was curated by the person who built the system"). A
second reader could reasonably reject 5928/5870 on the `not_planned` grounds above, which
would halve the headline number to 1 in 14.

Nothing here was written back to the graph. Creating `motivated_by` edges for these two pairs
would manufacture explicit evidence for a link no artifact records, which is the one thing the
provenance model exists to prevent. They are reported, not repaired.

## Reproducing

```
psql -U postgres -d dg -f eval/thread_key_census.sql
```

Defaults to repo 1, top 3 candidates per thread, similarity floor 0.15; override with
`-v repo=N -v top=N -v floor=0.NN`. The timeline checks used
`GET /repos/pallets/flask/issues/{n}/timeline`, reading `cross-referenced` and `connected`
events. Adjudication is by reading bodies and cannot be scripted.
