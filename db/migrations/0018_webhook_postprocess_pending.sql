-- 0018: which applied webhook deliveries are still waiting for post-processing (#162).
--
-- The receiver extracts a delivery, replies, and then runs post-processing (queued references,
-- Decision synthesis, evidence tiers), debounced. That "still to do" lived only in the receiver's
-- memory: a crash lost it and nothing showed it. These two columns make it durable and visible.
--
-- POSTPROCESSED_AT. NULL means applied but not yet covered by a complete post-processing pass;
-- `dg status` counts those. A pass sets it for exactly the deliveries that were committed before
-- the pass began, so a delivery applied while a pass runs stays pending for the next one. Either
-- writer's pass counts, the receiver's or `dg ingest`'s, because there is one `run.postprocess`.
--
-- REPO_NODE_ID. Which repository the delivery was applied to. A pass is per repository, so a poll
-- of one repository must not mark another's deliveries done. NULL for rows recorded before this
-- migration: whether those were post-processed was never recorded, so they are not reported as
-- pending either, and they age out with the 30-day pruning.
--
-- Still no payloads: a repository id and a timestamp.

BEGIN;

ALTER TABLE webhook_delivery
    ADD COLUMN repo_node_id     bigint REFERENCES node (id) ON DELETE CASCADE,
    ADD COLUMN postprocessed_at timestamptz;

-- `dg status` and each pass read only the pending rows of one repository.
CREATE INDEX webhook_delivery_pending_idx
    ON webhook_delivery (repo_node_id, received_at) WHERE postprocessed_at IS NULL;

COMMENT ON COLUMN webhook_delivery.repo_node_id IS
    'The repository the delivery was applied to. NULL for rows recorded before migration 0018.';
COMMENT ON COLUMN webhook_delivery.postprocessed_at IS
    'When a complete post-processing pass first covered this delivery. NULL: applied, not yet '
    'post-processed (shown by dg status when repo_node_id is set).';

COMMIT;
