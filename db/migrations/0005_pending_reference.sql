-- 0005_pending_reference.sql
-- Deferred resolution of forward references (issue #3).
--
-- extractors._lookup_by_number resolved "#N" against whatever was in the graph at that
-- instant. Issues and PRs are walked updated-ascending, so a PR whose updated_at
-- precedes the issue it closes was written first, found nothing, and dropped the edge.
-- Nothing revisited it: the cursor had already moved past both. Measured on the first
-- pallets/flask window, 9 of 30 closing keywords were lost this way.
--
-- The queue records the *intent* to link, so resolution can be retried once the rest of
-- the window has landed. It deliberately stores a reference NUMBER rather than a target
-- node id — the target does not exist yet, which is the entire problem.
--
-- This preserves the bounded window. Resolution only retries the LOOKUP; it never
-- fetches the missing target. A reference to an artifact genuinely outside the 12-month
-- window stays unresolved forever, which is correct, and is visible as a row with a
-- climbing attempts count rather than as silence.

BEGIN;

CREATE TABLE pending_reference (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo_node_id     bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,

    -- The artifact that made the reference. Known; it is the target that is missing.
    src_node_id      bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,
    ref_number       integer NOT NULL,
    edge_type        edge_type NOT NULL,

    -- Carried so the edge created on resolution is indistinguishable from one written
    -- inline at extraction time — same extractor, same source_ref, same observed_at.
    extractor        text NOT NULL,
    source_ref       text,
    observed_at      timestamptz,

    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_attempt_at  timestamptz,
    attempts         integer NOT NULL DEFAULT 0,

    resolved_at      timestamptz,
    resolved_edge_id bigint REFERENCES edge (id) ON DELETE SET NULL,

    CONSTRAINT pending_reference_resolution_consistent
        CHECK ((resolved_at IS NULL) = (resolved_edge_id IS NULL))
);

-- One open row per (source, number, edge type). Re-parsing the same body on a later
-- poll must not enqueue a duplicate.
CREATE UNIQUE INDEX pending_reference_open_uidx
    ON pending_reference (src_node_id, ref_number, edge_type)
    WHERE resolved_at IS NULL;

CREATE INDEX pending_reference_open_idx
    ON pending_reference (repo_node_id, ref_number)
    WHERE resolved_at IS NULL;

COMMENT ON TABLE pending_reference IS
    'Queue of parsed cross-references whose target was not yet in the graph. Drained at '
    'the end of every ingestion run (issue #3). An unresolved row is not necessarily a '
    'bug: a reference to an artifact outside the backfill window can never resolve, and '
    'stays here as a visible record rather than being silently dropped.';

-- Unresolved references, split by whether the target has since arrived. A row in
-- `resolvable_now` means the drain has not run yet; a row in `outside_window` is the
-- expected, correct outcome of bounding the backfill.
CREATE VIEW v_pending_reference_status AS
SELECT p.repo_node_id,
       p.edge_type,
       count(*)                                            AS open_refs,
       count(*) FILTER (WHERE t.id IS NOT NULL)            AS resolvable_now,
       count(*) FILTER (WHERE t.id IS NULL)                AS target_outside_window,
       max(p.attempts)                                     AS max_attempts
FROM pending_reference p
LEFT JOIN node t
       ON t.repo_node_id = p.repo_node_id
      AND t.external_id = p.ref_number::text
      AND t.node_type IN ('issue', 'pull_request')
WHERE p.resolved_at IS NULL
GROUP BY p.repo_node_id, p.edge_type;

COMMIT;
