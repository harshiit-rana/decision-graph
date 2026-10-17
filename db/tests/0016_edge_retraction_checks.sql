-- 0016_edge_retraction_checks.sql
-- The audit guarantees of ADR 0009 / migration 0016, as SQL. Transactional; rolls back.
--
-- These check the SCHEMA half: that an edge cannot be deleted, that retraction is one-way and
-- explained and frozen, that validity is append-only, that a retracted edge frees its
-- "current" slot, and that the audit view and the Decision withdrawal log record what
-- happened. The half that decides WHICH edge to end or retract is Python, in
-- tests/test_edge_retraction.py, against the real parser. That file also covers the
-- current / --as-of / --as-recorded semantics through the real traversal.

BEGIN;

CREATE TEMP TABLE test_result (
    seq    int GENERATED ALWAYS AS IDENTITY,
    name   text,
    expect text,
    passed boolean,
    detail text
);

CREATE FUNCTION pg_temp.mk(p_type node_type, p_ext text) RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO node (node_type, external_id) VALUES (p_type, p_ext) RETURNING id;
$fn$;

CREATE FUNCTION pg_temp.mk_edge(p_src bigint, p_dst bigint) RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from)
    VALUES (p_src, p_dst, 'references', 'explicit', 'explicit', 'test_fixture',
            now() - interval '2 days')
    RETURNING id;
$fn$;

-- A statement that must be refused: runs it, records whether it raised and with what.
CREATE FUNCTION pg_temp.refused(p_sql text, OUT raised boolean, OUT msg text) AS $fn$
BEGIN
    BEGIN
        EXECUTE p_sql;
        raised := false;
    EXCEPTION WHEN others THEN
        raised := true; msg := SQLERRM;
    END;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- E1  A direct DELETE of an edge is refused.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e1a'); b bigint := pg_temp.mk('issue', 'e1b');
        e bigint; r record;
BEGIN
    e := pg_temp.mk_edge(a, b);
    SELECT * INTO r FROM pg_temp.refused(format('DELETE FROM edge WHERE id = %s', e));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E1 direct DELETE of an edge', 'refused', r.raised AND r.msg LIKE '%never deleted%', r.msg);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E2  Deleting a NODE still removes its edges (withdrawing a Decision depends on it).
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e2a'); b bigint := pg_temp.mk('issue', 'e2b'); e bigint; n int;
BEGIN
    e := pg_temp.mk_edge(a, b);
    DELETE FROM node WHERE id = a;
    SELECT count(*) INTO n FROM edge WHERE id = e;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E2 node delete cascades to its edges', '0 rows left', n = 0, format('%s row(s)', n));
END;
$t$;

-- ---------------------------------------------------------------------------
-- E3  retract_edge sets retracted_at (database clock) and a reason; it needs a reason.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e3a'); b bigint := pg_temp.mk('issue', 'e3b');
        e bigint; ok boolean; r record; rt timestamptz;
BEGIN
    e := pg_temp.mk_edge(a, b);
    SELECT * INTO r FROM pg_temp.refused(format($q$SELECT retract_edge(%s, '  ')$q$, e));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E3a retraction needs a reason', 'refused', r.raised, r.msg);

    SELECT retract_edge(e, 'because') INTO ok;
    SELECT retracted_at INTO rt FROM edge WHERE id = e;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E3b retraction stamps the database clock', 'now()', ok AND rt = now(), rt::text);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E4  A retracted edge is frozen: it cannot be un-retracted, edited, or given an end.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e4a'); b bigint := pg_temp.mk('issue', 'e4b');
        e bigint; r1 record; r2 record; r3 record;
BEGIN
    e := pg_temp.mk_edge(a, b);
    PERFORM retract_edge(e, 'frozen next');
    SELECT * INTO r1 FROM pg_temp.refused(format('UPDATE edge SET retracted_at = NULL, retraction_reason = NULL WHERE id = %s', e));
    SELECT * INTO r2 FROM pg_temp.refused(format($q$UPDATE edge SET extractor = 'x' WHERE id = %s$q$, e));
    SELECT * INTO r3 FROM pg_temp.refused(format('UPDATE edge SET valid_to = now() WHERE id = %s', e));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E4 retracted edge is frozen', 'all three refused',
            r1.raised AND r2.raised AND r3.raised, concat_ws(' | ', r1.msg, r2.msg, r3.msg));
END;
$t$;

-- ---------------------------------------------------------------------------
-- E5  Validity is append-only: valid_from never changes, valid_to cannot change once set.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e5a'); b bigint := pg_temp.mk('issue', 'e5b');
        e bigint; r1 record; r2 record; r3 record;
BEGIN
    e := pg_temp.mk_edge(a, b);
    UPDATE edge SET valid_to = now() WHERE id = e;   -- ending it is allowed, once
    SELECT * INTO r1 FROM pg_temp.refused(format($q$UPDATE edge SET valid_to = valid_to + interval '1 day' WHERE id = %s$q$, e));
    SELECT * INTO r2 FROM pg_temp.refused(format('UPDATE edge SET valid_to = NULL WHERE id = %s', e));
    SELECT * INTO r3 FROM pg_temp.refused(format($q$UPDATE edge SET valid_from = valid_from - interval '1 day' WHERE id = %s$q$, e));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E5 validity is append-only', 'all three refused',
            r1.raised AND r2.raised AND r3.raised, concat_ws(' | ', r1.msg, r2.msg, r3.msg));
END;
$t$;

-- ---------------------------------------------------------------------------
-- E6  Ending validity records when it was learned (system time), set by trigger.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e6a'); b bigint := pg_temp.mk('issue', 'e6b');
        e bigint; rec timestamptz;
BEGIN
    e := pg_temp.mk_edge(a, b);
    UPDATE edge SET valid_to = now() - interval '1 day' WHERE id = e;
    SELECT valid_to_recorded_at INTO rec FROM edge WHERE id = e;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E6 valid_to_recorded_at is stamped', 'now()', rec = now(), rec::text);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E7  A retracted edge frees its "current" slot: the same edge can be asserted again as a
--     new row, and the retracted one stays.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e7a'); b bigint := pg_temp.mk('issue', 'e7b');
        e1 bigint; e2 bigint; kept int;
BEGIN
    e1 := pg_temp.mk_edge(a, b);
    PERFORM retract_edge(e1, 'reassert next');
    e2 := pg_temp.mk_edge(a, b);
    SELECT count(*) INTO kept FROM edge WHERE id = e1 AND retracted_at IS NOT NULL;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E7 reasserting is a new row; the old stays', 'new id, old kept',
            e2 <> e1 AND kept = 1, format('%s -> %s, kept=%s', e1, e2, kept));
END;
$t$;

-- ---------------------------------------------------------------------------
-- E8  Two CURRENT copies of one edge are still impossible.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e8a'); b bigint := pg_temp.mk('issue', 'e8b'); r record;
BEGIN
    PERFORM pg_temp.mk_edge(a, b);
    SELECT * INTO r FROM pg_temp.refused(format('SELECT pg_temp.mk_edge(%s, %s)', a, b));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E8 duplicate current edge', 'refused', r.raised, r.msg);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E9  The audit view lists the retraction with its reason.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e9a'); b bigint := pg_temp.mk('issue', 'e9b');
        e bigint; reason text;
BEGIN
    e := pg_temp.mk_edge(a, b);
    PERFORM retract_edge(e, 'audit me');
    SELECT retraction_reason INTO reason FROM v_edge_retraction_audit WHERE edge_id = e;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E9 v_edge_retraction_audit shows it', 'audit me', reason = 'audit me', reason);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E10 An edge cannot be created already retracted.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE a bigint := pg_temp.mk('issue', 'e10a'); b bigint := pg_temp.mk('issue', 'e10b'); r record;
BEGIN
    SELECT * INTO r FROM pg_temp.refused(format(
        $q$INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor,
                             retracted_at, retraction_reason)
           VALUES (%s, %s, 'references', 'explicit', 'explicit', 'x', now(), 'born retracted')$q$, a, b));
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E10 no edge is born retracted', 'refused', r.raised, r.msg);
END;
$t$;

-- ---------------------------------------------------------------------------
-- E11 A Decision whose only closing edge is retracted is withdrawn, with its reason kept.
-- ---------------------------------------------------------------------------
DO $t$
DECLARE issue bigint; pr bigint; dec bigint; closes bigint; out record; w record;
BEGIN
    SET CONSTRAINTS ALL DEFERRED;
    INSERT INTO node (node_type, external_id, title, thread_key)
    VALUES ('issue', 'e11-i', 'e11 issue', 'thread:11:pr-11') RETURNING id INTO issue;
    INSERT INTO node (node_type, external_id, title, thread_key)
    VALUES ('pull_request', 'e11-p', 'e11 pr', 'thread:11:pr-11') RETURNING id INTO pr;
    INSERT INTO node (node_type, external_id, title, thread_key)
    VALUES ('decision', 'thread:11:pr-11', 'e11 decision', 'thread:11:pr-11') RETURNING id INTO dec;
    INSERT INTO issue (node_id, number, state) VALUES (issue, 911, 'closed');
    INSERT INTO pull_request (node_id, number, state, merged_at) VALUES (pr, 911, 'closed', now());
    INSERT INTO decision (node_id, status, summary) VALUES (dec, 'reconstructed', 's');
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from)
    VALUES (pr, issue, 'closes', 'explicit', 'explicit', 'pr_body_closing_keyword', now() - interval '2 days')
    RETURNING id INTO closes;
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor, valid_from)
    VALUES (dec, issue, 'motivated_by', 'explicit', 'explicit', 'synthesis_closes_cluster', now() - interval '2 days'),
           (dec, pr, 'implemented_by', 'explicit', 'explicit', 'synthesis_closes_cluster', now() - interval '2 days');

    PERFORM retract_edge(closes, 'keyword edited out');
    SELECT * INTO out FROM retract_unsupported_decisions();
    SELECT * INTO w FROM decision_withdrawal WHERE thread_key = 'thread:11:pr-11';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('E11 Decision withdrawn when its closing edge goes', '1 withdrawn, reason kept',
            out.retracted = 1 AND w.reason LIKE '%no longer holds a current closing-keyword edge%'
            AND NOT EXISTS (SELECT 1 FROM decision WHERE node_id = dec),
            COALESCE(w.reason, 'no withdrawal row'));
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
