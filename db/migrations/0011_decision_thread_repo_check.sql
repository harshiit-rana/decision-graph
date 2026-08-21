-- 0011: assert that a Decision's thread_key names its own repo_node_id (issue #28).
--
-- Numbered assuming 0010_closing_keyword_provenance.sql (#12) merges first. If it lands
-- after this one instead, renumber whichever file lands second to 0011/0012 in merge
-- order -- the migration runner keys off the numeric prefix, and a gap or repeat is
-- what `dg doctor` would actually catch, not a build failure.
--
-- Every query in this codebase that promotes or looks up a Decision joins on BOTH
-- repo_node_id and thread_key, trusting that the two always agree. Nothing asserted
-- that. `synthesis.CANDIDATE_QUERY` selected `closes` edges with no repo predicate, so
-- running synthesis for repo B could re-promote repo A's clusters stamped with repo B's
-- repo_node_id -- a Decision whose thread_key names one repo and whose repo_node_id
-- names another, silently. The synthesis queries are fixed (this repo's other #28 PR),
-- but per the issue: "the guard, not the caller, is the authority". This is the guard.
--
-- Scoped to node_type = 'decision' only -- that is the identity the rest of the schema
-- (decision_thread_uidx, _promote's lookup-by-thread_key) actually depends on agreeing.
-- Other thread_key-bearing node types (issue, pull_request, commit) are not touched by
-- this issue and are left alone.
--
-- This is not hypothetical. Applying this migration against the actual dev database
-- failed the CHECK immediately: it holds two repos (pallets/flask, id 1; a second repo
-- ingested later, id 1662), and 13 Decisions existed with repo_node_id = 1662 but
-- thread_key = 'thread:1:...' -- the exact corruption the issue describes, produced by
-- running synthesis for the second repo before this fix landed. Every one of the 13 had
-- a correctly-scoped twin already at repo_node_id = 1 (same thread_key, same
-- external_id) -- true duplicates, not real data about the second repo -- so they are
-- deleted, not re-keyed; re-keying would have collided with the twin on
-- node_identity_uidx anyway. A survivor with no correctly-scoped twin (not observed
-- here, but not excluded by anything) is re-keyed to the repo its own thread_key names,
-- so real data is repaired rather than discarded.

BEGIN;

-- 1. Repair: drop corrupt duplicates that already have a correctly-scoped twin.
DELETE FROM node bad
USING node good
WHERE bad.node_type = 'decision'
  AND bad.thread_key IS NOT NULL
  AND bad.repo_node_id IS NOT NULL
  AND bad.thread_key NOT LIKE 'thread:' || bad.repo_node_id::text || ':%'
  AND good.node_type = 'decision'
  AND good.thread_key = bad.thread_key
  AND good.repo_node_id::text = split_part(bad.thread_key, ':', 2)
  AND good.id <> bad.id;

-- 2. Repair: re-key any remaining violator (no twin existed) to the repo its
--    thread_key actually names, rather than losing real data.
UPDATE node
SET repo_node_id = split_part(thread_key, ':', 2)::bigint
WHERE node_type = 'decision'
  AND thread_key IS NOT NULL
  AND repo_node_id IS NOT NULL
  AND thread_key NOT LIKE 'thread:' || repo_node_id::text || ':%';

-- 3. Enforce it. The guard, not the calling code, is the authority.
ALTER TABLE node
    ADD CONSTRAINT decision_thread_key_matches_repo
        CHECK (
            node_type <> 'decision'
            OR repo_node_id IS NULL
            OR thread_key IS NULL
            OR thread_key LIKE 'thread:' || repo_node_id::text || ':%'
        );

COMMENT ON CONSTRAINT decision_thread_key_matches_repo ON node IS
    'A Decision''s thread_key must name the same repo as its repo_node_id column. '
    'Catches a repo-unscoped synthesis query re-promoting another repo''s cluster '
    'under this repo''s id (issue #28) at write time, rather than leaving the drift '
    'silent until something notices the two fields disagree.';

COMMIT;
