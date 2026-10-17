# TraceMind

Organizational Intelligence Engine — implementation of `PRD_v3.2_Organizational_Intelligence_Engine.md`.

*Naming: **TraceMind** is the product. The repository, the Python package and the command
are `decision-graph` and `dg`.*

**What is built (Phases 1–4, plus the Phase 5 items marked).** Time-versioned graph schema,
extraction-first GitHub ingestion, Decision synthesis, candidate retrieval, the Why/Impact
traversal engine, evidence tiering, and the §9 evaluation set — adjudicated 18 of 18
against flask's real history, with the disclosures that number needs recorded in
[`eval/RESULTS.md`](eval/RESULTS.md). Phase 5 items shipped so far: Bus Factor (#91),
Expert Finder (#111), comment ingestion (#107), webhook ingestion (#113), `--format json`
(#135), prior attempts (#137), `dg context` (#138), and snapshot tooling (#122).

**What is *not* built.** The inferred-link proposer is a **stub**:
`inference.propose()` always returns no proposals (`src/decision_graph/inference.py`). Nothing
in this repository has ever produced an inferred edge from real data. The gate that would
admit one is tested, and so is the traversal fallback, but only on a seeded fixture. There
is no file-level history, no MCP server yet, and only one repository has been evaluated.
The roadmap is [`docs/ROADMAP.md`](docs/ROADMAP.md).

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

```powershell
.\dg.ps1
```

No command, and **nothing to memorise** — a banner, a live status line (is the database up?
is the graph empty?), and a numbered list:

```
  1  Browse decisions      see what was found, then drill into one
  2  Ask why               why did a change happen?
  3  Trace impact          what does a change affect, downstream?
  4  Natural Language Q&A  ask in plain English (requires an Nvidia API key)
  5  Report to HTML        every decision and its evidence, as a page
  6  Repository status     counts, cursors, decisions
  7  Health check          what works, and how to fix what does not
  8  Ingest a repository   pull the last 12 months into the graph
  i  Setup / reconfigure   token, target repo, migrations (safe to re-run)
```

**Browsing comes first because it is the only entry that asks nothing of you.** Every other
question starts by wanting the name of something — `what changed?` — which is precisely what
a newcomer does not have. Option 1 lists what was actually found and you pick a number:

```
  15 decisions, newest first:

   1  2026-08-11  #6093   reconstructed  IPv6 addresses parsed incorrectly because of
   ...
   8  2026-01-25  #5895   reconstructed  change default redirect code to 303
  12  2025-08-19  #5774   explicit       `stream_with_context` does not work with async

  pick a number (or Enter to go back): 8
```

Pressing Enter at any `what changed?` prompt drops you into that same list rather than
giving up.

**Every answer then offers what used to be a flag**, in context, one keystroke each — and
they are toggles, so the bar shows what is currently on:

```
  ------------------------------------------------------------------
  d  diagram       [on]          as a mermaid graph you can paste into GitHub
  v  details                     node ids and the extractor behind every edge
  a  all matches                 answer from every candidate, not just the best
  t  as of a date  [2025-06-01]  what the graph knew at a point in time
  s  switch mode   [why]         ask the other question about the same thing
  Enter  back
```

Each one re-runs the same `dg query` the flags would have run, so the menu cannot drift from
what the command prints — it *is* what the command prints. On a pipe or in a script `dg`
prints help instead, so it never blocks waiting for input that will not come.

### The commands

Every menu choice maps to one of these. The flags are the scripting interface; you never
need to type one to use the tool.

| command | what it does |
|---|---|
| `dg` *(no command)* | open the interactive menu |
| `dg init` | first-time setup, and safe to repeat |
| `dg doctor` | checks Docker, `.env`, token, rate limit, database, schema, data — and says how to fix each failure |
| `dg ingest --repo owner/name` | pulls the last 12 months into the graph |
| `dg status` | what is ingested: counts by type, per repo, cursor positions |
| `dg query "..." --mode why` | ask why something happened, or what a change affects |
| `dg ask "..."` | the same, asked in plain English — needs an Nvidia NIM key |
| `dg query ... --format mermaid` | the same answer as a diagram (`dot` also available) |
| `dg query "..." --mode attempts` | has this been tried before? earlier issues and PRs that ended without landing, how each ended, and where the thread later landed |
| `dg context "#N"` | one JSON document for a coding agent: why, impact, prior attempts, Decision facts, and what the graph does **not** record (`gaps`) |
| `dg query ... --format json` | the same answer as data, for programs and coding agents: a versioned contract ([schema](docs/schema/answer.v1.json)) |
| `dg report` | render every decision and its evidence to a browsable HTML page |
| `dg snapshot export` | save the whole graph to one file; `import` restores it into an empty database |

```powershell
.\dg.ps1 ingest --repo pallets/flask
.\dg.ps1 status
.\dg.ps1 query "change default redirect code to 303" --mode why
.\dg.ps1 query "#5898" --mode impact --depth 2
```

An answer states what the graph holds, grades the evidence, shows the traversal that
produced it, and links every artifact it names:

```
Why did this happen?   'change default redirect code to 303'

  starting from  issue #5895  change default redirect code to 303
                 exact title match

  What the graph says
    issue #5895 "change default redirect code to 303" motivated the decision in this thread
        that decision is reconstructed, implemented by PR #5898, merged 2026-01-25

  evidence: explicit - stated in the repository itself, a closing keyword, a review, a commit list

  Evidence trail  (1 path)
  [1] == explicit  1 hop
      issue #5895 - change default redirect code to 303
       \- motivated  [explicit]
      decision - change default redirect code to 303
          reconstructed, implemented by PR #5898, merged 2026-01-25

  Check it on GitHub
    issue #5895              https://github.com/pallets/flask/issues/5895
    pull request #5898       https://github.com/pallets/flask/pull/5898
```

**The summary sentence is generated from the graph, not by a model.** A Decision with a
`motivated_by` and an `implemented_by` edge determines that sentence completely; writing it
is templating, and nothing there is an inference. `dg ask` is the command that involves a
model, and it prints this same trace beside its prose.

Three things about that output are deliberate:

- **Artifacts are named as GitHub names them.** The trace used to print `issue:620`, a
  database primary key. The number and the URL have been stored on every node since the
  first migration and were printed on none of them, while §9's entire method is a human
  opening the artifact and comparing it to the claim (#69).
- **`-v` shows node ids and the extractor behind each edge.** Moved, not removed: they are
  what you query the graph with directly once something looks wrong.
- **The credited implementer is named and linked**, with its merge date. A Why-walk stops at
  the Decision and never reaches the pull request that did the work, so the only number a
  reader used to see was the one inside the `thread_key` — which names the *cluster*, and for
  6 of 15 Decisions names a pull request that was abandoned (#19).

A refusal is treated as a result, because it is one — 8 of the 18 §9 outcomes are refusals.
A refusal has to be *true*, which for most artifacts means naming how they ended:

```
  No answer - and that is a result, not a failure

    issue #6143 was closed as "not planned" on 2026-08-28, with nothing merged
    for it.

    That is why there is no Decision to walk back to: §5.1 asserts one only where
    work landed, so work that did not land produces no node. The silence is the
    rubric holding, not a gap in the graph.

    The last word on it, from a MEMBER, 2026-08-28:

      If you throw away the error saying you did something wrong, then yes
      you might end up with incorrect setup.

    That is what was said, not why it closed - the graph records no link between
    the two, and someone with standing writing it is not the same as the project
    deciding. Closed without landing covers rejection, supersession and abandonment
    alike.

    engine: no path found, explicit or inferred
    the inferred fallback was tried and also found nothing

  What to try
    - widen the walk:      --depth 3
    ...
```

The last paragraph is the point. *How* it ended is in the graph and checkable; *why* it ended
is in closing discussion that is not ingested, and guessing would be the failure §5.1 exists
to prevent — sampled closing comments include a reporter withdrawing (`"ok, my bad"`), a
duplicate, and an already-fixed bug, none of which are decisions.

Of the 263 artifacts a Why-walk refuses, **236 end in a way the graph records** — 185 pull
requests closed unmerged, 50 issues closed `not_planned`, one duplicate. The wording below is
what the other 27 get, and is correct only for them: work that landed with nothing recording
why (issue #86).

```
    Nothing in the ingested window records why this happened. The rubric needs
    a motivating issue and merged work in the same conversation; a change made
    without an issue leaves nothing to reconstruct a decision from.
```

### Answers as diagrams

`--format mermaid` (or `dot`) emits the traversal as a graph instead of prose. Raw, no code
fence, so it pipes into a file:

```powershell
.\dg.ps1 query "#5898" --mode impact --format mermaid > answer.mmd
```

```
graph LR
  n656["pull request #5898<br/>redirect defaults to 303"]
  n620(["issue #5895<br/>change default redirect code to 303"])
  n658(("commit eca5fd1<br/>redirect defaults to 303"))
  n1648{{"decision<br/>change default redirect code to 303"}}
  n656 -->|"closes . explicit"| n620
  n656 -->|"implements . explicit"| n658
  n1648 -->|"was implemented by . explicit"| n656
  classDef decision fill:#fde68a,stroke:#b45309,stroke-width:2px;
  class n1648 decision;
```

Paste it into a ```` ```mermaid ```` block on GitHub and it renders there. `--format dot`
gives Graphviz instead, with each node carrying its GitHub URL so a rendered SVG is
clickable.

Two constraints the diagram keeps, both about not claiming more than the walk did:

- **Only edges the engine actually traversed are drawn.** The credited implementer is named
  in the text output and is deliberately absent from the picture, because a Why-walk stops at
  the Decision and never crosses `implemented_by`. A diagram is read as the whole story, so
  it may contain nothing but the story.
- **The tier survives the translation** — solid explicit, thick corroborated, dashed
  inferred — *and* is written on every edge label. Line weight is a hint; the word is the
  claim, and a picture that renders a guess and a record identically undoes the tiering at
  the last step.

Edges are drawn along the graph's own direction rather than the walker's, so a Why-walk that
crosses `motivated_by` backwards still shows the arrow the edge actually has.

`ingest` and `query` forward any flag they do not recognise to the underlying tools, so
`--months`, `--resources`, `--max-pages`, `--as-of`, `--limit`, `--all` and `--format` all
still work.

### Has this been tried before? (`--mode attempts`)

```
dg query "redirect 303" --mode attempts
```

This lists earlier issues and pull requests matching the query that ended without
landing: closed unmerged, closed as not planned, or closed as a duplicate. Each shows when
it ended, the last word from someone with standing (quoted, never offered as the reason),
and, when a merged pull request shares its thread, **where it later landed** ("tried in
#A, landed in #B"). It reads only closure facts the graph already records, and adds no
assertion (#137, ADR 0003 option A).

It says how each attempt ended, never why. Matching is lexical, on titles and
identifiers, so a differently-worded attempt is missed. That's why an empty list says
"not evidence that nobody tried". `--format json` returns the same list as a
`kind: "attempts"` document under the [v1 schema](docs/schema/answer.v1.json).

### Everything at once, for an agent (`dg context`)

```
dg context "#5895" --max-paths 5 --max-quote-chars 400
```

An agent starting work on an issue would otherwise make several queries and merge them.
`dg context` returns one document (`kind: "context"`, [v1 schema](docs/schema/answer.v1.json))
holding:
- the resolved artifact;
- its Why answer or refusal, and its impact answer or refusal. These are the same objects
  `dg query --format json` returns, and a test checks they're identical;
- prior attempts matching its title (`--mode attempts`);
- the Decision facts;
- **`gaps`**: what the graph does not record, from a fixed vocabulary and never written by a
  model.

The gap codes are `why_refused` and `impact_refused` (each with its `refusal_kind`),
`inferred_fallback_used`, `no_decision_reached`, `no_review_recorded`, `match_not_exact`
and `no_prior_attempt_found`. A refused pack, abridged:

```json
"gaps": [
  {"code": "why_refused", "refusal_kind": "closed_not_planned", "detail": "No Why answer: ..."},
  {"code": "impact_refused", "refusal_kind": "nothing_downstream", "detail": "..."},
  {"code": "no_prior_attempt_found", "detail": "... Matching is lexical, so that is not evidence nobody tried."}
],
"truncated": null
```

`--max-paths` and `--max-quote-chars` keep it within an agent's budget, and cut honestly.
`truncated` counts every path and character removed, and gaps are computed before
anything is cut. Full answered and refused examples are the goldens
`tests/golden/contract/context_*.json`. The answered example is about 4.5 KB. Pack sizes
on the real flask graph are not yet measured (#173).

### Answers as data (`--format json`)

For a program, such as a coding agent, a CI job, or the MCP server planned in #139, the
same answer is available as JSON under a versioned contract
([`docs/schema/answer.v1.json`](docs/schema/answer.v1.json)). It is built from the same
objects as the text, not parsed from it, so the two can't disagree.

```
dg query "#5895" --format json
```

Every document has `schema_version` (currently `1`) and a `kind`: `answer`,
`no_match` (exit 1), or `error` (exit 2, including argparse usage errors). Each
`results[]` entry carries:

- `found`, the start node and how it `match`ed, and `used_inferred_fallback`;
- every path, with each step's `edge_type`, `evidence_tier`, `extractor` and walk
  direction (`traversed`);
- `decisions[]`, each with its `status`, credited `implementers` (and whether they merged),
  and `reviewers`.

A refusal is never a bare `null`. It is an object with a `kind`:

```json
"found": false,
"refusal": {
  "kind": "closed_not_planned",
  "engine_explanation": "no path within 3 hops",
  "inferred_fallback_tried": false,
  "closure": {
    "ref": "issue #5900", "kind": "not_planned", "closed_at": "2026-01-25T03:53:00+00:00",
    "last_word": {"body": "...", "author_association": "MEMBER", "at": "...",
                  "caveat": "This is what was said, not why it closed. ..."}
  }
}
```

The other refusal kinds are `closed_duplicate`, `closed_unmerged`,
`no_rationale_recorded`, `nothing_downstream` and `not_yet_recorded` (an `--as-of` before
the evidence existed).

**Repository text is untrusted.** Titles, body excerpts and quoted comments sit only in
the fields named by `repository_text_fields` (`title`, `excerpt`, `body`), and never inside
a sentence this system wrote. A consumer handing the document to a model can fence exactly
those fields.

**And it says what the window could have contained (#174).** A refusal can be misread as
"nothing happened" when the evidence was simply outside what was ingested. Every document
carries `coverage`: the repository's pinned window (`window_floor`, `ingested_through`,
`last_ingested_at`, and the last run's status), and a `checks` list with one entry per start
node, or one for the query when nothing matched. Each check has a `status` and a fixed
`reason`:

| status | reasons |
|---|---|
| `complete` | `ingestion_complete`: the cursors that pulled it are in steady state and the last run finished |
| `partial` | `backfill_incomplete`, `last_run_failed`, `last_run_stopped_early` (the rate-limit stop and the interrupt are stored as `succeeded` with a note, and are read from it) |
| `outside_window` | `older_than_window`: the query names a number lower than every issue or PR created inside the window |
| `unknown` | `no_ingestion_record`, `no_repository`, `not_in_graph`. The fact isn't recorded, so it is never guessed |

There is **no numeric completeness score**: nothing could calibrate one (ADR 0002). `complete`
says the cursors reached steady state, not that GitHub holds nothing more, and nothing about
activity since the last run. The window is **360 days** for `BACKFILL_MONTHS=12`, because the
floor is pinned as `now - 30 x months` days at first contact. A text refusal prints one line
with the same facts, ending "No record is not the same as nothing happened."

**And it can be replayed (#176).** Every document carries `provenance`:
- `request`: the query's own arguments (command, query, mode, `as_of`, depth, limits, quotes);
- `engine`: the package version, the schema version, and the migration ledger (how many are
  applied, the last one and its checksum, and a digest of the whole ledger, so any schema
  change alters it);
- `used`: the node ids the document is built from, and for each edge it crossed its id, type,
  tier, `observed_at`, `valid_from` and `created_at`.

It holds no timestamp of its own, so two runs over an unchanged database give identical
provenance, and when an answer changes between two runs the difference says whether the
graph, the code or the question moved. It never holds the DSN, a token or any `.env` value:
`request` is built from query arguments only, and a test checks that no `postgresql://`
string can appear. It is plain fields, not a PROV-O serialisation; mapping them to PROV-O or
SLSA terms as a separate view remains optional (#176).

**And it says who wrote it (#177).** Every object carrying one of those fields also has
`author_association` (GitHub's word: OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE, ...) and
`untrusted`, which is true unless the author is an OWNER, MEMBER or COLLABORATOR. GitHub
records the association for issues, pull requests and comments, and not for commits or
releases; a Decision's title is copied from its issue. Where it is unknown, it is `null` and
the text is `untrusted`. Unknown is never promoted. `--quotes maintainers` (on `dg query
--format json` and `dg context`) removes the text of every untrusted object, keeps the
object marked `suppressed: true`, and reports the count in `quotes.suppressed`. **These labels
are advisory.** A client model can still follow injected text from any author, and nothing
here stops it. They let a consumer fence or drop what it shouldn't trust, which is a
reduction, not a defence. Full examples of every kind are the goldens in `tests/golden/contract/`.
Removing or renaming a field fails `tests/test_contract.py` unless `schema_version` is
bumped.

### What to expect

**Ingestion is the slow part** — roughly **2 API requests per pull request**, because
reviews and commits are one call each. A repo with 200 PRs costs about 450 requests
against a budget of 5,000/hour. If it runs out it stops cleanly, commits its cursor, and
prints how to continue; **re-run the same command and it resumes from exactly there.**

Check `dg status` afterwards. A cursor watermark short of today means there is more to
fetch — that signal sat unnoticed through an entire evaluation cycle once, which is why
`dg status` now prints it.

**Few or no Decisions is a real answer, not a failure.** The rubric needs a motivating
issue *and* merged work in the same thread. On flask, 239 thread clusters yield 15
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
  with your own tools; nothing in `dg` uses it. Set `DG_DB_PORT=55444` to move it. It
  listens on `127.0.0.1` only. `DG_DB_BIND=0.0.0.0` exposes it to your network, which
  lets anyone there who knows the password rewrite the graph.
- **"database still uses the published default password"** — run `dg init`. It gives the
  database a random password, stores it in `.env` as `DG_DB_PASSWORD`, and keeps all your
  data. To connect with your own tools afterwards, use
  `postgresql://postgres:<DG_DB_PASSWORD>@localhost:55434/dg`.
- **"the database's password and DG_DB_PASSWORD in .env disagree"** — nothing is lost. Set
  the database back to the value in `.env`:
  `docker compose exec db psql -U postgres -c "ALTER ROLE postgres PASSWORD '<value>'"`
  (inside the container, the local socket needs no password). Don't delete the volume
  to fix this.
- **Rebuilding after a Dockerfile change** — `dg rebuild`.
- **Starting over** — `docker compose down -v` deletes the database volume and everything
  ingested. `.env` survives.

### Snapshots (`dg snapshot`)

```
dg snapshot export                 # snapshots/graph-<UTC time>.dgsnap
dg snapshot inspect FILE           # print its manifest
dg snapshot import FILE            # into an EMPTY database only
```

A snapshot is the whole graph in one file. It exists because every published figure was
measured on one graph that lived only in one Docker volume (#122). It is a zip:
- a manifest: the code commit, the migration ledger with checksums, repositories, counts
  by node type, and each table's row count and SHA-256;
- each table's rows in PostgreSQL's COPY format.

`pg_dump` isn't used, because the image's client is PostgreSQL 15 and the server is 16.

Import refuses:
- a snapshot taken with migrations this code doesn't have;
- one whose migration checksums differ;
- a target database that isn't empty.

It replays exactly the snapshot's migrations and loads the rows with triggers suspended,
so they're restored as they were, not re-judged. Then it re-hashes every table against
the manifest. Any mismatch fails the import, and nothing is left half-loaded. Newer
migrations then run through `dg init` as usual.

**`dg init` snapshots before it migrates a populated database**, into `snapshots/`, and
doesn't migrate if the snapshot fails (`--skip-snapshot` overrides that, for when you hold
a backup). `snapshots/` is git-ignored: a snapshot is a full copy of the ingested
repository's content. Adjudicated snapshots are published deliberately, as GitHub release
assets, and linked from `eval/RESULTS.md`. Export refuses if the configured `GITHUB_TOKEN`
appears anywhere in the data. Import uses `session_replication_role`, so it needs the
superuser that `dg` connects as today (see #172).

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

## Browsing the graph (`dg report`)

`dg query` answers one question and `--format mermaid` draws one answer. `dg report` renders
the whole graph — every Decision, its status, its motivating issue, the pull request credited
with the work and when it merged, the evidence tier on every edge, and a diagram of each
thread:

```powershell
.\dg.ps1 report --open
```

```
wrote /work/decision-graph-report.html
  15 decisions from 239 clusters
```

The page is one self-contained file. It fetches exactly one script — the mermaid UMD build,
pinned — and if that fetch fails the diagram source stays on the page as readable text
instead of vanishing. **A classic `<script>` tag, not an ES module**: `dg report` writes a
file you open from disk, and Chrome refuses a module import from a remote origin on a
`file://` page, which would render every diagram as nothing on exactly the path this command
produces.

It states its own coverage at the top, because a page listing 15 Decisions and saying nothing
about the 224 clusters that produced none is a coverage claim made by omission:

> **This is not the repository's history.** It is what the §5.1 rubric could evidence: a
> motivating issue and merged work in one conversation. 224 clusters produced no decision,
> most because no issue is referenced from the work at all, and a refusal to assert is the
> intended outcome there rather than a gap.

It also states its **concentration**, which is the PRD's Phase 5 Bus Factor and needs no new
ingestion — only a query over authorship the graph already holds:

> **10 of 15** decisions by davidism · **6** authors across all decisions
> **16 of 29** merged pull requests by davidism · **13** authors of merged pull requests

Two thirds of everything the rubric could evidence traces to one person, and a page that
lists what was decided while staying silent on how few people decided it is leaving out the
more consequential half. What it counts is authorship of the pull request each Decision is
credited to — not who understands the code, which the graph does not hold, and not a risk
score, since it knows nothing about who is still available. It is not weighted by review
either, and says so: this repository records review on a small fraction of its pull requests,
so review would rank almost everyone equally at zero. Bot accounts are named separately
rather than folded into the human counts.

Its numbers come from the graph at the moment you run it, not from `eval/results.json` —
that file is the record of an evaluation run and stays one — and they agree with
`eval/figures.sql`, which is the script that exists so any figure here can be checked.

## Reacting instead of polling (`dg-webhook`)

`dg ingest` polls: it walks cursors forward and asks GitHub what changed. `dg-webhook` reacts,
running the **same** extractors on a delivery — nothing here is a second ingestion path.

```bash
GITHUB_WEBHOOK_SECRET=... DATABASE_URL=... GITHUB_TOKEN=... dg-webhook --port 8000
```

It **refuses to start without a secret**, because a receiver with verification off is an
unauthenticated write endpoint into the graph, and the way that happens in practice is an unset
variable rather than a decision. Signatures are checked with `hmac.compare_digest` against the
raw body before it is parsed; missing, malformed and wrong all get the same 401, so the reply
does not tell a forger which attempt was closer.

Handled: `issues`, `pull_request`, `issue_comment`, `release`. Anything else is acknowledged
with 200 rather than refused — GitHub disables a hook after enough non-2xx replies, so erroring
on `star` would eventually switch off the deliveries that matter. **`push` is deliberately not
handled**: its commit objects are a different shape from the commits API, so `extract_commit`
would write subtly wrong nodes rather than fail.

It advances **no cursor**. A delivery says one thing changed; it cannot say nothing else did,
and writing a watermark from one would claim a completeness no webhook can offer. `dg ingest`
remains the thing that guarantees the window. It is also not a production server —
`http.server` is single-threaded and terminates no TLS.

**Order of arrival doesn't matter for issues and pull requests (#120).** GitHub doesn't
order deliveries. It retries failed ones, and a maintainer can redeliver any past one. A
payload whose `updated_at` is older than what the graph already holds is skipped
entirely: node content, the detail row (`merged_at`, `state`, `state_reason`), and the
references in its body. So an "opened" delivery replayed after "merged" can't un-merge a
pull request, and a closing keyword since edited out can't come back. Equal timestamps
apply, so re-polling unchanged state is a no-op. A payload with no `updated_at` counts as
oldest. Only GitHub's own timestamps are compared, never this machine's clock. Every
other node type has the same rule on its node row. Comments, reviews and PR commits are
always re-read from the REST API rather than taken from the delivery, so they are current
by construction.

## Asking in English (`dg ask`)

`dg query` takes a title, a `#number`, or a sha — abbreviated or full, since `commit eca5fd1`
is the only form of a commit the tool prints. `dg ask` takes a sentence, uses a model to
turn it into one of those plus a mode, runs the *same* engines, and writes the result up in
prose:

```powershell
.\dg.ps1 ask "why did we change the default redirect code?"
```

It needs `NVIDIA_API_KEY` in `.env` (get one at <https://build.nvidia.com>) and calls
`nvidia/nemotron-3-super-120b-a12b` through Nvidia NIM. **Set `DG_NLP_MODEL` in `.env` to
use a different one** — a hosted model id has an end-of-life date the code cannot see, and
the previous default was retired mid-project, so moving off a dead model must not require
editing Python and rebuilding an image. `dg doctor` reports whether the key and the
package are present, so you find out before typing a question rather than after.
Everything else works without it, and no other command in this repository calls any
service but GitHub.

**What leaves your machine.** `dg ask` makes two calls to the model. The first sends only
`question`, to turn it into a search term. The second sends one JSON document with these
fields, and nothing else:

| field | contents |
|---|---|
| `question` | your question, as typed |
| `paths` | the traversal: artifact references and **titles**, edge types, evidence tiers, extractor names |
| `evidence_tiers_present` | which tiers the paths carry |
| `inferred_fallback` | whether no explicit path existed |
| `decision_facts` | what each Decision is credited to (pull request numbers, merge dates) |
| `artifacts` | up to 1,500 characters of each issue, pull request or commit **body** on the paths |

Set `DG_ASK_SEND_BODIES=0` in `.env` to leave out `artifacts`. Explanations then lose the
motivation text, and only titles and structure are sent. This list was wrong until #119:
it used to say only the question and the paths left the machine, while bodies were sent
too. `tests/test_ask_boundary.py` now checks this table against the code.

**Repository text is treated as data, not instructions.** Titles and bodies are written by
anyone who can post to the repository, so they can contain text aimed at the model.
- The instructions sit alone in a system message.
- Everything above goes in the user message as JSON, each body in a field named
  `untrusted_artifact_text`. No delimiter in a body can close anything, and control and
  bidirectional-override characters are stripped.
- The system message tells the model the tiers come only from the `tier` fields.
- The adversarial bodies in `tests/fixtures/adversarial_bodies.json` (a fake tier claim, a
  delimiter break, chat-template tokens, forged JSON, an attacker link) all arrive intact
  as data in tests.

This **reduces** the risk; no prompt format removes it. The defence that doesn't depend on
the model is the trace printed above the prose.

**The paragraph is checked against the trace, mechanically (#125).** After the prose,
`dg ask` prints a citation check, from fixed sentences in the code. It uses regular
expressions and set comparison only: no second model call. It verifies:
- every issue or pull request number, commit sha (any prefix of 7 or more), release tag and
  GitHub URL the paragraph names is on the trail above it, or is credited by it;
- a number the paragraph calls an issue is an issue there, and likewise a pull request;
- GitHub links point at the trail's own repository, and any other link is one the trail
  printed (a link the model invented is how an injected instruction would try to send you
  somewhere);
- the paragraph doesn't claim a stronger tier than the trail grants ("corroborated" over
  explicit evidence, "explicit" over an inferred answer), skipping negations such as "not
  corroborated", and says "inferred" when the answer used the inferred fallback.

It does **not** verify whether the reasoning is right, sentences that name no artifact, or
people, teams, workflows and files, whose forms collide with ordinary English ("release
notes", "team members"). A clean result means nothing *recognisable* is unsupported. It is
not proof the paragraph is faithful. A warning never rewrites or hides the prose; it sits
beside it. A refusal is a fixed sentence and gets no check. The same result is available as
`cite_check.Report.to_dict()` for the JSON contract and the MCP server.

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
- **Comments are ingested as artifacts only** (migrations 0014/0015). The closing discussion
  is in the graph now, so a refusal no longer has to say it is absent — but it creates no
  Decision and satisfies no rubric clause. Sampled closing comments are the argument for
  stopping there: `"ok, my bad"` is the reporter withdrawing (`author_association: NONE`),
  `"Duplicate of ..."` is triage. `db/tests/0015_comment_checks.sql` enforces it, and the
  refusal counts are unchanged — 263 artifacts refused a Why-walk before comments and 263
  after.
- **`has_wiki: false`.** The `wiki_page` extractor no-ops. On this repo `motivated_by`
  therefore resolves only to issues and PR bodies, never wiki pages.
- **The `corroborated` tier is sparse: 8 of 239 threads.**
  flask merges largely without formal GitHub reviews — 14 `reviewed` edges across 226
  PRs, and only 8 threads carry any review at all; 3 threads appear in release notes.
  The rubric was chosen on independence grounds and not tuned to raise this number, so
  §9 should report the tier as under-exercised on this repo rather than as a rubric
  weakness. A repo with mandatory review would populate it heavily.
- **Explicit-status Decisions are limited to what release notes itemise** (3 of 15).
  flask's changelog lives in `CHANGES.rst`, a repo file, and file-content ingestion is not
  built — but building it would not move this number today, which is worth stating so
  nobody writes the parser to find out. `CHANGES.rst` carries 276 structured `:pr:` and
  `:issue:` refs across 63 version sections. Eleven resolve inside the 12-month window, and
  ten of those sit in a Decision thread; the question is which version cites them. Three are
  cited by 3.1.2 and are **already `explicit`**, caught by the release-body path. The other
  seven are cited by **3.2.0, which is "Unreleased"** — and §5.1 treats a release note as a
  formal artifact precisely because it records that something shipped, so upgrading on it
  would assert a decision reached users when it has not. The blocker is not the missing
  parser; it is that this repository has not cut a release since that work landed. (The graph
  is not merely behind: flask's newest release on the API is 3.1.3, which is exactly the
  releases watermark.)
- Cross-references to artifacts **outside** the 12-month window are skipped rather than
  fetched — fetching them would make the window unbounded by the back door. They are
  counted in the run summary under `*_target_not_ingested` rather than hidden.

## Evaluation (§9)

```bash
DATABASE_URL=... python -m decision_graph.evaluation   # runs eval/query_set.json
#   --output elsewhere.json  writes there instead of over the committed record
python eval/render_report.py                            # -> eval/report.html
psql "$DATABASE_URL" -f eval/figures.sql                # every figure quoted below
psql "$DATABASE_URL" -f eval/recall_audit.sql           # the coverage buckets
python eval/b2_landing_check.py                         # did any refused thread land anyway?
```

**Every repository figure in this README was measured on 2026-09-05 and is reproducible by
running `eval/figures.sql`.** It exists because they were not, and drifted: an ingestion run
moved the corpus and the README went on quoting a coverage ratio whose denominator was two
measurements out of date while its numerator was current (#67). A number produced by a query
nobody wrote down can only be re-derived, never checked — the argument `eval/recall_audit.sql`
already makes for the audit (#46), one level out.

18 queries — 2 flagship plus 16 harder cases spanning causal reconstruction, impact,
retrieval by identifier and by fuzzy title, point-in-time, and four **correct-refusal**
cases. **None of the answered queries tests paraphrase retrieval:** the one query that looks
like it (H5) is a bare `#5898` identifier lookup, and the one reworded query (H7) correctly
returns nothing. Paraphrase retrieval is unmeasured (#144).

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
built the system; and the graph holds zero inferred edges **because the proposer is a stub**
(`inference.propose()` returns nothing), not because flask has none, so nothing here
measures inference. 15 Decisions out of 239 threads — coverage, not precision, is the binding limit,
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
`thread:30:pr-5867`, but 5867 was never merged and PR 5899 did the work. 6 of 15 Decisions
read that way today, and 3 of 13 did when this was found (#19) — the share grew with the
corpus rather than being a quirk of the first few. The graph was right and the report was misleading, which is the worse
failure of the two — it made a stale label and a stale edge indistinguishable on sight,
and telling those apart is the whole job during adjudication. Traces now carry the current
`implemented_by` target and its merge date, every one of them if there is more than one,
with an unmerged or absent implementer flagged rather than omitted (issue #19).

## Security

[`SECURITY.md`](SECURITY.md) states what TraceMind trusts (nothing written in the ingested
repository), what leaves your machine, what listens on the network, and how to report a
vulnerability. Every statement in it names the code it describes.

## Tests

```bash
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
DATABASE_URL=... python -m unittest discover -s tests -t .
```

Each command prints its own totals. This section states none, because a hand-typed count
drifted from 209 to 394 before anyone noticed (#117). CI runs both on every push.

`-t .` matters. It makes `tests/` a package, whose `__init__.py` puts `src/` on the path,
so the suite runs in a plain checkout without `pip install -e .` (#123). Without it,
discovery reports 17 import errors that look like broken code.

`.github/workflows/ci.yml` runs all of it on every push and pull request, against Postgres
16 on both ends of the supported Python range. That is recent: the 0009 suite had been
aborting on its first statement since migration 0011 added a CHECK its fixture violated,
and all five of its checks were silently dead until something finally ran them (#55).

**A failing check now fails the run.** CI passes `ON_ERROR_STOP=1`, which turns a SQL
*error* into a red build; a check that merely recorded `passed = false` was not an error,
so four of the five suites then existing printed `FAIL` inside a collapsed group and
exited 0 — most of their checks could not turn the build red (#62). Each suite now prints its results table and
then raises if anything failed, and `tests/test_sql_suites.py` asserts that every file in
`db/tests/` does, so a suite added later cannot quietly arrive without it.

All SQL suites run in a transaction and roll back. The Python suite runs standalone.
Setting `DATABASE_URL` adds integration tests that seed their own fixtures (the real graph
holds no inferred edges) and tests that run every `dg report` query against the real schema,
which is the half a fixture cannot check. Docker adds tests that compile the whole package
on the oldest Python `pyproject.toml` claims to support, because the Dockerfile pins a much
newer one and otherwise nothing ever exercises the declared floor.

A run without those is still reported as `OK`, with the database-gated tests listed as
*skipped* in the summary line, so a local pass and a complete pass look alike. Read that
number. CI sets both, and skips almost nothing there.

Three of the standalone tests assert the shell wrappers are pure ASCII. Windows PowerShell
5.1 decodes a BOM-less script using the system ANSI codepage, where an em-dash's last byte
becomes a closing quote and silently truncates the string it sits in.

These run on the host and still need Python. They are for working *on* the tool; using it
needs only Docker.

## License

MIT — see [LICENSE](LICENSE).
