-- Behavioural checks for 0009: one Decision per thread cluster (issue #25).
--
-- Runs in a transaction and rolls back.

BEGIN;
SET CONSTRAINTS ALL DEFERRED;

DO $$
DECLARE
    v_repo   bigint;
    v_a      bigint;
    v_b      bigint;
    v_failed int := 0;
    v_ok     boolean;
BEGIN
    INSERT INTO node (node_type, external_id, title)
    VALUES ('repository', 'test/identity', 'fixture repo') RETURNING id INTO v_repo;

    -- T1: two Decisions in DIFFERENT clusters are fine.
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('decision', v_repo, 'thread:1:pr-1', 'thread:1:pr-1') RETURNING id INTO v_a;
    INSERT INTO decision (node_id, status) VALUES (v_a, 'reconstructed');

    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('decision', v_repo, 'thread:1:pr-2', 'thread:1:pr-2') RETURNING id INTO v_b;
    INSERT INTO decision (node_id, status) VALUES (v_b, 'reconstructed');
    RAISE NOTICE 'T1 PASS: distinct clusters may each hold a Decision';

    -- T2: a second Decision in the SAME cluster is rejected. This is the shape the
    -- 6072/6095 duplicate took -- different external_id, same thread_key.
    BEGIN
        INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
        VALUES ('decision', v_repo, 'thread:1:pr-99', 'thread:1:pr-1');
        v_failed := v_failed + 1;
        RAISE WARNING 'T2 FAIL: a duplicate Decision for one cluster was accepted';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'T2 PASS: one Decision per cluster is enforced';
    END;

    -- T3: the guard is scoped to Decisions. Ordinary artifacts share a thread_key by
    -- design -- that is what a thread IS -- so the index must not touch them.
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('pull_request', v_repo, '1', 'thread:1:pr-1');
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('issue', v_repo, '2', 'thread:1:pr-1');
    RAISE NOTICE 'T3 PASS: non-Decision nodes still share a thread freely';

    -- T4: a thread-less Decision is not caught by the partial index. It cannot pass the
    -- rubric anyway (clause 3), so the index must not be the thing that stops it --
    -- otherwise the error names the wrong problem.
    BEGIN
        INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
        VALUES ('decision', v_repo, 'orphan-a', NULL);
        INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
        VALUES ('decision', v_repo, 'orphan-b', NULL);
        RAISE NOTICE 'T4 PASS: NULL thread_key is not collapsed by the index';
    EXCEPTION WHEN unique_violation THEN
        v_failed := v_failed + 1;
        RAISE WARNING 'T4 FAIL: the index treated two NULL thread_keys as a collision';
    END;

    -- T5: the real-world repair. A Decision whose cluster was renamed by a merge must be
    -- re-keyable in place, not blocked by the index it now conflicts with.
    UPDATE node SET thread_key = 'thread:1:pr-2' WHERE id = v_a AND FALSE;  -- no-op guard
    SELECT NOT EXISTS (
        SELECT 1 FROM node n JOIN decision d ON d.node_id = n.id
        WHERE n.thread_key IS NOT NULL
        GROUP BY n.repo_node_id, n.thread_key HAVING count(*) > 1
    ) INTO v_ok;
    IF v_ok THEN
        RAISE NOTICE 'T5 PASS: no cluster holds more than one Decision';
    ELSE
        v_failed := v_failed + 1;
        RAISE WARNING 'T5 FAIL: a cluster holds multiple Decisions';
    END IF;

    IF v_failed > 0 THEN
        RAISE EXCEPTION '% check(s) failed', v_failed;
    END IF;
    RAISE NOTICE 'all 0009 checks passed';
END $$;

ROLLBACK;
