-- 0006_corroboration_checks.sql
-- Behavioural checks for the §5.4 corroboration rubric. Transactional; rolls back.
--
-- The real graph proves the upgrade path (33 edges across 6 threads). It cannot prove
-- the parts that matter most: that a derived edge cannot self-corroborate, that a
-- 2-of-4 thread is refused, and that the tier is REMOVED when evidence is invalidated.

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

CREATE FUNCTION pg_temp.mk_edge(p_src bigint, p_dst bigint, p_type edge_type,
                                p_extractor text)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (p_src, p_dst, p_type, 'explicit', 'explicit', p_extractor) RETURNING id;
$fn$;

-- Builds a thread with a selectable subset of the four categories.
CREATE FUNCTION pg_temp.build_thread(
    p_key text, p_declared boolean, p_structural boolean,
    p_attested boolean, p_published boolean)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE pr bigint; iss bigint; c bigint; person bigint; rel bigint;
BEGIN
    pr  := pg_temp.mk('pull_request', p_key || '-pr', p_key);
    iss := pg_temp.mk('issue',        p_key || '-issue', p_key);
    c   := pg_temp.mk('commit',       p_key || '-commit', p_key);

    IF p_declared THEN
        PERFORM pg_temp.mk_edge(pr, iss, 'closes', 'body_closing_keyword');
    END IF;
    IF p_structural THEN
        PERFORM pg_temp.mk_edge(pr, c, 'implements', 'pr_commit_list');
    END IF;
    IF p_attested THEN
        person := pg_temp.mk('person', p_key || '-person');
        PERFORM pg_temp.mk_edge(person, pr, 'reviewed', 'pr_review');
    END IF;
    IF p_published THEN
        rel := pg_temp.mk('release', p_key || '-rel');
        PERFORM pg_temp.mk_edge(iss, rel, 'deployed_by', 'release_notes_reference');
    END IF;
    RETURN pr;
END;
$fn$;


-- T1  3 of 4 categories -> upgrade
DO $t$
DECLARE pr bigint; n int;
BEGIN
    pr := pg_temp.build_thread('test:t1', true, true, true, false);
    PERFORM apply_corroboration();
    SELECT count(*) INTO n FROM edge e
    JOIN node s ON s.id = e.src_node_id
    WHERE s.thread_key = 'test:t1' AND e.evidence_tier = 'corroborated';
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 three categories upgrade the thread', '>0 corroborated', n > 0,
            'corroborated edges=' || n);
END;
$t$;


-- T2  2 of 4 -> refused
DO $t$
DECLARE pr bigint; n int;
BEGIN
    pr := pg_temp.build_thread('test:t2', true, true, false, false);
    PERFORM apply_corroboration();
    SELECT count(*) INTO n FROM edge e
    JOIN node s ON s.id = e.src_node_id
    WHERE s.thread_key = 'test:t2' AND e.evidence_tier = 'corroborated';
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 two categories are refused', '0 corroborated', n = 0,
            'corroborated edges=' || n);
END;
$t$;


-- T3  Derived edges cannot manufacture a third category.
--     A thread with DECLARED + STRUCTURAL plus a pile of synthesis_* edges must still
--     be refused -- this is the self-corroboration trap the rubric exists to block.
DO $t$
DECLARE pr bigint; dec bigint; iss bigint; n int; cats int;
BEGIN
    pr  := pg_temp.build_thread('test:t3', true, true, false, false);
    SELECT id INTO iss FROM node WHERE thread_key = 'test:t3' AND node_type = 'issue';

    dec := pg_temp.mk('decision', 'test:t3-dec', 'test:t3');
    INSERT INTO decision (node_id, status) VALUES (dec, 'reconstructed');
    PERFORM pg_temp.mk_edge(dec, iss, 'motivated_by',   'synthesis_closes_cluster');
    PERFORM pg_temp.mk_edge(dec, pr,  'implemented_by', 'synthesis_closes_cluster');

    PERFORM apply_corroboration();
    SELECT category_count INTO cats FROM v_thread_corroboration WHERE thread_key = 'test:t3';
    SELECT count(*) INTO n FROM edge e
    JOIN node s ON s.id = e.src_node_id
    WHERE s.thread_key = 'test:t3' AND e.evidence_tier = 'corroborated';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 synthesis edges cannot self-corroborate', '0 corroborated',
            n = 0 AND cats = 2, 'category_count=' || cats || ' corroborated=' || n);
END;
$t$;


-- T4  REVERSIBILITY: invalidate a category, the tier must be withdrawn.
DO $t$
DECLARE pr bigint; before_n int; after_n int; down bigint;
BEGIN
    pr := pg_temp.build_thread('test:t4', true, true, true, false);
    PERFORM apply_corroboration();
    SELECT count(*) INTO before_n FROM edge e JOIN node s ON s.id = e.src_node_id
    WHERE s.thread_key = 'test:t4' AND e.evidence_tier = 'corroborated';

    -- Withdraw ATTESTED by invalidating the review edge (time-versioned, not deleted).
    -- valid_to must exceed valid_from per the 0001 CHECK, and inside one transaction
    -- now() is frozen -- so an edge cannot be invalidated in the same transaction that
    -- created it. Real invalidations always happen in a later run; the interval models
    -- that elapsed time.
    UPDATE edge SET valid_to = now() + interval '1 second'
    WHERE edge_type = 'reviewed' AND dst_node_id = pr;

    SELECT downgraded INTO down FROM apply_corroboration();
    SELECT count(*) INTO after_n FROM edge e JOIN node s ON s.id = e.src_node_id
    WHERE s.thread_key = 'test:t4' AND e.evidence_tier = 'corroborated';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 tier withdrawn when evidence is invalidated', 'before>0, after=0',
            before_n > 0 AND after_n = 0,
            'before=' || before_n || ' after=' || after_n || ' downgraded=' || down);
END;
$t$;


-- T5  Non-evidence-carrying edge types never upgrade, even in a qualifying thread.
DO $t$
DECLARE pr bigint; iss bigint; person bigint; tier evidence_tier;
BEGIN
    pr := pg_temp.build_thread('test:t5', true, true, true, false);
    SELECT id INTO iss FROM node WHERE thread_key = 'test:t5' AND node_type = 'issue';
    person := pg_temp.mk('person', 'test:t5-author');
    UPDATE node SET thread_key = 'test:t5' WHERE id = person;
    PERFORM pg_temp.mk_edge(person, iss, 'created', 'issue_author');

    PERFORM apply_corroboration();
    SELECT e.evidence_tier INTO tier FROM edge e
    WHERE e.src_node_id = person AND e.edge_type = 'created';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T5 authorship edges never corroborate', 'stays explicit',
            tier = 'explicit', 'tier=' || tier);
END;
$t$;


-- T6  Inferred edges are never touched -- the 0001 CHECK ties their tier to their tag.
DO $t$
DECLARE pr bigint; c bigint; tier evidence_tier; ok boolean := true;
BEGIN
    pr := pg_temp.build_thread('test:t6', true, true, true, false);
    c  := pg_temp.mk('commit', 'test:t6-extra', 'test:t6');
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier,
                      extractor, relevance)
    VALUES (pr, c, 'depends_on', 'inferred', 'inferred', 'llm_similarity_v1', 0.9);

    PERFORM apply_corroboration();
    SELECT e.evidence_tier INTO tier FROM edge e
    WHERE e.src_node_id = pr AND e.dst_node_id = c AND e.tag = 'inferred';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T6 inferred edges untouched by backfill', 'stays inferred',
            tier = 'inferred', 'tier=' || tier);
END;
$t$;


-- T7  Idempotence: a second application changes nothing.
DO $t$
DECLARE up bigint; down bigint;
BEGIN
    SELECT upgraded, downgraded INTO up, down FROM apply_corroboration();
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T7 second application is a no-op', '0 up / 0 down',
            up = 0 AND down = 0, 'upgraded=' || up || ' downgraded=' || down);
END;
$t$;


SELECT seq, CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS result, name, expect,
       left(COALESCE(detail, ''), 46) AS detail
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
