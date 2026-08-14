-- 0009: one Decision per thread cluster, enforced (issue #25).
--
-- A Decision's identity IS its thread cluster: synthesis writes external_id = thread_key.
-- But thread_key is not stable -- union-find rewrites it whenever two clusters merge, and
-- `threads.union` updates node.thread_key without touching node.external_id. A Decision
-- created before a merge therefore keeps a frozen external_id naming a cluster that no
-- longer exists, synthesis looks the cluster up by the NEW key, finds nothing, and creates
-- a second Decision node for the same decision.
--
-- Both duplicates pass the §5.1 rubric individually -- each has its own motivated_by and
-- implemented_by edges -- so neither the rubric guard nor edge_current_uidx can see the
-- problem. The violated invariant is one nothing was asserting: at most one Decision per
-- cluster.
--
-- Found by completing the 12-month backfill. Ingesting the remaining third of the window
-- linked issue 6072's thread into the cluster that already held Decision 937
-- (external_id thread:30:pr-6095), and the next synthesis run duplicated it as node 2230.
-- No amount of re-running against the partial corpus would have surfaced it.

BEGIN;

-- 1. Repair. Keep the OLDEST Decision per cluster: it is the node existing edges,
--    evaluation traces and any exported adjudication already reference. Newer duplicates
--    are deleted outright rather than time-versioned -- they are not superseded claims
--    about the world, they are the same claim written twice, and keeping them would leave
--    the traversal engine free to return both.
WITH ranked AS (
    SELECT n.id,
           row_number() OVER (PARTITION BY n.repo_node_id, n.thread_key ORDER BY n.id) AS rn
    FROM node n
    JOIN decision d ON d.node_id = n.id
    WHERE n.thread_key IS NOT NULL
)
DELETE FROM node WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- 2. Re-sync the survivors. external_id is a snapshot of thread_key at creation; where a
--    merge has moved the cluster on, the snapshot is stale and must follow.
UPDATE node n
SET external_id = n.thread_key
FROM decision d
WHERE d.node_id = n.id
  AND n.thread_key IS NOT NULL
  AND n.external_id IS DISTINCT FROM n.thread_key;

-- 3. Enforce it. The guard, not the calling code, is the authority -- synthesis is fixed
--    in the same change, but a backfill, a manual repair or a future caller must not be
--    able to reintroduce this.
CREATE UNIQUE INDEX decision_thread_uidx
    ON node (COALESCE(repo_node_id, 0), thread_key)
    WHERE node_type = 'decision' AND thread_key IS NOT NULL;

COMMIT;
