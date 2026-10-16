# 0009 — Edge validity against retraction: two different ends for an edge

**Status:** Proposed — awaiting owner decision (#181)

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
  repository did say it. That breaks the temporal guarantee ADR 0006's agents and the
  evaluation (#128) depend on.
- H2: Being able to replay what TraceMind *believed* at a past date ("as recorded") is
  needed to reproduce a published evaluation after later ingestion has changed the graph.
  Today it cannot be done.

## Options

| | Option | Cost | What it breaks |
|---|---|---|---|
| A | **Add `retracted_at`** (system time; plus `retracted_by`, naming the extractor or reason). "Current" becomes `valid_to IS NULL AND retracted_at IS NULL`. `--as-of T` keeps event-time semantics; a new `--as-recorded T` filters on `created_at <= T AND (retracted_at IS NULL OR retracted_at > T)` | One migration; every `valid_to IS NULL` predicate audited (listed above); the partial unique index rebuilt | Nothing, if the audit is complete. An incomplete audit lets a retracted edge count as rubric evidence, so the audit needs a test per predicate |
| B | Reuse `valid_to` for retraction | Smallest change | H1: `--as-of` silently changes meaning |
| C | A temporal key: `tstzrange` validity with `UNIQUE (src, dst, type, validity WITHOUT OVERLAPS)` ([PostgreSQL 18](https://www.postgresql.org/docs/18/sql-createtable.html)), plus `retracted_at` | Option A's cost, plus a Postgres 16 → 18 upgrade in compose and CI, and a data migration of every edge | Requires the upgrade; existing volumes need `pg_upgrade` or a dump and restore |

## Recommendation

**A now; C only if overlapping-validity bugs actually appear.** This is the
invalidate-don't-delete model that Graphiti's paper describes
([arXiv 2501.13956](https://arxiv.org/pdf/2501.13956)) and that Fowler's bitemporal history
makes append-only ([article](https://martinfowler.com/articles/bitemporal-history.html)),
without a database upgrade.

Order of work:
1. This ADR.
2. #121 implements retraction with `retracted_at`.
3. #124 fixes `valid_from` to mean when the link was written, where the API exposes it, and
   documents the clock.
4. `--as-recorded` lands with the evaluation replay that needs it.

## Evidence that would change this

- An audit finds a `valid_to IS NULL` predicate whose meaning can't be split cleanly → re-scope
  before migrating.
- A real overlapping-validity defect → C.

## Consequences

- Answers gain a distinction they can't make today: "the repository stopped saying this"
  against "TraceMind stopped believing it". The JSON contract (#135) would expose both as an
  additive v1 change.
- Every future query helper must filter both columns. A test enumerates edge predicates
  in migrations and code so that none is missed.

## Decision

_Pending._
