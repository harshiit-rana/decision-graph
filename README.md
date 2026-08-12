# decision-graph

Organizational Intelligence Engine — implementation of `PRD_v3.1_Organizational_Intelligence_Engine.md`.

**Phase 1 only.** The knowledge graph schema and extraction-first GitHub ingestion.
Retrieval, traversal, reasoning, and Decision synthesis (Phase 2+) are not built.

- **Code repo:** this repository.
- **Ingestion target:** `pallets/flask`.

## Layout

```
db/migrations/     schema (apply in numeric order)
db/tests/          behavioural checks for the rubric + inferred-edge gate
src/decision_graph/  ingestion
tests/             unit tests for the parsers
```

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
edges must share one PR/issue thread. `decision_status` has no `inferred` member — a
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

Rate limiting is first-class: a 12-month backfill of a repo flask's size sits close
enough to the 5,000 req/hour budget that exhaustion is expected. The client stops
cleanly at a configurable floor and the next run resumes.

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

## Known limitations on `pallets/flask`

Accepted deliberately rather than fixed by switching repos:

- **No CODEOWNERS** at `/CODEOWNERS`, `/.github/CODEOWNERS`, or `/docs/CODEOWNERS`. The
  `owns` extractor is built but no-ops; the graph gets no ownership edges. **The §9
  evaluation set cannot include ownership queries against flask.**
- **`has_wiki: false`.** The `wiki_page` extractor no-ops. On this repo `motivated_by`
  therefore resolves only to issues and PR bodies, never wiki pages.
- Cross-references to artifacts **outside** the 12-month window are skipped rather than
  fetched — fetching them would make the window unbounded by the back door. They are
  counted in the run summary under `*_target_not_ingested` rather than hidden.

## Running it

```bash
docker run -d --name oie-pg -e POSTGRES_PASSWORD=oie -e POSTGRES_DB=oie \
    -p 55432:5432 postgres:16

for f in db/migrations/*.sql; do
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done

pip install -e .
export DATABASE_URL="postgresql://postgres:oie@localhost:55432/oie"
export GITHUB_TOKEN="$(gh auth token)"

dg-ingest --resources issues commits releases codeowners
dg-ingest --resources issues --max-pages 1   # smoke test; leaves cursor mid-window
```

`GITHUB_TOKEN` is required — unauthenticated access is capped at 60 req/hour, which
cannot complete a backfill.

## Tests

```bash
psql "$DATABASE_URL" -f db/tests/0002_rubric_checks.sql             # 13 checks
psql "$DATABASE_URL" -f db/tests/0005_pending_reference_checks.sql  #  4 checks
DATABASE_URL=... python -m unittest discover -s tests               # 18 checks
```

All SQL suites run in a transaction and roll back. The Python suite runs 11 unit tests
standalone; setting `DATABASE_URL` adds the 7 traversal-fallback integration tests,
which seed their own fixture because the real graph holds no inferred edges.
