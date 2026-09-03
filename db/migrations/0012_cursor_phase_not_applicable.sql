-- 0012: let `phase` be NULL for the resources that never backfill (issue #47 follow-up).
--
-- 0003 added `phase` as NOT NULL DEFAULT 'backfill' and, in the same file, documented that
-- only one strategy ever uses it:
--
--     COMMENT ON COLUMN ingestion_cursor.phase IS '... Only COMMITTED_DESC resources pass
--     through backfill.'
--
-- The writer never honoured that. `cursors.load()` seeded 'steady' for FULL resources and
-- fell through to the 'backfill' default for everything else, so every `issues` and `pulls`
-- row has claimed since 0003 to be walking towards a window floor by a mechanism that has
-- no floor to reach and no transition to make. The column comment and the column contents
-- have disagreed from the day the column existed.
--
-- This is not a cosmetic disagreement. UPDATED_ASC resources reach `steady` by never
-- leaving it -- backfill and steady-state are the same forward walk, seeded differently
-- (0003's own header says so) -- so nothing in the codebase can ever move that value off
-- 'backfill'. It is a latch wired shut, and it read as evidence: the recall audit cited
-- `ingestion_cursor: issues phase=backfill` as proof that ingestion had stalled mid-window
-- (eval/RECALL_AUDIT.md). The backfill had in fact stalled, but this column was not
-- evidence of it and would have read identically had it completed.
--
-- PR #49 stopped `dg status` printing the field. That fixed one reader. It left the value
-- in the table for `dg ingest`'s own log line and for anyone querying `ingestion_cursor`
-- directly, and left the audit's methodology note warning readers away from a column the
-- database populates on their behalf ("read the watermark, not the phase").
--
-- NULL is the fix because NULL is the only value that declines to make a claim. 'steady'
-- would be a second false statement -- it asserts a floor was reached -- and the honest
-- reading of `phase` for a forward walker is not "true" or "false" but "not applicable".
--
-- WHAT THIS DOES NOT DO. It adds no CHECK constraint pinning phase to NULL for non-commit
-- resources, though 0011 argued that the guard and not the caller should be the authority.
-- That guard would have to name the COMMITTED_DESC resources in SQL, duplicating
-- cursors.RESOURCE_STRATEGY into the schema; adding a second backwards-paging endpoint
-- would then fail at the constraint rather than at the code, which is a worse failure than
-- the one being prevented. The invariant stays in Python, where the strategy table lives.
-- The repair below hard-codes 'commits' too, but a migration describes the data at one
-- moment and does not outlive it -- a constraint would.

-- ORDERING. `cursors.load()` writes NULL for these resources from the moment this change
-- lands, so the code requires this migration. On a database still short of it, `dg ingest`
-- fails on the NOT NULL constraint rather than silently writing the old value -- the loud
-- direction, and `dg doctor` already reports the pending migration by name with `dg init`
-- as the fix. That is the same bargain every migration-bearing change here makes (0007's
-- thread_landed(), 0011's constraint); it is called out because a NOT NULL violation on a
-- cursor table reads less obviously than a missing function.

BEGIN;

-- 1. `phase` becomes optional. The existing CHECK (phase IN ('backfill','steady')) needs no
--    change: a CHECK holds unless it evaluates to FALSE, and NULL IN (...) is NULL.
ALTER TABLE ingestion_cursor ALTER COLUMN phase DROP NOT NULL;

-- 2. And loses its default, so 'backfill' cannot come back by omission. There is exactly
--    one INSERT into this table (cursors.load) and it always states the phase explicitly;
--    a default here would only ever serve to reintroduce the value being withdrawn.
ALTER TABLE ingestion_cursor ALTER COLUMN phase DROP DEFAULT;

-- 3. Repair. Every existing row for a resource that does not page backwards is asserting a
--    phase it cannot have; withdraw the assertion. Scoped by resource rather than by
--    strategy because the strategy table is Python-side -- see the note above. At the time
--    of writing `commits` is the only COMMITTED_DESC resource.
UPDATE ingestion_cursor SET phase = NULL, updated_at = now()
WHERE resource <> 'commits' AND phase IS NOT NULL;

COMMENT ON COLUMN ingestion_cursor.phase IS
    'backfill = still walking towards window_floor; steady = floor reached, now polling '
    'forward from steady_watermark. NULL = not applicable: this resource''s strategy has no '
    'backfill phase to be in. Only COMMITTED_DESC resources (commits) are ever non-NULL. '
    'Do not read this column as progress for anything else -- read steady_watermark.';

COMMIT;
