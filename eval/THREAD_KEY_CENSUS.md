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

That outlier is sharper than the date filter alone makes it look. Issue 6044 was called a
**duplicate of 5870** on the thread before being rejected on its merits — so the teardown
decision has three artifacts in three clusters, and title similarity ranked the *duplicate of
the right issue* first and the right issue itself fifth. Similarity was not merely wrong here;
it was wrong in a way that looks like being right.

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
10008)". PR 6133 adds the shortcut, opening with "RFC 10008 isn't accepted yet, but it's been
kicking around for years... I'm adding it now". Same RFC, same feature, issue opened 49 days
before the merge and closed `completed`.

The timing settles it. Issue 6065 was closed at **20:18:12** on 2026-08-11, with no comment;
PR 6133 merged at **20:19:14** — **62 seconds later**. A maintainer closing an issue
`completed` one minute before merging the pull request is not doing housekeeping.

**Scope caveat, and it belongs with the number.** 6065 asks for two things, and 6133 delivers
one. The `MethodView` half arrived separately as commit `2a8a38b0` "support query in
methodview", 2h13m later, whose parent is `d8eaaba8` — 6133's own merge commit. It belongs to
no pull request at all. So even if the two clusters were joined, the resulting Decision's
implementation would be partial, with the remainder in a commit no PR contains. This does not
overturn the pairing; it qualifies what joining it would buy.

They are in different clusters. Issue 6065 sits in `thread:1:pr-6094` with PRs 6064, 6066,
6090 and 6127 — **all closed unmerged** — so its own thread lands in bucket B, "nothing
merged", while the PR that actually shipped the feature sits alone in bucket A with no issue.
The graph holds both halves of one decision and cannot join them.

Worth recording as a separate observation: PR 6133's body *does* carry a closing keyword —
`closes #3193` — and it is mis-scoped. From the surrounding text it means
`pallets/werkzeug`#3193; inside flask it resolves to an unrelated 2019 issue. It is queued in
`pending_reference` and inert only because flask#3193 is outside the window. A bare `#N`
resolves locally for GitHub too, so this is the author's error rather than the parser's — but
it sits inside one of the two confirmed pairs and is worth not discovering twice.

### 5928 / 5870 — genuine, and the `not_planned` question is a red herring

Issue 5870 reports that `do_teardown_request` does not wrap individual handlers, so one
raising handler skips all the rest. PR 5928 makes "all teardown callbacks... called despite
errors", collecting them into an `ExceptionGroup`. That is the fix for that report.

The strongest evidence is in the diff rather than the titles. When davidism closed 5870 he
cited the documented contract — teardown functions must be written so they do not fail. PR
5928 rewrites that exact sentence in `docs/appcontext.rst`:

```
-functions in a way that does not depend on other callbacks and will not fail.
+functions in a way that does not depend on other callbacks. All callbacks are
+called even if any raise an error.
```

The PR is, on its face, the reversal of the stated grounds for closing the issue. That is a
firmer basis than `sim = 0.159`.

**The `not_planned` closure is not what excludes this pair, and an earlier draft of this
document was wrong to frame it as an open rubric question.** Migration 0008 excluded
`not_planned` issues as a side effect, because "all four such clusters contain no merged PR
at all". That premise has held as the corpus grew — on the complete window:

| issue closure | issues | in a thread that landed |
|---|---|---|
| `not_planned` | 50 | **0** |
| `completed` | 29 | 15 |
| `duplicate` / none | 2 | 0 |

Not one of the 50 sits in a landed thread. So admitting `not_planned` as a Motivation would
change **zero rows**. What actually excludes 5870/5928 is clause 3 — the two artifacts are in
different clusters (`thread:1:issue-5870` and `thread:1:pr-5928`), and that is true whatever
`state_reason` says. The cost is charged to `thread_key`, full stop; there is no `not_planned`
rule to relax and relaxing one would be inert.

Context that argues against ever building a rule on `state_reason` at all: in this window
`not_planned` is the *majority* closure reason, 50 against 29 `completed`, and a substantial
share of recent ones are spam and junk reports rather than considered refusals. It is a
disposal label here, not a decision signal.

**Strongest argument against the pairing**, which belongs on the record: the causal chain may
run through a different artifact. On PR 5911 (closed unmerged, 2026-02-06) davidism wrote
"perhaps there's also a case for `try/except pass` around every teardown function... I'll look
into that", and 5928 lands two weeks later doing exactly that. PR 5927 sits in the same area.
So 5870 may be an antecedent with no influence, and 5928 is materially broader than 5870's
report. Neither reader found this decisive — the user-visible defect 5870 named is precisely
what 5928 fixes, and it is what the changelog line describes — but it is the real counterargument
and it is not similarity-based.

## Neither is detectable mechanically — checked, not assumed

The obvious objection is that the graph simply missed a link that was written down somewhere.
It was not. GitHub's own timeline for both issues records no connection to the merged PR:

- **6065** — cross-references 6064 and 6066 (its own thread's unmerged PRs, both of which say
  `fixes #6065` and both closed unmerged) plus four references from two unrelated
  repositories. Nothing pointing to 6133. Closed with no closing commit or PR.
- **5870** — no cross-references at all. Its entire timeline is three events: a comment, the
  close (both the same maintainer, same minute), and a lock by a bot a fortnight later.

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

Nothing here was written back to the graph. Creating `motivated_by` edges for these two pairs
would manufacture explicit evidence for a link no artifact records, which is the one thing the
provenance model exists to prevent. They are reported, not repaired.

## The adjudication was reviewed independently

The first draft disclosed that the adjudication was **not** independent — performed by the
same agent that built the candidate generator, the arrangement §9 warns about elsewhere ("the
query set was curated by the person who built the system") — and predicted that a second
reader might reject 5928/5870 on `not_planned` grounds, halving the headline to 1 in 14.

Two reviewers then read it, each starting cold, each told to form a verdict from the primary
artifacts before reading this document, and each asked to report where they differed. One was
given the factual question (do these pairs describe the same decision?); the other the
normative one (should a `not_planned` issue be admissible as Motivation?).

**Both confirmed both pairs. The predicted dissent did not materialise** — the second reviewer
went looking for the `not_planned` objection specifically and withdrew it, on the grounds that
5928 fixes the defect 5870 reported and that `state_reason` is not the binding gate anyway.
**2 of 14 stands.**

What the review changed is above, not here: the `not_planned` wrinkle is closed rather than
left open, the 6133/6065 entry gains its 62-second timing and loses its overstated "exactly
that", the 5928/5870 entry gains the documentation reversal and the PR 5911 counterargument,
and the 6065 cross-reference count is corrected.

**What this does not amount to.** Both reviewers were instances of the same model as the
author. Agreement among them is weaker evidence than agreement among independent people, and
it is not the human review #26 asked for — it is a check on whether the reading survives a
reader who did not perform it. The 5870/5928 pairing in particular still rests on semantic
judgment that no query can reproduce.

## Reproducing

```
psql -U postgres -d dg -f eval/thread_key_census.sql
```

Defaults to repo 1, top 3 candidates per thread, similarity floor 0.15; override with
`-v repo=N -v top=N -v floor=0.NN`. The timeline checks used
`GET /repos/pallets/flask/issues/{n}/timeline`, reading `cross-referenced` and `connected`
events. Adjudication is by reading bodies and cannot be scripted.
