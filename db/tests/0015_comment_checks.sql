-- 0015_comment_checks.sql
-- A comment is an artifact and must stay one (issue #106).
-- Transactional; rolls back.
--
-- The whole safety argument for ingesting discussion is that it changes nothing the rubric
-- reads. The PRD roadmap note says asserting a rejected outcome without rejection-rationale
-- extraction "would reintroduce exactly the failure §5.1 exists to prevent", and sampled
-- closing comments bear it out -- "ok, my bad" is a reporter withdrawing, and the graph now
-- holds that sentence. If a comment could ever satisfy a rubric clause or feed
-- corroboration, the prose would start deciding things, which is the one outcome this
-- feature promised not to have.
--
-- That promise was prose in two migration comments. It is a check now.

BEGIN;

CREATE TEMP TABLE test_result (
    seq    int GENERATED ALWAYS AS IDENTITY,
    name   text,
    expect text,
    passed boolean,
    detail text
);

CREATE FUNCTION pg_temp.mk(p_type node_type, p_ext text, p_repo bigint DEFAULT NULL,
                           p_thread text DEFAULT NULL)
RETURNS bigint LANGUAGE sql AS $fn$
    INSERT INTO node (node_type, external_id, repo_node_id, thread_key)
    VALUES (p_type, p_ext, p_repo, p_thread) RETURNING id;
$fn$;


-- T1  A comment must hang off something. An orphan would be prose with no artifact it
--     belongs to, which is a quotation without a source.
DO $t$
DECLARE repo bigint; c bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 'c-t1-repo');
    c    := pg_temp.mk('comment', 'c-t1-comment', repo);
    BEGIN
        INSERT INTO comment (node_id, parent_node_id, body)
        VALUES (c, 999999999, 'orphan');
        ok := true;
    EXCEPTION WHEN foreign_key_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T1 comment with no parent refused', 'rejected', NOT ok, err);
END;
$t$;


-- T2  The detail table is for comments only, like every other artifact table.
DO $t$
DECLARE repo bigint; i bigint; ok boolean; err text;
BEGIN
    repo := pg_temp.mk('repository', 'c-t2-repo');
    i    := pg_temp.mk('issue', 'c-t2-issue', repo);
    BEGIN
        INSERT INTO comment (node_id, node_type, parent_node_id, body)
        VALUES (i, 'issue', i, 'not a comment');
        ok := true;
    EXCEPTION WHEN check_violation OR foreign_key_violation THEN ok := false; err := SQLERRM;
    END;
    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T2 non-comment node refused by the detail table', 'rejected', NOT ok, err);
END;
$t$;


-- T3  THE LOAD-BEARING ONE. A thread already carrying two of the four §5.4 categories must
--     still carry two after discussion is added. Built to sit one short of the >=3
--     threshold on purpose: comparing a thread that corroborates in neither state proves
--     nothing, and this check is the whole safety argument for ingesting prose at all.
DO $t$
DECLARE
    repo bigint; iss bigint; pr bigint; c bigint;
    before_cats int; after_cats int; before_ok boolean; after_ok boolean;
BEGIN
    repo := pg_temp.mk('repository', 'c-t3-repo');
    iss  := pg_temp.mk('issue', 'c-t3-issue', repo, 'thread:c-t3');
    pr   := pg_temp.mk('pull_request', 'c-t3-pr', repo, 'thread:c-t3');

    -- Two of four: declared (a closing keyword) and structural (a commit-list implements).
    --
    -- The extractor names are load-bearing and not decoration. Every category in
    -- v_thread_corroboration is keyed to one -- `%_closing_keyword`, `pr_commit_list`,
    -- `pr_review`, `release_notes_reference` -- so an edge written by any other producer
    -- counts for nothing. A first version of this fixture used 'test_fixture' and sat at
    -- zero categories in both states, which passed while proving nothing.
    --
    -- That keying is also why a comment edge cannot corroborate even in principle:
    -- `issue_comment` is not one of the four names, and adding it would be a deliberate
    -- act in this view rather than an accident in the extractor.
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (pr, iss, 'closes', 'explicit', 'explicit', 'body_closing_keyword'),
           (pr, iss, 'implements', 'explicit', 'explicit', 'pr_commit_list');

    SELECT coalesce(max(category_count), 0), coalesce(bool_or(corroborates), false)
      INTO before_cats, before_ok
      FROM v_thread_corroboration WHERE thread_key = 'thread:c-t3';

    -- Now the discussion, including the kind of sentence that reads like a decision.
    c := pg_temp.mk('comment', 'c-t3-comment', repo, 'thread:c-t3');
    INSERT INTO comment (node_id, parent_node_id, body)
    VALUES (c, iss, 'we are not going to do this');
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (iss, c, 'discussed_in', 'explicit', 'explicit', 'test_fixture');

    SELECT coalesce(max(category_count), 0), coalesce(bool_or(corroborates), false)
      INTO after_cats, after_ok
      FROM v_thread_corroboration WHERE thread_key = 'thread:c-t3';

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T3 discussion adds no corroboration category', '2 -> 2, still short',
            before_cats = 2 AND after_cats = 2
              AND before_ok IS FALSE AND after_ok IS FALSE,
            format('categories %s -> %s, corroborates %s -> %s',
                   before_cats, after_cats, before_ok, after_ok));
END;
$t$;


-- T4  A comment satisfies no rubric clause. The rubric is the thing that decides whether a
--     Decision exists, and prose must not reach it.
DO $t$
DECLARE repo bigint; iss bigint; c bigint; verdict boolean;
BEGIN
    repo := pg_temp.mk('repository', 'c-t4-repo');
    iss  := pg_temp.mk('issue', 'c-t4-issue', repo, 'thread:c-t4');
    c    := pg_temp.mk('comment', 'c-t4-comment', repo, 'thread:c-t4');
    INSERT INTO comment (node_id, parent_node_id, body)
    VALUES (c, iss, 'closing, we will not implement this');
    INSERT INTO edge (src_node_id, dst_node_id, edge_type, tag, evidence_tier, extractor)
    VALUES (iss, c, 'discussed_in', 'explicit', 'explicit', 'test_fixture');

    -- A thread of an issue and a comment, with no merged work, is not a Decision. It was
    -- not one before comments existed and it must not become one because someone wrote a
    -- sentence in it.
    SELECT thread_landed('thread:c-t4') INTO verdict;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T4 discussion alone does not make a thread landed', 'false',
            verdict IS FALSE, format('thread_landed=%s', verdict));
END;
$t$;


-- T5  Deleting the artifact takes its discussion with it. A comment outliving its parent
--     would be quoted prose attributed to nothing.
DO $t$
DECLARE repo bigint; iss bigint; c bigint; left_over int;
BEGIN
    repo := pg_temp.mk('repository', 'c-t5-repo');
    iss  := pg_temp.mk('issue', 'c-t5-issue', repo);
    c    := pg_temp.mk('comment', 'c-t5-comment', repo);
    INSERT INTO comment (node_id, parent_node_id, body) VALUES (c, iss, 'text');

    DELETE FROM node WHERE id = iss;
    SELECT count(*) INTO left_over FROM comment WHERE node_id = c;

    INSERT INTO test_result (name, expect, passed, detail)
    VALUES ('T5 deleting the parent removes the comment', '0',
            left_over = 0, format('rows left=%s', left_over));
END;
$t$;


\echo ''
\echo '== 0015 comment checks =='
SELECT seq, name, expect, passed, coalesce(detail, '') AS detail
FROM test_result ORDER BY seq;

DO $$
DECLARE v_failed int; v_total int;
BEGIN
    SELECT count(*) FILTER (WHERE NOT passed), count(*) INTO v_failed, v_total
    FROM test_result;
    IF v_failed > 0 THEN
        RAISE EXCEPTION '% of % check(s) failed', v_failed, v_total;
    END IF;
    RAISE NOTICE 'all % checks passed', v_total;
END $$;

ROLLBACK;
