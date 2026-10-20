-- 0017: a delivery log for the webhook receiver (#164) and a request count on each run (#163).
--
-- DELIVERIES. GitHub retries a delivery that failed and lets a maintainer redeliver any past one.
-- The receiver used to process each arrival in full, GitHub round-trips included. This table
-- records which deliveries were applied, so a repeat is acknowledged and skipped.
--
-- THE KEY IS THE DELIVERY ID AND THE BODY'S HASH, NOT THE ID ALONE. `X-GitHub-Delivery` is a
-- header, and the signature covers only the body. Keyed by id alone, someone holding one captured
-- signed delivery could replay its body under the id of a delivery that has not arrived yet, and
-- the real one would then be skipped as "already processed". With the hash in the key, a genuine
-- retry or redelivery (same id, same body) is a duplicate and that replay is not.
--
-- NO PAYLOADS. Ids, a hash, an event name and an outcome sentence only. The row is inserted in the
-- same transaction as the extraction, so "recorded" and "applied" cannot diverge: a delivery that
-- fails half way rolls back its row with everything else, and its retry is applied.
--
-- RUNS. `requests` is how many GitHub API calls a run made, so a scheduled run's cost is visible
-- next to its outcome. NULL for runs before this migration; the count was never recorded for them.

BEGIN;

CREATE TABLE webhook_delivery (
    delivery_id    text        NOT NULL CHECK (length(delivery_id) BETWEEN 1 AND 200),
    payload_sha256 text        NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    event          text        NOT NULL,
    received_at    timestamptz NOT NULL DEFAULT now(),
    outcome        text,
    PRIMARY KEY (delivery_id, payload_sha256)
);

-- Pruning reads by age.
CREATE INDEX webhook_delivery_received_idx ON webhook_delivery (received_at);

COMMENT ON TABLE webhook_delivery IS
    'Webhook deliveries already applied, keyed by X-GitHub-Delivery and the SHA-256 of the body '
    '(the signature covers the body, not the header). Holds no payloads. Written in the same '
    'transaction as the extraction. Pruned by age; polling, not this table, is the completeness '
    'guarantee.';

ALTER TABLE ingestion_run ADD COLUMN requests integer;

COMMENT ON COLUMN ingestion_run.requests IS
    'GitHub API requests this run made. NULL for runs recorded before migration 0017.';

COMMIT;
