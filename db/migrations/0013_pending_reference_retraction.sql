-- 0013: let a queued reference be withdrawn (issue #61).
--
-- `pending_reference` has been append-and-resolve since 0005. A row is written when a
-- parsed reference names an artifact that is not in the graph, and the only thing that
-- ever happens to it afterwards is resolution into an edge. Nothing deletes one, nothing
-- expires one, and nothing re-derives the queue from the stored bodies. So a reference
-- that was WRONG when it was queued stays queued, armed, indefinitely.
--
-- The case that prompted this: PR #59 stopped the parser reading closing keywords out of
-- HTML comments, because flask's PR 6106 carries `<!-- Fixes #11 -->` template boilerplate
-- above a docs typo fix and GitHub honours no such thing. The fix stopped the parser
-- producing that reference. It could not reach the row already sitting in the queue --
-- which would resolve into a `closes` edge, Validation-tier evidence under the §5.1
-- rubric, the moment a widened window ingested flask#11 (a 2010 issue).
--
-- WHY A COLUMN AND NOT A DELETE. The queue's value is that it does not silently drop
-- things: 0005 exists because 9 of 30 closing keywords had been lost to a lookup that
-- found nothing and moved on. Deleting a row to fix that would be the same disappearance
-- with better intentions, and it would make the one genuinely dangerous case -- a parser
-- REGRESSION retracting good references -- both invisible and irreversible. A retracted
-- row stays, carrying the reason it was withdrawn, and can be counted, read, and argued
-- with. If the retraction was wrong, fixing the parser and re-running re-queues it.
--
-- WHAT RETRACTION MEANS, EXACTLY. Not "this reference cannot be resolved" -- an
-- out-of-window target is unresolvable and perfectly correct, and 5 of the 7 open `closes`
-- references on flask are exactly that. It means "the current parser, reading the same
-- stored body, no longer produces this reference at all". That is a statement about the
-- extractor, not about the target, and it is the only one that can be checked mechanically
-- without guessing at intent.

BEGIN;

ALTER TABLE pending_reference
    ADD COLUMN retracted_at     timestamptz,
    ADD COLUMN retraction_reason text;

ALTER TABLE pending_reference
    ADD CONSTRAINT pending_reference_retraction_consistent
        CHECK ((retracted_at IS NULL) = (retraction_reason IS NULL)),
    -- A row cannot be both. Resolution created an edge; retraction says the reference
    -- should never have existed. Allowing both would leave the edge standing with the
    -- queue asserting its source was withdrawn.
    ADD CONSTRAINT pending_reference_not_both_resolved_and_retracted
        CHECK (NOT (retracted_at IS NOT NULL AND resolved_at IS NOT NULL));

COMMENT ON COLUMN pending_reference.retracted_at IS
    'When this reference was withdrawn because the current parser no longer produces it '
    'from the source body. Distinct from unresolved-and-unresolvable: an out-of-window '
    'target is correct and stays open.';
COMMENT ON COLUMN pending_reference.retraction_reason IS
    'Why it was withdrawn, in text, so a retraction can be audited and disputed rather '
    'than merely observed.';

-- The open-row uniqueness must exclude retracted rows, or a reference the parser later
-- produces again could never be re-queued -- the retracted row would sit in the index
-- forever, silently swallowing the INSERT that ON CONFLICT DO NOTHING makes look like a
-- success. That is what makes a mistaken retraction recoverable, so it is load-bearing
-- rather than tidy. `_enqueue_reference`'s ON CONFLICT clause names this same predicate;
-- the two must be changed together or the index is not inferred.
DROP INDEX pending_reference_open_uidx;
CREATE UNIQUE INDEX pending_reference_open_uidx
    ON pending_reference (src_node_id, ref_number, edge_type)
    WHERE resolved_at IS NULL AND retracted_at IS NULL;

DROP INDEX pending_reference_open_idx;
CREATE INDEX pending_reference_open_idx
    ON pending_reference (repo_node_id, ref_number)
    WHERE resolved_at IS NULL AND retracted_at IS NULL;

-- The status view counted every unresolved row as open. A retracted row is unresolved and
-- must not be counted as waiting, but it also must not vanish -- the whole argument for a
-- column over a DELETE is that a wrong retraction stays visible. So it gets its own count
-- alongside, in the same view, rather than a second view nobody would think to read.
--
-- `retracted` is appended after the existing columns rather than slotted in beside the
-- counts it belongs with: CREATE OR REPLACE VIEW can add columns at the end and nothing
-- else, and DROP-then-CREATE would silently take any dependent view with it. Readability
-- of the SELECT list is not worth that.
CREATE OR REPLACE VIEW v_pending_reference_status AS
SELECT p.repo_node_id,
       p.edge_type,
       count(*) FILTER (WHERE p.retracted_at IS NULL)          AS open_refs,
       count(*) FILTER (WHERE p.retracted_at IS NULL
                          AND t.id IS NOT NULL)                AS resolvable_now,
       count(*) FILTER (WHERE p.retracted_at IS NULL
                          AND t.id IS NULL)                    AS target_outside_window,
       max(p.attempts) FILTER (WHERE p.retracted_at IS NULL)   AS max_attempts,
       count(*) FILTER (WHERE p.retracted_at IS NOT NULL)      AS retracted
FROM pending_reference p
LEFT JOIN node t
       ON t.repo_node_id = p.repo_node_id
      AND t.external_id = p.ref_number::text
      AND t.node_type IN ('issue', 'pull_request')
WHERE p.resolved_at IS NULL
GROUP BY p.repo_node_id, p.edge_type;

COMMENT ON TABLE pending_reference IS
    'Queue of parsed cross-references whose target was not yet in the graph. Drained at '
    'the end of every ingestion run (issue #3). An unresolved row is not necessarily a '
    'bug: a reference to an artifact outside the backfill window can never resolve, and '
    'stays here as a visible record rather than being silently dropped. A row the current '
    'parser no longer produces from its source body can be retracted (issue #61) -- '
    'withdrawn but retained, with its reason, and re-queueable if the parser changes back.';

COMMIT;
