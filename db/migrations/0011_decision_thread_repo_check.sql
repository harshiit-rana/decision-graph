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

BEGIN;

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
