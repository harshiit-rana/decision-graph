-- 0007_landed_state.sql
-- Persist the two fields that tell us whether work actually LANDED (issue #16).
--
-- `pull_request.merged_at` has existed since 0001 and was never once populated:
-- extract_issue_or_pr read `payload.get("merged_at")`, but the /issues endpoint nests
-- merge state under payload["pull_request"]["merged_at"]. Nothing read the column, so
-- nothing failed — the schema looked complete while 27 of 145 merges were discarded.
--
-- `issue.state_reason` was never modelled at all. It distinguishes an issue closed as
-- `completed` from one closed as `not_planned` — i.e. resolved versus declined. 28 of
-- 54 closed issues in this window are `not_planned`.
--
-- Both are backfilled from the `raw` JSONB we already store, so this costs no API calls
-- and does not widen the bounded backfill window.

BEGIN;

ALTER TABLE issue ADD COLUMN state_reason text;

COMMENT ON COLUMN issue.state_reason IS
    'GitHub state_reason: completed | not_planned | reopened | null. An issue closed as '
    'not_planned records a decision NOT to act -- see the §5.1 rubric, which requires '
    'evidence that work landed before asserting a Decision.';

-- Backfill merge state from the payloads already in the graph.
UPDATE pull_request p
SET merged_at = (n.raw->'pull_request'->>'merged_at')::timestamptz
FROM node n
WHERE n.id = p.node_id
  AND n.raw->'pull_request'->>'merged_at' IS NOT NULL;

UPDATE issue i
SET state_reason = n.raw->>'state_reason'
FROM node n
WHERE n.id = i.node_id
  AND n.raw->>'state_reason' IS NOT NULL;

-- "Did the work in this thread actually land?" -- the question the §5.1 rubric was
-- always asking under the name Validation, but never actually checked.
CREATE FUNCTION thread_landed(p_thread_key text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT p_thread_key IS NOT NULL AND EXISTS (
        SELECT 1
        FROM node n
        JOIN pull_request p ON p.node_id = n.id
        WHERE n.thread_key = p_thread_key
          AND p.merged_at IS NOT NULL
    );
$$;

COMMENT ON FUNCTION thread_landed(text) IS
    'True when a thread cluster contains at least one MERGED pull request. A closing '
    'keyword is written by a contributor before the outcome is known and survives '
    'rejection intact; a merge does not.';

CREATE INDEX pull_request_merged_idx ON pull_request (merged_at) WHERE merged_at IS NOT NULL;

COMMIT;
