-- Behavioural checks for 0009: one Decision per thread cluster (issue #25).
--
-- Runs in a transaction and rolls back.
--
-- Thread keys are built from v_repo rather than written out. They used to hard-code
-- `thread:1:`, which was harmless until 0011 added
--
--     CHECK (... thread_key LIKE 'thread:' || repo_node_id::text || ':%')
--
-- and the fixture became the first thing that constraint rejected. It aborted at T1, so
-- every check below it stopped running and stayed stopped (#55). The keys are derived now
-- because a fixture that states the repo id twice can disagree with itself; one that
-- derives it cannot.

BEGIN;
SET CONSTRAINTS ALL DEFERRED;

DO $$
DECLARE
    v_repo   bigint;
    v_a      bigint;
    v_b      bigint;
    v_k1     text;
    v_k2     text;
    v_k3     text;
    v_failed int := 0;
    v_ok     boolean;
BEGIN
    INSERT INTO node (node_type, external_id, title)
    VALUES ('repository', 'test/identity', 'fixture repo') RETURNING id INTO v_repo;

    v_k1 := format('thread:%s:pr-1', v_repo);
    v_k2 := format('thread:%s:pr-2', v_repo);
    v_k3 := format('thread:%s:pr-3', v_repo);

    -- T1: two Decisions in DIFFERENT clusters are fine.
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('decision', v_repo, v_k1, v_k1) RETURNING id INTO v_a;
    INSERT INTO decision (node_id, status) VALUES (v_a, 'reconstructed');

    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('decision', v_repo, v_k2, v_k2) RETURNING id INTO v_b;
    INSERT INTO decision (node_id, status) VALUES (v_b, 'reconstructed');
    RAISE NOTICE 'T1 PASS: distinct clusters may each hold a Decision';

    -- T2: a second Decision in the SAME cluster is rejected. This is the shape the
    -- 6072/6095 duplicate took -- different external_id, same thread_key.
    BEGIN
        INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
        VALUES ('decision', v_repo, format('thread:%s:pr-99', v_repo), v_k1);
        v_failed := v_failed + 1;
        RAISE WARNING 'T2 FAIL: a duplicate Decision for one cluster was accepted';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'T2 PASS: one Decision per cluster is enforced';
    END;

    -- T3: the guard is scoped to Decisions. Ordinary artifacts share a thread_key by
    -- design -- that is what a thread IS -- so the index must not touch them.
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('pull_request', v_repo, '1', v_k1);
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('issue', v_repo, '2', v_k1);
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
    -- re-keyable in place, not blocked by the index it now conflicts with. This is what
    -- 0009's own repair step does, and it is the operation the partial index is most
    -- likely to obstruct by accident.
    --
    -- The UPDATE used to end `AND FALSE  -- no-op guard`, so the rename this check is
    -- named for never happened. What ran was the global duplicate assertion that follows
    -- it (now T9) -- a real check, but one T2 already makes, and not the one described.
    -- Renaming into a FREE cluster is the case that must be allowed; renaming into an
    -- OCCUPIED one is T6.
    UPDATE node SET thread_key = v_k3 WHERE id = v_a;
    SELECT EXISTS (SELECT 1 FROM node WHERE id = v_a AND thread_key = v_k3) INTO v_ok;
    IF NOT v_ok THEN
        v_failed := v_failed + 1;
        RAISE WARNING 'T5 FAIL: re-keying a Decision into a free cluster did not take';
    ELSE
        RAISE NOTICE 'T5 PASS: a Decision can be re-keyed into a free cluster';
    END IF;

    -- T6: the same rename into an OCCUPIED cluster must still be refused. Without this,
    -- T5 alone would pass just as happily against an index that had been dropped.
    BEGIN
        UPDATE node SET thread_key = v_k2 WHERE id = v_a;
        v_failed := v_failed + 1;
        RAISE WARNING 'T6 FAIL: a Decision was re-keyed onto a cluster that already had one';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'T6 PASS: re-keying onto an occupied cluster is refused';
    END;

    -- T7: 0011's constraint -- a Decision's thread_key must name its own repo. It has no
    -- suite of its own, and it is what silently killed this file from the day it landed
    -- (#55), so it is checked here: in the fixture it broke.
    BEGIN
        INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
        VALUES ('decision', v_repo, 'wrong-repo', 'thread:999999:pr-1');
        v_failed := v_failed + 1;
        RAISE WARNING 'T7 FAIL: a Decision keyed to another repo was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'T7 PASS: a Decision keyed to another repo is refused';
    END;

    -- T8: and that guard is scoped to Decisions. An ordinary artifact may legitimately
    -- carry a thread_key naming a different repo id while a merge is in flight; only the
    -- Decision -- the thing synthesis writes per repo -- is pinned.
    INSERT INTO node (node_type, repo_node_id, external_id, thread_key)
    VALUES ('pull_request', v_repo, '3', 'thread:999999:pr-1');
    RAISE NOTICE 'T8 PASS: the repo-match guard does not touch non-Decision nodes';

    -- T9: the invariant the whole file is about, asserted over the fixture and everything
    -- else in the database.
    SELECT NOT EXISTS (
        SELECT 1 FROM node n JOIN decision d ON d.node_id = n.id
        WHERE n.thread_key IS NOT NULL
        GROUP BY n.repo_node_id, n.thread_key HAVING count(*) > 1
    ) INTO v_ok;
    IF v_ok THEN
        RAISE NOTICE 'T9 PASS: no cluster holds more than one Decision';
    ELSE
        v_failed := v_failed + 1;
        RAISE WARNING 'T9 FAIL: a cluster holds multiple Decisions';
    END IF;

    IF v_failed > 0 THEN
        RAISE EXCEPTION '% check(s) failed', v_failed;
    END IF;
    RAISE NOTICE 'all 0009 checks passed';
END $$;

ROLLBACK;
