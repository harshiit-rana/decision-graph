# 0009 — Edge validity against retraction: two different ends for an edge

**Status:** Accepted, 2026-10-17 (owner, on #181). Option A: a separate `retracted_at`
column, with `valid_to` semantics unchanged.

## Facts

Checked against `main` at `ad5fe16`.

- An edge carries four timestamps (`db/migrations/0001_core_graph.sql:338-341`):
  - `created_at`: "when WE created the edge";
  - `observed_at`: "when the signal occurred in GitHub";
  - `valid_from`, which defaults to `now()`;
  - `valid_to`: "NULL = currently valid".
- The only writer of `valid_to` is synthesis, superseding an implementer
  (`synthesis.py:297`, `SET valid_to = now()`). Extracted edges are never ended (#121): a
  closing keyword edited out of a PR body stays as rubric evidence.
- `valid_from` is set from the artifact's state at first observation, not from when the
  link was written in the repository (#124).
- `--as-of T` keeps edges with `valid_from <= T AND (valid_to IS NULL OR valid_to > T)`
  (`reasoning._WALK_SQL`).
- `valid_to IS NULL` is the definition of "current" throughout the database:
  - the partial unique index that stops duplicate live edges (0001:354-357), and the
    traversal indexes (0001:360-363);
  - 4 occurrences in the rubric (0002), 5 in corroboration (0006), 4 in the landing rule
    (0008) and 5 in closing-keyword provenance (0010);
  - every `trace` query in Python.
- The current Postgres is 16 (`docker-compose.yml`, CI).

## Hypotheses

- H1: Reusing `valid_to` to retract an edge (the obvious fix for #121) would make
  `--as-of` answer "what TraceMind believes now about time T", not "what the repository
  said at T". An edge retracted today would vanish from a query about last March, when the
  repository did say it.
- H2: Being able to replay what TraceMind *believed* at a past date is needed to reproduce
  a published evaluation after later ingestion has changed the graph.

## Options

| | Option | Cost | What it breaks |
|---|---|---|---|
| A | **Add `retracted_at`** (system time), plus a reason | One migration; every "current edge" predicate audited (listed above); the partial unique index rebuilt | Nothing, if the audit is complete, so the audit has a test |
| B | Reuse `valid_to` for retraction | Smallest change | H1: `--as-of` silently changes meaning |
| C | A PostgreSQL 18 temporal key (`WITHOUT OVERLAPS`), plus `retracted_at` | A, plus a 16 → 18 upgrade and a data migration | Existing volumes need `pg_upgrade` |

## Decision

**Option A, accepted by the owner on 2026-10-17. `valid_to` keeps its meaning. Retraction
never rewrites a validity interval and never deletes a row.**

### Two different events, two different columns

| Event | Example | Recorded as | Meaning |
|---|---|---|---|
| **The repository stopped saying it** | An author edits `Fixes #12` out of a PR body | `valid_to` = the `updated_at` of the first payload seen without it | Validity (event time). It was true in the repository from `valid_from` until somewhere in (the previous observation, `valid_to`]. |
| **TraceMind stopped believing it** | The extractor no longer reads that edge from the *same* body; it was our mistake, not the repository's change. Or an explicit `retract_edge()` | `retracted_at` = when we retracted it, plus `retraction_reason` | Belief (system time). The repository never said it, as far as we now know. |

Three columns record **when TraceMind learned each fact**: `created_at` (when it was
written), `retracted_at`, and a new `valid_to_recorded_at`, set by trigger whenever
`valid_to` is set. Together with the validity interval, they let a past answer be
reproduced exactly.

### What each query sees

An edge is visible when:

| Query | Rule |
|---|---|
| **current** (no flags) | `valid_to IS NULL AND retracted_at IS NULL` |
| **`--as-of T`**: what the repository said at T, as TraceMind *now* understands it | `valid_from <= T AND (valid_to IS NULL OR valid_to > T) AND retracted_at IS NULL` |
| **`--as-recorded R [--as-of T]`**: what TraceMind's graph held at moment R (T defaults to R) | `created_at <= R AND (retracted_at IS NULL OR retracted_at > R) AND valid_from <= T AND (valid_to IS NULL OR valid_to > T OR valid_to_recorded_at > R)` |

Worked example: an edge with `valid_from` 2026-01-01, created 2026-01-05, retracted
2026-03-01.

| Query | Visible? | Why |
|---|---|---|
| current | no | retracted |
| `--as-of 2026-02-01` | **no** | We now believe the repository never said it. A retracted edge is hidden from current-belief history at every T. |
| `--as-recorded 2026-02-01` | **yes** | That's what TraceMind held then, so a February answer can be reproduced. |
| `--as-recorded 2026-04-01` | no | Retracted before April. |

A second example: an edited-out reference, `valid_from` 2026-01-01, `valid_to`
2026-02-10, with the end written 2026-03-01.

| Query | Visible? |
|---|---|
| current | no |
| `--as-of 2026-02-01` | **yes**: the repository said it then |
| `--as-of 2026-02-20` | no |
| `--as-recorded 2026-02-20` | **yes**: TraceMind hadn't seen the edit yet |
| `--as-recorded 2026-03-05` | no |

### Audit guarantees, enforced in the database

1. **No deletes.** A trigger refuses `DELETE` on `edge` outside the explicit test-fixture
   escape the SQL suites use. Retraction is the only way an edge leaves current belief.
2. **Retraction is one-way and explained.** `retracted_at` can be set once, never changed
   or cleared, and only together with a non-empty `retraction_reason`.
3. **Validity is append-only.** Once set, `valid_to` can't change, and `valid_from` can't
   change after creation. A #124 backfill, if one is ever approved, is a migration that
   says so, not an `UPDATE` that ingestion can perform.
4. **Re-asserting is a new row.** If a later extraction yields a retracted edge again, a
   new edge is inserted. The current-edge unique index excludes retracted rows. The
   retracted row stays as history.
5. **Retraction is visible, never silent.** `v_edge_retraction_audit` lists every
   retracted edge with its reason and times. Answers say how many edges touching their
   nodes were retracted, and point to `--as-recorded`.

## Consequences

- Every query helper filters both columns. A test enumerates the "current edge" predicates
  in migrations and Python and fails if one checks `valid_to` without `retracted_at`.
- The JSON contract (#135) gains the retraction count additively, within v1.
- #121 implements both events: validity end on edit, retraction on extractor change.
  #124 is about `valid_from` only and is unaffected by this decision.
- The migration that adds these columns is applied by `dg init`, which now snapshots a
  populated database first (#122). On the flask graph, it runs only after the first
  snapshot has been taken and checked.
