-- 0002_rubric_checks.sql
-- Behavioural verification of the §5.1 rubric guard, the inferred-edge gate, and the
-- audit view. Runs in a single transaction and ROLLBACKs — leaves no data behind.
--
-- Technique: the rubric guard is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so
-- it normally fires at COMMIT, which a test cannot catch. Each test therefore inserts
-- with constraints deferred, then issues SET CONSTRAINTS ALL IMMEDIATE inside a plpgsql
-- subtransaction to force the check to fire somewhere catchable.
--
-- Tests use bare `node` rows (no per-type detail rows) because the rubric reads only
-- node_type, thread_key, and edges. The `decision` detail row IS created, since the
-- guard hangs off it.

BEGIN;

CREATE TEMP TABLE test_result (
    seq     int GENERATED ALWAYS AS IDENTITY,
    name    text,
    expect  text,
    passed  boolean,
    detail  text
);

CREATE SEQUENCE pg_temp.fixture_num;

-- Since migration 0008 the rubric requires that something in the thread actually
-- MERGED, so a pull_request fixture now carries a real detail row with merged_at set.
-- Without it every case below would fail on the landing clause and the tests would pass
-- for the wrong reason -- "rejected because nothing merged" instead of "rejected because
-- there is no motivation". Each test still isolates the clause it names.
CREATE FUNCTION pg_temp.mk(p_type node_type, p_ext text, p_thread text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint;
BEGIN
    INSERT INTO node (node_type, external_id, thread_key)
    VALUES (p_type, p_ext, p_thread)
    RETURNING id INTO v_id;

    IF p_type = 'pull_request' THEN
        INSERT INTO pull_request (node_id, number, state, merged_at)
        VALUES (v_id, nextval('pg_temp.fixture_num'), 'closed', now() - interval '1 day');
    END IF;
    RETURN v_id;
END;
$fn$;

CREATE FUNCTION pg_temp.mk_edge(
    p_src bigint, p_dst bigint, p_type edge_type,
    p_tag edge_tag DEFAULT 'explicit', p_rel numeric DEFAULT NULL)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                      extractor, relevance)
    VALUES (p_src, p_dst, p_type, p_tag,
            CASE WHEN p_tag = 'inferred' THEN 'inferred'::evidence_tier
                 ELSE 'explicit'::evidence_tier END,
            'test_fixture', p_rel)
    RETURNING id;
$fn$;

CREATE FUNCTION pg_temp.mk_decision(p_ext text, p_status decision_status DEFAULT 'reconstructed')
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint;
BEGIN
    v_id := pg_temp.mk('decision', p_ext, NULL);
    INSERT INTO decision (node_id, status) VALUES (v_id, p_status);
    RETURN v_id;
END;
$fn$;


-- ===========================================================================
-- T1  PASS: motivated_by + implemented_by, one thread  (rubric clauses 1,2a,3)
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; p bigint; ok boolean; err text; thr text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue',        't1-issue', 'thread:t1');
        p := pg_temp.mk('pull_request', 't1-pr',    'thread:t1');
        d := pg_temp.mk_decision('t1-dec');
        PERFORM pg_temp.mk_edge(d, i, 'motivated_by');
        PERFORM pg_temp.mk_edge(d, p, 'implemented_by');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    SELECT rubric_thread_key INTO thr FROM decision WHERE node_id = d;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 motivation + implementation, same thread', 'accepted',
            ok AND thr = 'thread:t1',
            COALESCE(err, 'rubric_thread_key=' || COALESCE(thr, 'NULL')));
END;
$t$;


-- ===========================================================================
-- T2  REJECT: implementation + validation but NO motivation (clause 1)
--     This is the exact case §5.1 calls out: a merged, reviewed PR with no
--     linked issue describes THAT something landed, not WHY.
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; p bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue',        't2-issue', 'thread:t2');
        p := pg_temp.mk('pull_request', 't2-pr',    'thread:t2');
        d := pg_temp.mk_decision('t2-dec');
        PERFORM pg_temp.mk_edge(d, p, 'implemented_by');
        PERFORM pg_temp.mk_edge(p, i, 'closes');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 no motivation (clause 1)', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T3  REJECT: motivation alone, nothing else (clause 2)
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue', 't3-issue', 'thread:t3');
        -- A merged PR in the thread, but NO implemented_by / reviewed / closes edge:
        -- landing holds, so the refusal below is genuinely about clause 2 having
        -- neither Implementation nor Validation.
        PERFORM pg_temp.mk('pull_request', 't3-pr', 'thread:t3');
        d := pg_temp.mk_decision('t3-dec');
        PERFORM pg_temp.mk_edge(d, i, 'motivated_by');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 motivation alone (clause 2)', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T4  PASS: motivation + VALIDATION only (no implemented_by).
--     Exercises the in-thread interpretation of clause 2b: the `closes` edge
--     runs PR->issue, not off the Decision node.
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; p bigint; ok boolean; err text; v boolean;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue',        't4-issue', 'thread:t4');
        p := pg_temp.mk('pull_request', 't4-pr',    'thread:t4');
        d := pg_temp.mk_decision('t4-dec');
        PERFORM pg_temp.mk_edge(d, i, 'motivated_by');
        PERFORM pg_temp.mk_edge(p, i, 'closes');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    SELECT has_validation INTO v FROM decision_rubric(d);

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 motivation + in-thread validation', 'accepted',
            ok AND v, COALESCE(err, 'has_validation=' || v));
END;
$t$;


-- ===========================================================================
-- T5  REJECT: edges span two threads (clause 3 — the bounded window)
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; p bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue',        't5-issue', 'thread:t5a');
        p := pg_temp.mk('pull_request', 't5-pr',    'thread:t5b');
        d := pg_temp.mk_decision('t5-dec');
        PERFORM pg_temp.mk_edge(d, i, 'motivated_by');
        PERFORM pg_temp.mk_edge(d, p, 'implemented_by');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T5 edges span two threads (clause 3)', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T6  REJECT: motivation target has no thread_key at all (clause 3)
-- ===========================================================================
DO $t$
DECLARE d bigint; i bigint; p bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        i := pg_temp.mk('issue',        't6-issue', NULL);
        p := pg_temp.mk('pull_request', 't6-pr',    'thread:t6');
        d := pg_temp.mk_decision('t6-dec');
        PERFORM pg_temp.mk_edge(d, i, 'motivated_by');
        PERFORM pg_temp.mk_edge(d, p, 'implemented_by');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T6 unthreaded rubric target (clause 3)', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T7  REJECT: inferred edge touching a Decision node (§5.1 exemption)
-- ===========================================================================
DO $t$
DECLARE d bigint; c bigint; ok boolean; err text;
BEGIN
    BEGIN
        SELECT node_id INTO d FROM decision WHERE node_id IN
            (SELECT id FROM node WHERE external_id = 't1-dec');
        c := pg_temp.mk('commit', 't7-commit', 'thread:t7');
        PERFORM pg_temp.mk_edge(c, d, 'relates_to', 'inferred', 0.99);
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T7 inferred edge -> Decision node', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T8  REJECT: inferred edge below the relevance threshold (0.75)
-- ===========================================================================
DO $t$
DECLARE a bigint; b bigint; ok boolean; err text;
BEGIN
    BEGIN
        a := pg_temp.mk('commit', 't8-a');
        b := pg_temp.mk('issue',  't8-b');
        PERFORM pg_temp.mk_edge(a, b, 'relates_to', 'inferred', 0.50);
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T8 inferred edge below relevance threshold', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T9  Per-node cap (4): the first 4 inferred edges land, the 5th is refused
-- ===========================================================================
DO $t$
DECLARE hub bigint; partner bigint; i int; ok boolean; err text; landed int := 0;
BEGIN
    hub := pg_temp.mk('commit', 't9-hub');
    FOR i IN 1..5 LOOP
        BEGIN
            partner := pg_temp.mk('issue', 't9-partner-' || i);
            PERFORM pg_temp.mk_edge(hub, partner, 'relates_to', 'inferred', 0.90);
            landed := landed + 1;
        EXCEPTION WHEN others THEN err := SQLERRM;
        END;
    END LOOP;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T9 per-node inferred cap holds at 4', '4 of 5 land',
            landed = 4, 'landed=' || landed || '; 5th: ' || COALESCE(err, 'no error'));
END;
$t$;


-- ===========================================================================
-- T10 REJECT: stranding a passing Decision by deleting its motivated_by edge.
--     Without the edge-side guard this would silently leave a reconstructed
--     Decision with no evidence of "why".
-- ===========================================================================
DO $t$
DECLARE d bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        SELECT id INTO d FROM node WHERE external_id = 't1-dec';
        DELETE FROM edge WHERE src_node_id = d AND edge_type = 'motivated_by';
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T10 deleting motivated_by strands a Decision', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T11 REJECT: tag/evidence_tier invariant (inference cannot claim a higher tier)
-- ===========================================================================
DO $t$
DECLARE a bigint; b bigint; ok boolean; err text;
BEGIN
    BEGIN
        a := pg_temp.mk('commit', 't11-a');
        b := pg_temp.mk('issue',  't11-b');
        INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                          extractor, relevance)
        VALUES (a, b, 'relates_to', 'inferred', 'explicit', 'test_fixture', 0.99);
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T11 inferred edge claiming explicit tier', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T12 REJECT: explicit Decision with no formal source artifact
-- ===========================================================================
DO $t$
DECLARE d bigint; ok boolean; err text;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    BEGIN
        d := pg_temp.mk('decision', 't12-dec');
        INSERT INTO decision (node_id, status) VALUES (d, 'explicit');
        SET CONSTRAINTS ALL IMMEDIATE;
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T12 explicit Decision without source artifact', 'rejected', NOT ok, err);
END;
$t$;


-- ===========================================================================
-- T13 The standing invariant: no reconstructed Decision fails the rubric
-- ===========================================================================
DO $t$
DECLARE bad int;
BEGIN
    SELECT count(*) INTO bad
    FROM v_decision_rubric_audit
    WHERE status = 'reconstructed' AND NOT passes;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T13 audit view invariant', '0 offending rows', bad = 0, 'offending=' || bad);
END;
$t$;


-- ===========================================================================
SELECT seq,
       CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS result,
       name, expect,
       left(regexp_replace(COALESCE(detail, ''), '\s+', ' ', 'g'), 78) AS detail
FROM test_result
ORDER BY seq;

SELECT count(*) FILTER (WHERE passed)       AS passed,
       count(*) FILTER (WHERE NOT passed)   AS failed,
       count(*)                             AS total
FROM test_result;

ROLLBACK;
