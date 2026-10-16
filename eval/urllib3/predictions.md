# urllib3: predictions, registered before ingestion (#131)

**Registered 2026-10-17.** urllib3 has not been ingested into TraceMind. The commit that adds
this file is the pre-registration: the ingestion (#132) must be a later commit. **These
predictions are never edited.** Results and corrections are added as annotations in later
commits, under a new heading, so a prediction can't be quietly changed after the fact.

## What I looked at, and what I didn't

I read GitHub, through the `gh` CLI, and applied TraceMind's own pure parsing functions
(`refs.closing_refs`, `refs.mentioned_refs`, `refs.parse_codeowners`) to public text. I then
applied the rubric's rules as a set computation. All of it is in
`eval/urllib3/predict_inputs.py`, and its output is committed as `predict_inputs.json`, so
every number below can be re-derived. **I did not run any TraceMind ingestion, query or
extractor against urllib3, and no urllib3 row exists in any TraceMind database.**

Looking at GitHub is unavoidable: queries "chosen from GitHub directly" need it. The risk is
that knowing GitHub's links makes the predictions easy. That is partly the point (a
prediction of what the extractors *should* find), and partly a bias, which is why the
random arm exists and why #143 asks for a second person.

The window the predictions assume is 2025-10-17 to 2026-10-17 (12 months). If ingestion
runs later, its window floor moves. Re-run `predict_inputs.py` with the new window and
commit the result as an annotation. Don't edit this file.

## The four required predictions

| Quantity | Prediction | Range I'd accept as "right" | Reasoning |
|---|---|---|---|
| **Decisions** | **28** | 24 to 33 | 154 PRs merged in the window. 30 have a closing keyword in the body, and none has one only in a commit message. Those 30 PRs yield 31 `closes` edges to issues GitHub confirms are issues and that were updated inside the window, so they are ingested. Merging PRs that close the same issue into one thread (as `threads.union` does) gives **28 threads**, each containing a merged PR, so each lands. Uncertainty: a squash commit on the default branch that states a keyword but isn't in a PR's commit list would add Decisions I can't see; a thread merged unexpectedly through shared commits would remove one. |
| **Explicit / reconstructed split** | **14 explicit, 14 reconstructed** | 12 to 16 explicit | `upgrade_explicit` promotes a Decision when any artifact in its thread has a `deployed_by` edge to a release. Release 2.8.0 cites 29 numbers, 2.6.0 cites 11, and 2.6.1 cites one. 14 of the 28 predicted threads contain a cited artifact. Uncertainty: whether all in-window releases are ingested, and whether a cited PR or issue is itself ingested. |
| **Corroborated share of Decision threads** | **96%** (27 of 28) | 90% to 100% (at least 25 of 28) | A thread is corroborated when it shows 3 of 4 categories. DECLARED (the body keyword) and STRUCTURAL (the merged PR's commit list) are present in every Decision thread by construction. The third comes from ATTESTED (any review on a PR in the thread; `extract_reviews` records every review state and reviewer) or PUBLISHED. 27 of 28 threads have a reviewed PR. The one that doesn't is PR #4990. Flask, with few reviews, had 8 of 239 threads corroborated; urllib3 reviews almost everything (146 of 154 merged PRs have a review of some kind). |
| **`owns` edges** | **20**, from 4 people to 5 `codeowners_scope` nodes | exactly 20, if the file is unchanged | `.github/CODEOWNERS` (blob sha `8a4c410e33b39d37336ec787825ecf6ff202a819`) has 5 path rules, each owned by the same four logins (`sethmlarson`, `pquentin`, `shazow`, `illia-v`), so 5 x 4 = 20 edges. Every rule is deploy or config (`.github/workflows/`, `.github/CODEOWNERS`, `CHANGES.rst`, `pyproject.toml`, `ci/`). Ownership edges will exist but **cover no source code**, so the results must not imply wide ownership. |

## Further predictions, made now so they can fail

- **Decisions per merged PR.** 30 of 154 merged PRs (19%) are in a Decision thread. The
  other 81% will refuse a Why query. Most are dependency bumps and CI changes with no issue.
- **Bots.** 38 of 154 merged PRs (25%) are authored by bots. **None** of the Decision PRs
  is bot-authored (`decision_prs_authored_by_bots` is empty).
- **A recall ceiling set by GitHub's UI, not by us.** 7 of the 37 PRs GitHub links to an
  issue (19%) carry no closing keyword anywhere (`#3715`, `#3721`, `#3799`, `#4954`,
  `#5020`, `#5093`, `#5140`). They are sidebar links. The extraction-first design can't see
  them, so they will not appear in Decisions. That is a recall limit on this repository,
  and `X6` tests one. No parser keyword is missing: I read the bodies.
- **No closed-as-duplicate case exists** in the window (GitHub reports zero), so that
  refusal kind goes untested here.
- **Cost.** Ingestion completes in one run, using roughly 1,000 to 3,000 GitHub API
  requests, inside one hourly budget. Basis: about 2 requests per PR (README), plus
  reviews, comments and commit lists.
- **Query set (`query_set.json`, 29 queries).** I predict:
  - R1 to R5 return answers at tier corroborated, credited to the drawn PR.
  - R6 returns an answer crediting #3697, *not* the drawn PR #3721.
  - `H1` credits one of its two PRs.
  - `H2` returns tier explicit.
  - Paraphrases: `P1` passes, `P2` **fails**, `P3` is partial.
  - Each refusal returns the kind listed in its `predicted` field.
  - Every `contract` field holds.
  - Of 29 queries, a human adjudicating against GitHub will find **26 correct** (P2 and P3
    wrong or partial, and one more among H1 to H4 or the temporal pair).

## What would show these predictions wrong

- Decisions outside 24 to 33: the thread model or ingestion window isn't doing what I
  think. A count well above 33 would suggest squash-commit keywords are being read; well
  below, that the landing gate or window is stricter than I computed.
- Fewer than 25 corroborated: the ATTESTED category isn't counting reviews the way
  `extract_reviews` writes them.
- Any bot-authored Decision, or any `owns` edge other than 20.
- R6 crediting #3721: the extractor reads GitHub's sidebar links, which would contradict
  what the README says it does.

## Fixed rules, so the set can't be tuned afterwards

1. **The random arm** is `random.Random(20261017).sample(sorted(eligible), 6)` over the 34
   merged PRs that GitHub links to an issue and that merged in 2025-11-01 to 2026-09-30.
   The seed is the registration date, chosen before the draw. The input is `pool.json`, the
   output `draw.json`, and the whole procedure is `select_queries.py`. Re-running `draw`
   on the committed pool must reproduce `draw.json`.
2. **Query form** for each draw is fixed by parity: odd draws use the linked issue's exact
   title, even draws use `#<issue number>`. The lower issue number when a PR links two.
3. **6 of the 10 why-queries are random**, and each query is labelled `random` or `hand`.
4. Hand-picked queries were chosen to cover cases the random draw can't be relied on to
   reach (an unreviewed thread, a thread with two PRs, an issue older than the window), and
   that choice is itself disclosed here.
5. **No query is dropped or replaced after ingestion**, including one that turns out to be
   badly chosen. It is annotated.

## Annotations

*(Results, and anything that turns out to have been mis-specified, go below in later
commits. Nothing above this line is edited.)*
