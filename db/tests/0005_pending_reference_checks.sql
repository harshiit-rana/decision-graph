-- 0005_pending_reference_checks.sql
-- Constraint behaviour for the deferred-reference queue (issue #3).
-- Transactional; rolls back. Companion to 0002_rubric_checks.sql.

BEGIN;

CREATE TEMP TABLE test_result (
    seq    int GENERATED ALWAYS AS IDENTITY,
    name   text,
    expect text,
    passed boolean,
    detail text
);

CREATE FUNCTION pg_temp.mk(p_type node_type, p_ext text, p_repo bigint DEFAULT NULL)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO node (node_type, external_id, repo_node_id)
    VALUES (p_type, p_ext, p_repo) RETURNING id;
$fn$;

CREATE FUNCTION pg_temp.enqueue(p_repo bigint, p_src bigint, p_ref int, p_type edge_type)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO pending_reference (repo_node_id, src_node_id, ref_number, edge_type, extractor)
    VALUES (p_repo, p_src, p_ref, p_type, 'test_fixture')
    RETURNING id;
$fn$;


-- T1  One open row per (src, ref, edge_type): re-parsing on a later poll must not
--     enqueue a duplicate.
DO $t$
DECLARE repo bigint; pr bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't1-repo');
    pr   := pg_temp.mk('pull_request', 't1-pr', repo);
    PERFORM pg_temp.enqueue(repo, pr, 999, 'closes');
    BEGIN
        PERFORM pg_temp.enqueue(repo, pr, 999, 'closes');
        ok := true;
    EXCEPTION WHEN unique_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 duplicate open reference refused', 'rejected', NOT ok, err);
END;
$t$;


-- T2  The uniqueness is PARTIAL. Once resolved, the same triple may be queued again --
--     an edge can legitimately be invalidated and re-derived on a later poll.
DO $t$
DECLARE repo bigint; pr bigint; iss bigint; e bigint; p bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't2-repo');
    pr   := pg_temp.mk('pull_request', 't2-pr', repo);
    iss  := pg_temp.mk('issue', 't2-issue', repo);
    p    := pg_temp.enqueue(repo, pr, 42, 'closes');

    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (pr, iss, 'closes', 'explicit', 'explicit', 'test_fixture') RETURNING id INTO e;

    UPDATE pending_reference SET resolved_at = now(), resolved_edge_id = e WHERE id = p;

    BEGIN
        PERFORM pg_temp.enqueue(repo, pr, 42, 'closes');
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 re-queue allowed after resolution', 'accepted', ok, err);
END;
$t$;


-- T3  A row cannot claim resolution without naming the edge that resolved it.
DO $t$
DECLARE repo bigint; pr bigint; p bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't3-repo');
    pr   := pg_temp.mk('pull_request', 't3-pr', repo);
    p    := pg_temp.enqueue(repo, pr, 7, 'references');
    BEGIN
        UPDATE pending_reference SET resolved_at = now() WHERE id = p;
        ok := true;
    EXCEPTION WHEN check_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 resolution without edge id refused', 'rejected', NOT ok, err);
END;
$t$;


-- T4  The status view separates "drain has not run" from "target outside window" --
--     the distinction that made issue #3 diagnosable in the first place.
DO $t$
DECLARE repo bigint; pr bigint; iss bigint; v_now int; v_out int;
BEGIN
    repo := pg_temp.mk('repository', 't4-repo');
    pr   := pg_temp.mk('pull_request', 't4-pr', repo);
    iss  := pg_temp.mk('issue', '4242', repo);      -- in-window target
    PERFORM pg_temp.enqueue(repo, pr, 4242, 'closes');   -- resolvable
    PERFORM pg_temp.enqueue(repo, pr, 1111, 'closes');   -- outside window

    SELECT resolvable_now, target_outside_window INTO v_now, v_out
    FROM v_pending_reference_status WHERE repo_node_id = repo AND edge_type = 'closes';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 view separates resolvable from out-of-window', '1 and 1',
            v_now = 1 AND v_out = 1,
            format('resolvable_now=%s target_outside_window=%s', v_now, v_out));
END;
$t$;


SELECT seq, CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS result, name, expect,
       left(regexp_replace(COALESCE(detail, ''), '\s+', ' ', 'g'), 60) AS detail
FROM test_result ORDER BY seq;

SELECT count(*) FILTER (WHERE passed) AS passed,
       count(*) FILTER (WHERE NOT passed) AS failed, count(*) AS total
FROM test_result;

-- A failed check must fail the RUN, not merely appear in its output. CI invokes psql with
-- ON_ERROR_STOP=1, which promotes a SQL *error* to a non-zero exit; a 'FAIL' row in the
-- table above is not an error and left the job green (issue #62). The table is printed
-- first and deliberately kept -- it is what makes the failure diagnosable -- and the raise
-- comes after it, so the diagnosis survives the abort.
DO $fail$
DECLARE v_failed int; v_total int;
BEGIN
    SELECT count(*) FILTER (WHERE NOT passed), count(*) INTO v_failed, v_total
    FROM test_result;
    IF v_failed > 0 THEN
        RAISE EXCEPTION '% of % check(s) failed', v_failed, v_total;
    END IF;
    RAISE NOTICE 'all % checks passed', v_total;
END
$fail$;

ROLLBACK;
