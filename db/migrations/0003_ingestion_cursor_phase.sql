-- 0003_ingestion_cursor_phase.sql
-- Cursor model for bounded backfill + resume (PRD v3.1 §8).
--
-- 0001 gave ingestion_cursor a single `last_since` watermark. That is enough for
-- steady-state polling but not for a bounded backfill, because the two move in
-- opposite directions: a backfill walks from now *backwards* to a window floor,
-- while steady-state polling walks *forwards* from the newest thing already seen.
-- One column cannot hold both without ambiguity about which direction it means.
--
-- Only the commits resource actually needs the two-phase treatment. The GitHub
-- issues endpoint accepts `since` and can be sorted updated-ascending, so its
-- backfill is just a forward walk starting at the window floor — the same
-- mechanism as steady state, seeded differently. The commits endpoint accepts
-- `since`/`until` but always returns newest-first, so backfill must page
-- backwards with `until` and checkpoint on the oldest row seen.
--
-- Resource strategies (see cursors.py):
--   UPDATED_ASC    issues, pull requests  — steady_watermark only
--   COMMITTED_DESC commits                — phase + backfill_cursor, then watermark
--   FULL           releases, workflows, CODEOWNERS — no window, ETag-conditional

BEGIN;

ALTER TABLE ingestion_cursor RENAME COLUMN last_since TO steady_watermark;

ALTER TABLE ingestion_cursor
    ADD COLUMN phase text NOT NULL DEFAULT 'backfill'
        CHECK (phase IN ('backfill', 'steady')),
    ADD COLUMN window_floor    timestamptz,
    ADD COLUMN backfill_cursor timestamptz,
    ADD COLUMN last_run_id     bigint REFERENCES ingestion_run (id) ON DELETE SET NULL;

COMMENT ON COLUMN ingestion_cursor.phase IS
    'backfill = still walking towards window_floor; steady = floor reached, now polling '
    'forward from steady_watermark. Only COMMITTED_DESC resources pass through backfill.';

COMMENT ON COLUMN ingestion_cursor.window_floor IS
    'Oldest point this resource intends to reach, set once at first run (now - N months). '
    'Persisted rather than recomputed so that resuming a run days later does not silently '
    'move the floor forward and leave a hole in the middle of the window.';

COMMENT ON COLUMN ingestion_cursor.backfill_cursor IS
    'Oldest timestamp fully processed during backfill. A run that dies mid-window resumes '
    'from here instead of restarting at now. Meaningless once phase = steady.';

COMMENT ON COLUMN ingestion_cursor.steady_watermark IS
    'Newest timestamp fully processed. Drives the `since` parameter for steady-state polls.';

COMMIT;
