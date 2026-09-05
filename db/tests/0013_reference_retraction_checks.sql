-- 0013_reference_retraction_checks.sql
-- Constraint and view behaviour for withdrawn queue rows (issue #61).
-- Transactional; rolls back. Companion to 0005_pending_reference_checks.sql.
--
-- These check the SCHEMA half of retraction: that a withdrawn row cannot also claim
-- resolution, that it stops counting as open, and -- the load-bearing one -- that the same
-- reference can be queued again afterwards, which is what makes a mistaken retraction
-- recoverable rather than permanent. The half that decides WHICH rows to withdraw is
-- Python, and is tested in tests/test_reference_retraction.py against the real parser.

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

CREATE FUNCTION pg_temp.retract(p_id bigint) RETURNS void LANGUAGE sql AS $fn$
    UPDATE pending_reference
    SET retracted_at = now(), retraction_reason = 'test fixture'
    WHERE id = p_id;
$fn$;


-- T1  A retraction must say why. The reason is what makes a withdrawal auditable instead
--     of merely observable, so the schema refuses one without it.
DO $t$
DECLARE repo bigint; pr bigint; p bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't1-repo');
    pr   := pg_temp.mk('pull_request', 't1-pr', repo);
    p    := pg_temp.enqueue(repo, pr, 11, 'closes');
    BEGIN
        UPDATE pending_reference SET retracted_at = now() WHERE id = p;
        ok := true;
    EXCEPTION WHEN check_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 retraction without a reason refused', 'rejected', NOT ok, err);
END;
$t$;


-- T2  Resolved and retracted are mutually exclusive. Resolution created an edge;
--     retraction says the reference should never have existed. Both at once would leave
--     the edge standing while the queue disowned its source.
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
        PERFORM pg_temp.retract(p);
        ok := true;
    EXCEPTION WHEN check_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 retracting a resolved row refused', 'rejected', NOT ok, err);
END;
$t$;


-- T3  The same reference may be queued again after retraction. This is the property that
--     makes a wrong retraction recoverable: fix the parser, re-run --reconcile, and the
--     row comes back. Without it the retracted row would sit in the open-row unique index
--     forever and swallow the re-insert, which ON CONFLICT DO NOTHING would report as
--     success.
DO $t$
DECLARE repo bigint; pr bigint; p bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't3-repo');
    pr   := pg_temp.mk('pull_request', 't3-pr', repo);
    p    := pg_temp.enqueue(repo, pr, 77, 'closes');
    PERFORM pg_temp.retract(p);
    BEGIN
        PERFORM pg_temp.enqueue(repo, pr, 77, 'closes');
        ok := true;
    EXCEPTION WHEN others THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 re-queue allowed after retraction', 'accepted', ok, err);
END;
$t$;


-- T4  Uniqueness still holds among rows that are actually open. Retraction widens the
--     index predicate, and a widening that also let duplicates in would be a regression of
--     0005's T1 dressed as a feature.
DO $t$
DECLARE repo bigint; pr bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 't4-repo');
    pr   := pg_temp.mk('pull_request', 't4-pr', repo);
    PERFORM pg_temp.enqueue(repo, pr, 88, 'closes');
    BEGIN
        PERFORM pg_temp.enqueue(repo, pr, 88, 'closes');
        ok := true;
    EXCEPTION WHEN unique_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 duplicate open reference still refused', 'rejected', NOT ok, err);
END;
$t$;


-- T5  A retracted row stops counting as open but does not disappear. Both halves matter:
--     counting it as waiting would overstate the queue, and dropping it from the view
--     would undo the reason retraction is a column rather than a DELETE.
DO $t$
DECLARE repo bigint; pr bigint; p bigint; v_open int; v_retracted int;
BEGIN
    repo := pg_temp.mk('repository', 't5-repo');
    pr   := pg_temp.mk('pull_request', 't5-pr', repo);
    PERFORM pg_temp.enqueue(repo, pr, 101, 'closes');   -- stays open
    p := pg_temp.enqueue(repo, pr, 102, 'closes');      -- withdrawn
    PERFORM pg_temp.retract(p);

    SELECT open_refs, retracted INTO v_open, v_retracted
    FROM v_pending_reference_status WHERE repo_node_id = repo AND edge_type = 'closes';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T5 view separates open from retracted', '1 and 1',
            v_open = 1 AND v_retracted = 1,
            format('open_refs=%s retracted=%s', v_open, v_retracted));
END;
$t$;


-- T6  A retracted row whose target IS in the graph must not be reported as resolvable.
--     `resolvable_now` reads as "the drain has not run yet" (0005 T4), and a withdrawn row
--     that the drain will never touch again must not masquerade as work outstanding.
DO $t$
DECLARE repo bigint; pr bigint; p bigint; v_now int;
BEGIN
    repo := pg_temp.mk('repository', 't6-repo');
    pr   := pg_temp.mk('pull_request', 't6-pr', repo);
    PERFORM pg_temp.mk('issue', '4242', repo);          -- the target is present
    p := pg_temp.enqueue(repo, pr, 4242, 'closes');
    PERFORM pg_temp.retract(p);

    SELECT resolvable_now INTO v_now
    FROM v_pending_reference_status WHERE repo_node_id = repo AND edge_type = 'closes';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T6 retracted row is not resolvable_now', '0',
            v_now = 0, format('resolvable_now=%s', v_now));
END;
$t$;


SELECT seq, CASE WHEN passed THEN 'PASS' ELSE 'FAIL' END AS result, name, expect,
       left(regexp_replace(COALESCE(detail, ''), '\s+', ' ', 'g'), 60) AS detail
FROM test_result ORDER BY seq;

SELECT count(*) FILTER (WHERE passed) AS passed,
       count(*) FILTER (WHERE NOT passed) AS failed, count(*) AS total
FROM test_result;

-- A failed check must fail the RUN, not merely appear in its output (issue #62).
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
