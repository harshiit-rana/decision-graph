# decision-graph

Organizational Intelligence Engine — implementation of `PRD_v3.1_Organizational_Intelligence_Engine.md`.

**Phases 1–4 complete.** Time-versioned graph schema, extraction-first GitHub ingestion,
Decision synthesis, candidate retrieval, the Why/Impact traversal engine, evidence
tiering, and the §9 evaluation set — adjudicated 18 of 18 against flask's real history,
with the disclosures that number needs recorded in [`eval/RESULTS.md`](eval/RESULTS.md).

- **Code repo:** this repository.
- **Ingestion target:** `pallets/flask`.

## Getting started

**You need Docker Desktop. Nothing else.** No Python, no psql, no pip, no `gh`, no WSL.
Python and the database live inside containers; `dg` is a thin wrapper that talks to them.

```powershell
git clone https://github.com/harshiit-rana/decision-graph
cd decision-graph
.\dg.ps1 init
```

On macOS or Linux use `./dg init`. On Windows, `dg init` also works from Command Prompt.

### What `dg init` does, in four steps

1. **Database** — starts a Postgres container and waits until it actually accepts
   connections, rather than assuming it is ready.
2. **GitHub token** — prompts for one and verifies it against the API before saving. A
   token is required because unauthenticated GitHub allows 60 requests/hour, which cannot
   finish a backfill. **A public repo needs no scopes** — a classic token with nothing
   ticked works. Create one at <https://github.com/settings/tokens>.
3. **Target repository** — prompts for `owner/name` and confirms it exists.
4. **Schema** — applies the migrations. Safe to re-run: it keeps a ledger of what has been
   applied, and adopts a database that was migrated by hand before the ledger existed.

Answers are written to `.env` and read by every later command, so **you never export an
environment variable again**. Re-run `dg init` any time; add `--reconfigure` to change the
token or repo.

### Just run it

Run `dg` with no command and it opens an interactive menu — a banner, a live status line
(is the database up? is the graph empty?), and a numbered list you pick from. Nothing to
memorise; it asks for what a command would need. On a pipe or in a script it prints help
instead, so it never blocks waiting for input.

```powershell
.\dg.ps1
```

### The commands

Every menu choice maps to one of these, so you can skip the menu once you know them.

| command | what it does |
|---|---|
| `dg` *(no command)* | open the interactive menu |
| `dg init` | first-time setup, and safe to repeat |
| `dg doctor` | checks Docker, `.env`, token, rate limit, database, schema, data — and says how to fix each failure |
| `dg ingest --repo owner/name` | pulls the last 12 months into the graph |
| `dg status` | what is ingested: counts by type, per repo, cursor positions |
| `dg query "..." --mode why` | ask why something happened, or what a change affects |
| `dg ask "..."` | the same, asked in plain English — needs an Nvidia NIM key |

```powershell
.\dg.ps1 ingest --repo pallets/flask
.\dg.ps1 status
.\dg.ps1 query "change default redirect code to 303" --mode why
.\dg.ps1 query "#5898" --mode impact --depth 2
```

`ingest` and `query` forward any flag they do not recognise to the underlying tools, so
`--months`, `--resources`, `--max-pages`, `--as-of` and `--limit` all still work.

### What to expect

**Ingestion is the slow part** — roughly **2 API requests per pull request**, because
reviews and commits are one call each. A repo with 200 PRs costs about 450 requests
against a budget of 5,000/hour. If it runs out it stops cleanly, commits its cursor, and
prints how to continue; **re-run the same command and it resumes from exactly there.**

Check `dg status` afterwards. A cursor watermark short of today means there is more to
fetch — that signal sat unnoticed through an entire evaluation cycle once, which is why
`dg status` now prints it.

**Few or no Decisions is a real answer, not a failure.** The rubric needs a motivating
issue *and* merged work in the same thread. On flask, 238 thread clusters yield 15
Decisions. A repo that does not reference issues from pull requests will yield fewer.

### When something breaks

Run `dg doctor` first. It reports each check as pass/warn/fail with a specific next step,
rather than a stack trace:

```
Database
────────
  ok   database reachable
 FAIL  2 migration(s) not applied
        0008_rubric_requires_landing.sql, 0009_decision_thread_identity.sql
        → run `dg init` — it is safe to re-run
```

- **"Docker is installed but not running"** — start Docker Desktop and wait for it to settle.
- **Port already in use** — the database is published on 55434 only so you *can* reach it
  with your own tools; nothing in `dg` uses it. Set `DG_DB_PORT=55444` to move it.
- **Rebuilding after a Dockerfile change** — `dg rebuild`.
- **Starting over** — `docker compose down -v` deletes the database volume and everything
  ingested. `.env` survives.

### Running the internals directly

The original entry points still exist inside the container if you want them:
`dg-ingest` and `dg-query` are the same code `dg ingest` and `dg query` call.

## Layout

```
dg / dg.ps1 / dg.bat  host wrappers — the only thing you run directly
Dockerfile            the toolchain: Python, psql, dependencies
docker-compose.yml    app + database; injects DATABASE_URL
db/migrations/        schema, applied in numeric order by `dg init`
db/tests/             behavioural checks — rubrics, gates, queue, tiering
src/decision_graph/   cli, ingestion, synthesis, retrieval, reasoning
tests/                unit tests + traversal-fallback integration tests
```

The wrappers do only what must happen on the host: confirm Docker is running, build the
image once, and hand the arguments to `src/decision_graph/cli.py`. Every other decision
lives in that one file rather than three times over in three shell dialects.

## Schema

Node registry + per-type detail tables + one adjacency-list `edge` table. A composite
FK on `(id, node_type)` keeps referential integrity — a row in `commit` cannot point at
a non-commit node — while leaving Engine 2 a single table to traverse instead of a
15-way UNION across per-relationship tables.

Two columns carry provenance, split deliberately:

| column | values | meaning |
|---|---|---|
| `edge.tag` | `explicit` \| `inferred` | how the edge came to exist; set at creation, never rewritten |
| `edge.evidence_tier` | `explicit` \| `corroborated` \| `inferred` | §5.4 presentation tier; derived, Phase 3 upgrades explicit → corroborated |

A CHECK ties them (`tag='inferred' ⟺ evidence_tier='inferred'`), so only explicit edges
are ever eligible for the corroborated upgrade and inference cannot launder itself into
a higher tier. §5.3's "explicit and corroborated first" pass is then just `tag='explicit'`.

### The reconstructed-Decision rubric is executable

§5.1's rubric exists in three forms, all one implementation:

- `decision_rubric(node_id)` — as a query, returning each clause plus a failure reason
- `v_decision_rubric_audit` — as a standing invariant over every Decision
- `decision_rubric_guard` — as a **deferred constraint trigger**, on both `decision` and
  `edge`, so a reconstructed Decision cannot be stranded by later deleting the
  `motivated_by` edge that justified it

Motivation is mandatory; Implementation or Validation must also be present; all rubric
edges must share one PR/issue thread; and **something in that thread must have merged**.

That last clause is not an addition — §5.1 always defined Validation as "that it landed",
and §5.1's own worked example says "a *merged*, reviewed PR". The original implementation
accepted a `closes` edge as proof, but a closing keyword is typed by a contributor before
the outcome is known and survives rejection completely intact. 12 of the first 20
Decisions turned out to rest on pull requests that were never merged (issues #16/#17).
`thread_landed()` now checks merge state, and `retract_unsupported_decisions()` withdraws
Decisions whose evidence stops holding — the deferred guard only re-validates rows
something touches, so a claim made under an older rule would otherwise survive forever. `decision_status` has no `inferred` member — a
Decision that cannot reach explicit or reconstructed is simply not created.

**`thread_key` is the v1 stand-in for §5.1's "bounded time window"** and is the most
likely thing to be revisited. A thread is one conversation cluster (a PR, the issues it
closes, its commits, its reviews). Chosen over a literal time window because it is
mechanically checkable with no tunable magic number; the cost is that a decision
genuinely spanning two threads cannot reach `reconstructed`.

### Inferred edges are gated in the database

Three bounds, all enforced by the `edge_inferred_gate` trigger rather than by calling
code, so backfills and manual fixes stay bounded too:

1. relevance ≥ `graph_config.inferred_edge_min_relevance` (v1: 0.75)
2. at most `graph_config.inferred_edge_max_per_node` per node, both directions (v1: 4)
3. **no inferred edge may touch a Decision node**

## Ingestion

Bounded backfill — 12 months by default — with resume as the primary mechanism, not a
recovery path. The same cursor drives the first backfill and every steady-state poll, so
the resume path runs on every single run and cannot quietly rot.

The transaction commits after each page, immediately after that page's cursor advances,
so the cursor on disk never claims more progress than the graph contains.

Three cursor strategies, because the endpoints genuinely differ:

| strategy | resources | why |
|---|---|---|
| `UPDATED_ASC` | issues, pulls | `since` + updated-ascending; backfill and steady poll are the same forward walk |
| `COMMITTED_DESC` | commits | always newest-first, so backfill pages *backwards* with `until`, then flips to `steady` |
| `FULL` | releases, workflows, CODEOWNERS | small and unwindowed; ETag makes the no-change case one request |

`window_floor` is pinned on first contact rather than recomputed, so resuming a backfill
days later cannot move the floor forward and leave an unfetched hole mid-window.

`phase` belongs to `COMMITTED_DESC` alone and is NULL for the other two strategies, because
a forward walker has no floor to reach and so no transition to record. It used to sit on
`backfill` forever for issues and pulls, which nothing could ever retract — and the recall
audit read exactly that as proof of a stalled ingestion (#47, migration 0012). Read
`steady_watermark` for progress; it is the field that moves.

**Resume has now been exercised for real.** The first backfill stopped a third of the way
short and sat that way through the whole §9 cycle — the graph held 54 of 80 issues and 145
of 219 PRs without anything noticing. Re-running picked up from the committed watermark and
completed the window in 167 requests; a third run was a no-op at 6 requests. The mechanism
worked, and the gap it left is what the recall audit found (`eval/RECALL_AUDIT.md`).

Rate limiting is first-class: a 12-month backfill of a repo flask's size sits close
enough to the 5,000 req/hour budget that exhaustion is expected. The client stops
cleanly at a configurable floor and the next run resumes.

### The reference queue can now be un-said

A `#N` whose target is not in the graph yet is queued rather than dropped (issue #3) —
issues and PRs are walked updated-ascending, so a PR can be processed before the issue it
closes. The queue was append-and-resolve: the only thing that ever happened to a row was
becoming an edge. So a reference that was **wrong when it was queued stayed queued, armed,
indefinitely**, and would resolve the moment its target happened to arrive.

That is not hypothetical. Stopping the parser reading closing keywords out of HTML comments
(#59) could not reach the row it was about — `<!-- Fixes #11 -->`, template boilerplate
above a docs typo fix, waiting to become a `closes` edge, which is Validation-grade evidence
under the §5.1 rubric, if the window ever widened far enough to ingest flask#11 (a 2010
issue).

`--reconcile` now **retracts** a queued reference when re-reading the same stored body with
the current extractors no longer yields it (#61). The test is about the parser, not the
target, and that distinction is the whole design:

| the row | verdict |
|---|---|
| the current parser no longer reads it from the body | retracted |
| the target is outside the 12-month window | **left open** — unresolvable is not wrong |
| the number belongs to another repository (author's error) | left open — the parser still reads it |
| the source body has no stored text | left open — silence is not evidence |

On the flask graph that retracts exactly **1 of 26** open references: the `#11` above, and
nothing else.

Retraction is a column, not a `DELETE`, and the difference is the answer to the obvious
objection — that a parser *regression* would now withdraw good references rather than
merely fail to add them. The row stays with the reason it was withdrawn, `dg status` counts
it, and the open-row unique index excludes retracted rows so fixing the parser and
re-running `--reconcile` queues the reference again. A wrong retraction is visible and
reversible; a wrong `DELETE` would be neither.

**No Decision nodes are created by ingestion.** Phase 1 asserts artifacts and the links
between them; asserting that a *decision* occurred is gated by the rubric and belongs to
a later phase.

## Reasoning (Phase 2)

**Engine 1.5 — Candidate Retrieval.** Exact → identifier → prefix → trigram, in that
order, so an exact title always outranks a fuzzy match regardless of score. Deliberately
not ranked or embedding-based. It exists as a named seam because traversal stops working
past a toy graph without one, and adding a narrowing step after the traversal engine
assumes it can start anywhere touches every caller.

**Engine 2 — Graph Reasoning.** One `_walk` implementation, two modes. Only the start
node and edge set differ — sharing is a requirement, not an optimisation, since two
traversals would drift and §5.3's fallback rule would have to be correct twice.

The fallback order is written as an algorithm, not left as a principle for callers to
honour:

1. Walk `explicit` edges only. Thanks to the tag/tier invariant, `tag='explicit'` is
   exactly the "explicit and corroborated" set §5.3 names.
2. Only if that leaves the start disconnected from a plausible answer does the walk
   re-run with `inferred` edges admitted.
3. If an explicit path exists, inferred edges are excluded **entirely** — never fetched,
   not filtered out afterwards.

Clause 3 fails invisibly if implemented as a single pass that merely admits inferred
edges: the answers look fine and quietly blend tiers. The two-pass structure makes the
blend impossible, because the second pass runs only when the first returned nothing.

**A path's tier is its weakest link.** One inferred hop makes the whole answer inferred,
however many explicit edges surround it.

Two consequences worth knowing:

- Because no inferred edge may touch a Decision (§5.1), the Why fallback can never fire
  on its *first* hop from a Decision node. Any inferred bridge has to be further out.
- Point-in-time queries work via `--as-of`, using the `valid_from` / `valid_to` window
  that migration 0001 insisted on from v1.

```bash
dg-query "change default redirect code to 303" --mode why
dg-query "#5898" --mode impact --depth 2
dg-query "send_file type annotations" --mode why --as-of 2026-03-01T00:00:00Z
```

## Asking in English (`dg ask`)

`dg query` takes a title, a `#number`, or a sha. `dg ask` takes a sentence, uses a model to
turn it into one of those plus a mode, runs the *same* engines, and writes the result up in
prose:

```powershell
.\dg.ps1 ask "why did we change the default redirect code?"
```

It needs `NVIDIA_API_KEY` in `.env` (get one at <https://build.nvidia.com>) and calls
`meta/llama-3.1-70b-instruct` through Nvidia NIM. **The question and the traversal paths
leave your machine**; nothing else does, and no other command in this repository calls any
service but GitHub. `dg doctor` reports whether the key and the package are present, so you
find out before typing a question rather than after. Everything else works without it.

**The model does not decide anything.** Which paths exist, what tier each carries, and
whether there is an answer at all come from the graph. Three rules keep it that way (#65):

- **The trace prints with the prose**, in the same tier-marked form `dg query` uses, above
  it. §7 requires every answer to carry the traversal that produced it, and prose that
  cannot be checked against the graph is the one output this system should not print — an
  English paragraph reads identically whether it rests on four corroborated edges or one
  inferred guess.
- **A refusal is never generated.** When the engine finds nothing, the answer is a fixed
  sentence from the code and the model is never called. 8 of the 18 §9 outcomes are
  refusals; they are this system's most load-bearing output, and a model asked to *"politely
  state that the graph does not contain the answer"* is not making the same claim the engine
  is. It also means a missing key cannot turn a correct refusal into a crash.
- **The tier travels into the prompt**, which is told to name it and to use nothing but the
  paths — and an inferred fallback is flagged in the first sentence.

Installed by default in the Docker image. On a host install it is the optional extra:
`pip install 'decision-graph[nlp]'`.

## Evidence Ranking (Phase 3)

An explicit edge upgrades to `corroborated` iff (1) its `edge_type` is evidence-carrying,
(2) its endpoints share one thread cluster, and (3) that thread exhibits **≥3 of 4**
categories:

| category | edge + extractor | independent origin |
|---|---|---|
| DECLARED | `closes` via `pr_body_closing_keyword`/`commit_message_closing_keyword`, dst an issue | the author's prose |
| STRUCTURAL | `implements` via `pr_commit_list` | GitHub's PR↔commit structure |
| ATTESTED | `reviewed` via `pr_review` | a reviewer's action |
| PUBLISHED | `deployed_by` via `release_notes_reference` | a maintainer's release note |

**Only observed edges count.** Every `synthesis_*` extractor is excluded, because
`synthesis_closes_cluster` emits both `motivated_by` and `implemented_by` from a *single*
underlying `closes` edge — counting them as two converging signals would be one signal
wearing two hats. Note the deliberate asymmetry: synthesis edges are excluded from
*counting* categories but remain *eligible* for upgrade, since a `motivated_by` edge is
what a Why answer is made of and should carry the tier its thread earned.

`apply_corroboration()` is idempotent **and reversible** — it withdraws the tier from
threads whose evidence was later invalidated, not just grants it. It runs after every
ingestion, reading `v_edge_corroboration_audit` so the rubric lives in exactly one place.

A fifth category was drafted and rejected: "the same issue closed by ≥2 distinct source
artifacts". All `closes` edges come from one extractor, so that condition always implies
DECLARED and can never contribute an independent signal — nested, not independent. See
issue #12 for the provenance-label work that would make such a category meaningful.

## Known limitations on `pallets/flask`

Accepted deliberately rather than fixed by switching repos:

- **No CODEOWNERS** at `/CODEOWNERS`, `/.github/CODEOWNERS`, or `/docs/CODEOWNERS`. The
  `owns` extractor is built but no-ops; the graph gets no ownership edges. **The §9
  evaluation set cannot include ownership queries against flask.**
- **`has_wiki: false`.** The `wiki_page` extractor no-ops. On this repo `motivated_by`
  therefore resolves only to issues and PR bodies, never wiki pages.
- **The `corroborated` tier is sparse: 8 of 238 threads.**
  flask merges largely without formal GitHub reviews — 14 `reviewed` edges across 224
  PRs, and only 8 threads carry any review at all; 3 threads appear in release notes.
  The rubric was chosen on independence grounds and not tuned to raise this number, so
  §9 should report the tier as under-exercised on this repo rather than as a rubric
  weakness. A repo with mandatory review would populate it heavily.
- **Explicit-status Decisions are limited to what release notes itemise** (3 of 15).
  flask's changelog lives in `CHANGES.rst`, a repo file, and file-content ingestion is
  not built.
- Cross-references to artifacts **outside** the 12-month window are skipped rather than
  fetched — fetching them would make the window unbounded by the back door. They are
  counted in the run summary under `*_target_not_ingested` rather than hidden.

## Evaluation (§9)

```bash
DATABASE_URL=... python -m decision_graph.evaluation   # runs eval/query_set.json
python eval/render_report.py                            # -> eval/report.html
psql "$DATABASE_URL" -f eval/figures.sql                # every figure quoted below
psql "$DATABASE_URL" -f eval/recall_audit.sql           # the coverage buckets
```

**Every repository figure in this README was measured on 2026-09-05 and is reproducible by
running `eval/figures.sql`.** It exists because they were not, and drifted: an ingestion run
moved the corpus and the README went on quoting a coverage ratio whose denominator was two
measurements out of date while its numerator was current (#67). A number produced by a query
nobody wrote down can only be re-derived, never checked — the argument `eval/recall_audit.sql`
already makes for the audit (#46), one level out.

18 queries — 2 flagship plus 16 harder cases spanning causal reconstruction, impact,
retrieval by identifier and paraphrase, point-in-time, and four **correct-refusal** cases.

**The runner grades nothing.** §9 requires answers checked against the repository's real
history; a system scoring its own output against its own graph measures self-consistency,
not correctness. `verdict` is emitted null for every query and filled in by a human. The
only automatic assertion is `contract` — mechanical properties of the engine such as
"must return no answer for an artifact outside the window" — which are claims about the
code rather than about flask.

**Adjudicated: 18 of 18 correct, no failures — see [`eval/RESULTS.md`](eval/RESULTS.md)
for the figure with its disclosures.** Read them before quoting the number. 8 of the 18
correct outcomes are the system returning nothing, so a degenerate engine that always
returned nothing would score 8/18 on this set; the query set was curated by the person who
built the system; and the graph holds zero inferred edges, so nothing here measures
inference. 15 Decisions out of 238 threads — coverage, not precision, is the binding limit,
and this measures precision only. That ratio has now been taken three times against three
different corpora (#46, #48, #67), and the numerator has not moved once: the window grew by
45%, then by another 7 clusters, and produced no new Decision either time. The fourteenth and
fifteenth came instead from a parser fix (#50) — two merged PRs named their motivating issue
by URL rather than by `#N`, and the reference was being dropped. The 18 adjudicated outcomes
predate all of it and are unchanged by any of it.

Most usefully: **neither defect found during the evaluation cycle was caught by the
evaluation set.** Both #16/#17 and #19 were found by a human comparing a trace to GitHub,
which is what §9 is for — and which establishes that the automatic portion of the runner
has no power over correctness.

The point-in-time control is the load-bearing mechanical check: F1 returns 1 path and drops
to 0 when asked as of 2025-06, while F2 returns 17 both now and as of 2026-12 — so the time
filter restricts in one direction only, rather than being silently ignored.

**Every Decision in a trace names the artifact it is credited to.** A Why-walk reaching a
Decision across `motivated_by` stops there and never traverses `implemented_by`, so the
only PR number on the page used to be the one inside the `thread_key` — and that key names
the *cluster*, chosen order-independently as PR-preferring-then-lowest-number. Where a
change took two attempts it names the abandoned one: decision 928 sits in
`thread:30:pr-5867`, but 5867 was never merged and PR 5899 did the work. 3 of 13 Decisions
read that way. The graph was right and the report was misleading, which is the worse
failure of the two — it made a stale label and a stale edge indistinguishable on sight,
and telling those apart is the whole job during adjudication. Traces now carry the current
`implemented_by` target and its merge date, every one of them if there is more than one,
with an unmerged or absent implementer flagged rather than omitted (issue #19).

## Tests

```bash
psql "$DATABASE_URL" -f db/tests/0002_rubric_checks.sql             # 13 checks
psql "$DATABASE_URL" -f db/tests/0005_pending_reference_checks.sql  #  4 checks
psql "$DATABASE_URL" -f db/tests/0006_corroboration_checks.sql      #  7 checks
psql "$DATABASE_URL" -f db/tests/0008_landing_checks.sql            #  6 checks
psql "$DATABASE_URL" -f db/tests/0009_decision_identity_checks.sql  #  9 checks
psql "$DATABASE_URL" -f db/tests/0013_reference_retraction_checks.sql # 6 checks
DATABASE_URL=... python -m unittest discover -s tests               # 127 tests
```

`.github/workflows/ci.yml` runs all of it on every push and pull request, against Postgres
16 on both ends of the supported Python range. That is recent: the 0009 suite had been
aborting on its first statement since migration 0011 added a CHECK its fixture violated,
and all five of its checks were silently dead until something finally ran them (#55).

**A failing check now fails the run.** CI passes `ON_ERROR_STOP=1`, which turns a SQL
*error* into a red build; a check that merely recorded `passed = false` was not an error,
so four of the five suites printed `FAIL` inside a collapsed group and exited 0 — 30 of the
39 checks could not turn the build red (#62). Each suite now prints its results table and
then raises if anything failed, and `tests/test_sql_suites.py` asserts that every file in
`db/tests/` does, so a suite added later cannot quietly arrive without it.

All SQL suites run in a transaction and roll back. The Python suite runs 105 tests
standalone. Setting `DATABASE_URL` adds 19 integration tests — 7 for the traversal
fallback, which seed their own fixture because the real graph holds no inferred edges,
5 for the trace annotation, and 7 for reference retraction. Docker adds 3 more that compile the whole package on the
oldest Python `pyproject.toml` claims to support, because the Dockerfile pins a much newer
one and otherwise nothing ever exercises the declared floor.

A run without those is still reported as `OK`, with 22 of the 127 quietly skipped — so a
local pass and a complete pass look alike. CI sets both, and nothing is skipped there.

Three of the standalone tests assert the shell wrappers are pure ASCII. Windows PowerShell
5.1 decodes a BOM-less script using the system ANSI codepage, where an em-dash's last byte
becomes a closing quote and silently truncates the string it sits in.

These run on the host and still need Python. They are for working *on* the tool; using it
needs only Docker.

## License

MIT — see [LICENSE](LICENSE).
