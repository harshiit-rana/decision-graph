# 0008 — Storage, ingestion, caching and scalability

**Status:** Accepted 2026-10-20

## Facts

- Postgres with one adjacency-list `edge` table; traversals are 2–3 hops (PRD §5.1). The
  PRD names a dedicated graph database as a documented future decision point, with no
  trigger defined.
- Query latency and graph size have never been measured or budgeted.
- Polling (`dg ingest`) is the completeness guarantee: cursors, a pinned `window_floor`,
  commit-per-page, resume (README "Ingestion"). `FULL` resources use ETags.
- Webhooks (#113) advance no cursor, by design.
- **Writes are last-writer-wins.** `db.upsert_node` overwrites `title`/`raw` with whatever
  payload arrives, keeping only `source_updated_at` as the max (`db.py:66-76`);
  `upsert_detail` overwrites `state`, `merged_at`, `state_reason` unconditionally
  (`db.py:104-106`). Polling always fetched current state, so this was harmless; webhooks
  make out-of-order and redelivered payloads possible. Found by reading the code; not yet
  reproduced.
- There is no scheduler and no single-writer lock: two `dg ingest` runs, or a poll and the
  webhook receiver, can write concurrently.
- **Extracted edges are never retracted.** `upsert_explicit_edge` conflicts only on rows
  with `valid_to IS NULL` (`db.py:147`), and the only writer of `valid_to` anywhere is
  `synthesis.py:297`. A closing keyword edited out of a PR body leaves its `closes` edge —
  Validation-grade rubric evidence — current forever.
- **`valid_from` is "first observed", not "written".** It is `COALESCE(observed_at, now())`
  (`db.py:146`), and body-reference extractors pass the artifact's `updated_at` at
  extraction time (`extractors.py:197-199`). An `--as-of` before that moment omits an edge
  that already existed, so point-in-time answers err towards refusal.
- **The webhook path skips post-processing.** `webhook._ingest` commits after extraction
  (`webhook.py:211-248`); it never drains pending references, synthesises Decisions, or
  applies corroboration, all of which `run.ingest` does (`run.py:70-115`). A merge
  delivered by webhook produces no Decision until the next poll.
- The traversal is a recursive CTE bounded by depth (3) and a cycle guard, with **no bound
  on rows** (`reasoning.py:174-210`); behaviour on a hub artifact in a larger repository is
  unmeasured.
- **No snapshot of an ingested graph exists.** The adjudicated flask corpus lives in one
  local Docker volume; re-ingesting pins a new `window_floor` and yields a different corpus.

## Options — storage

| | Option | Note |
|---|---|---|
| A | **Keep Postgres; define measured triggers** for revisiting (e.g. p95 traversal latency, edge count) | no migration; the trigger becomes a number someone can check |
| B | Migrate to a graph database | operational cost; no measured need |

## Options — ingestion

| | Option | Note |
|---|---|---|
| A | **Polling guarantees completeness; webhooks reduce latency; both go through one writer** | keeps the cursor as the only completeness claim |
| B | Webhook-first with periodic reconciliation | same parts, but makes the unreliable channel primary |

## Recommendation

- **Storage A.** Measure latency and growth on the second repository and write the
  trigger values into this ADR when it is accepted.
- **Ingestion A**, with three prerequisites before anyone runs both channels together:
  newest-wins writes (compare `updated_at` before overwriting), webhook delivery
  de-duplication by `X-GitHub-Delivery`, and a Postgres advisory lock so there is one
  writer at a time.
- **Caching:** ETags are already used for small resources; extend conditional requests to
  per-item fetches only if the rate budget measurement says it matters. No application cache
  for queries until latency is measured.

## Evidence that would change this

- p95 query latency above the budget on a realistic graph → index work first, then B.
- Webhook-only users with no poller → B, with reconciliation made mandatory.

## Consequences

- One small concurrency primitive (advisory lock) instead of a queue or event bus — the PRD
  explicitly excludes an event bus.
- Every write path gains an ordering check, tested with an out-of-order fixture.

## Decision

Accepted on 2026-10-20: **Storage A** and **Ingestion A**, as recommended. The owner replied "do it"
to the offer to accept this ADR's recommended options and unblock #163 and #164.

- Storage A is accepted, but its numeric triggers are **not yet written**: they need the #130
  growth measurements and the second repository (#132), which don't exist. Until then the trigger
  is a placeholder, and nothing here claims a latency budget.
- Ingestion A is accepted with its prerequisites. Status: newest-wins writes (#120) and edge
  retraction (#121) are done, webhook post-processing (#162) is partly done, and the advisory
  lock (#163) and delivery de-duplication (#164) are implemented with this decision.
- No queue and no event bus.

