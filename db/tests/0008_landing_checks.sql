-- 0008_landing_checks.sql
-- Regression checks for the landing gate (issues #16/#17). Transactional; rolls back.
--
-- The defect these pin: a closing keyword is written by a contributor BEFORE the outcome
-- is known and survives rejection intact, so `closes` alone never showed that anything
-- landed. 12 of 20 Decisions rested on pull requests that were never merged.

BEGIN;

CREATE TEMP TABLE test_result (
    seq int GENERATED ALWAYS AS IDENTITY,
    name text, expect text, passed boolean, detail text
);

CREATE FUNCTION pg_temp.mk(p_type node_type, p_ext text, p_thread text DEFAULT NULL)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO node (node_type, external_id, thread_key)
    VALUES (p_type, p_ext, p_thread) RETURNING id;
$fn$;

CREATE FUNCTION pg_temp.mk_edge(p_src bigint, p_dst bigint, p_type edge_type)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (p_src, p_dst, p_type, 'explicit', 'explicit', 'test_fixture') RETURNING id;
$fn$;

-- A cluster: issue + PR that closes it, with selectable merge state.
CREATE FUNCTION pg_temp.build(p_key text, p_merged boolean)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE pr bigint; iss bigint; dec bigint;
BEGIN
    pr  := pg_temp.mk('pull_request', p_key || '-pr', p_key);
    iss := pg_temp.mk('issue', p_key || '-issue', p_key);
    INSERT INTO pull_request (node_id, number, state, merged_at)
    VALUES (pr, 1, 'closed', CASE WHEN p_merged THEN now() - interval '1 day' END);
    INSERT INTO issue (node_id, number, state) VALUES (iss, 2, 'closed');

    PERFORM pg_temp.mk_edge(pr, iss, 'closes');

    dec := pg_temp.mk('decision', p_key || '-dec', p_key);
    INSERT INTO decision (node_id, status) VALUES (dec, 'reconstructed');
    PERFORM pg_temp.mk_edge(dec, iss, 'motivated_by');
    PERFORM pg_temp.mk_edge(dec, pr, 'implemented_by');
    RETURN dec;
END;
$fn$;


-- T1  A cluster whose PR was NEVER MERGED must be refused, however many closing
--     keywords point at the issue. This is the exact 5912 case.
DO $t$
DECLARE dec bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        dec := pg_temp.build('test:unmerged', false);
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 unmerged cluster refused', 'rejected', NOT ok, left(COALESCE(err,''), 70));
END;
$t$;


-- T2  The same cluster WITH a merge is accepted -- the gate must not reject everything.
DO $t$
DECLARE dec bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        dec := pg_temp.build('test:merged', true);
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 merged cluster accepted', 'accepted', ok, left(COALESCE(err,''), 70));
END;
$t$;


-- T3  thread_landed() reads merge state, not closing keywords.
DO $t$
DECLARE a boolean; b boolean;
BEGIN
    a := thread_landed('test:merged');
    b := thread_landed('test:unmerged');
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 thread_landed tracks merges only', 'true / false',
            a AND NOT b, format('merged=%s unmerged=%s', a, b));
END;
$t$;


-- T4  RETRACTION: a Decision standing under the old rule is withdrawn once its merge
--     is taken away. Nothing else would revisit it -- the deferred guard only
--     re-validates rows something touches.
DO $t$
DECLARE dec bigint; before_n int; retracted bigint; after_n int;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    dec := pg_temp.build('test:retract', true);
    SET CONSTRAINTS ALL IMMEDIATE;
    SELECT count(*) INTO before_n FROM decision WHERE node_id = dec;

    -- Evidence withdrawn: the merge is revoked.
    UPDATE pull_request SET merged_at = NULL
    WHERE node_id IN (SELECT id FROM node WHERE thread_key='test:retract'
                        AND node_type='pull_request');

    SELECT r.retracted INTO retracted FROM retract_unsupported_decisions() r;
    SELECT count(*) INTO after_n FROM decision WHERE node_id = dec;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 Decision retracted when merge withdrawn', 'before 1, after 0',
            before_n = 1 AND after_n = 0,
            format('before=%s retracted=%s after=%s', before_n, retracted, after_n));
END;
$t$;


-- T5  A Decision whose evidence still holds is NOT retracted -- retraction must be
--     targeted, not a blanket purge.
DO $t$
DECLARE n int;
BEGIN
    PERFORM retract_unsupported_decisions();
    SELECT count(*) INTO n FROM node WHERE thread_key='test:merged' AND node_type='decision';
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T5 supported Decision survives retraction', 'still 1', n = 1, 'decisions=' || n);
END;
$t$;


-- T6  The failure message names the landing clause, so an operator can tell "nothing
--     merged" apart from "no motivation".
DO $t$
DECLARE reason text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    PERFORM pg_temp.build('test:reason', false);
    SELECT failure_reason INTO reason FROM v_decision_rubric_audit
    WHERE cluster_thread_key = 'test:reason';
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T6 failure reason cites the merge', 'mentions merged',
            reason ILIKE '%merged%', left(COALESCE(reason, '<none>'), 66));
END;
$t$;


SELECT seq, CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS result, name, expect, detail
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
