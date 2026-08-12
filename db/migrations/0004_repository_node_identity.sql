-- 0004_repository_node_identity.sql
-- Repair + invariant for repository node identity.
--
-- Found by running ingestion twice against pallets/flask. The repository extractor
-- wrote the node's own id back into its repo_node_id as a self-reference. Because
-- repo_node_id participates in node_identity_uidx via COALESCE(repo_node_id, 0),
-- that write moved the row's identity key from (repository, 0, 'pallets/flask') to
-- (repository, 30, 'pallets/flask'). The next run looked up the former, missed,
-- and inserted a SECOND repository node — caught only incidentally by the
-- repository(owner, name) unique constraint, one layer downstream.
--
-- 0001 already documented repo_node_id as "NULL only for repository nodes
-- themselves"; nothing enforced it. Since identity depends on this column, a
-- comment is not a strong enough guarantee: any writer that sets it re-keys the
-- row and silently breaks upsert idempotency for every future run.

BEGIN;

-- Repair rows written by the pre-fix extractor.
UPDATE node
SET repo_node_id = NULL
WHERE node_type = 'repository'
  AND repo_node_id = id;

ALTER TABLE node
    ADD CONSTRAINT node_repository_owns_itself
        CHECK (node_type <> 'repository' OR repo_node_id IS NULL);

COMMENT ON CONSTRAINT node_repository_owns_itself ON node IS
    'A repository node must not point at a repository, including itself. repo_node_id '
    'is part of the node identity key, so self-reference re-keys the row and breaks '
    'upsert idempotency across runs.';

COMMIT;
