-- Recall audit, re-runnable (issue #46).
--
-- The original audit (#23, eval/RECALL_AUDIT.md) classified every non-Decision thread by
-- hand, with ad-hoc queries that were never written down. So when the corpus grew from 145
-- pull requests to 217, none of its numbers could be checked -- they could only be
-- re-derived from scratch. This file is those queries, so the audit is a measurement that
-- can be repeated rather than a snapshot that can only age.
--
-- Usage:  psql -U postgres -d dg -v repo=1 -f eval/recall_audit.sql
--
-- BUCKET ORDER IS LOAD-BEARING. The buckets are not disjoint as prose; they are made
-- disjoint by the order of the CASE below, which reproduces the original audit's order:
--
--   A  no issue in the thread            -- no Motivation can exist to find
--   D  singleton issue, nothing linked   -- no Implementation can exist to find
--   C  issue present, no in-thread closes
--   B1 in-thread closes, declined        -- the #17 landing gate, refusing correctly
--   B2 in-thread closes, nothing merged  -- the landing gate, possibly missing a link
--   F  residual: passes every mechanical check and still produced no Decision
--
-- B was one bucket until #88. It read "closes, nothing merged" and carried the note that
-- the original audit "hypothesised their merged PR sat in the unfetched third of the
-- window". That is now checkable, and it is wrong for most of them: where the issue was
-- closed `not_planned` there is no merged pull request anywhere, fetched or not, because
-- the request was declined. The gate refused exactly as designed, and counting that beside
-- a possible miss overstates the recall problem. B1 is the correct refusal; B2 is the one
-- worth reading -- an issue closed `completed` whose in-thread pull request never merged
-- means the work landed by some other route and the graph did not connect it.
--
-- A singleton issue also has no in-thread `closes` edge, so it matches C as well as D;
-- D is tested first, exactly as the original did (which is why it reported C = 0, D = 34).
-- Read a nonzero F as a rubric bug, not as a curiosity.

-- Defaults to repo 1 so the file runs with no arguments; pass -v repo=N for another.
\if :{?repo}
\else
\set repo 1
\endif

-- Thread membership comes from node.thread_key, the same stand-in the rubric uses.
CREATE OR REPLACE TEMP VIEW t_threads AS
SELECT DISTINCT thread_key
FROM node
WHERE repo_node_id = :repo AND thread_key IS NOT NULL AND node_type <> 'decision';

CREATE OR REPLACE TEMP VIEW t_facts AS
SELECT
    t.thread_key,
    EXISTS (SELECT 1 FROM node n
             WHERE n.thread_key = t.thread_key AND n.node_type = 'decision')  AS has_decision,
    EXISTS (SELECT 1 FROM node n
             WHERE n.thread_key = t.thread_key AND n.node_type = 'issue')     AS has_issue,
    (SELECT count(*) FROM node n
      WHERE n.thread_key = t.thread_key AND n.node_type <> 'decision')        AS members,
    EXISTS (SELECT 1 FROM edge e
              JOIN node s ON s.id = e.src_node_id
              JOIN node d ON d.id = e.dst_node_id
             WHERE e.edge_type = 'closes' AND e.valid_to IS NULL
               AND s.thread_key = t.thread_key
               AND d.thread_key = t.thread_key)                               AS closes_in_thread,
    thread_landed(t.thread_key)                                               AS landed,
    -- Declined is a property of the ISSUE, not of the thread's shape, which is why the
    -- audit could not see it: every other fact here is derived from nodes and edges.
    EXISTS (SELECT 1 FROM node n JOIN issue i ON i.node_id = n.id
             WHERE n.thread_key = t.thread_key
               AND i.state_reason::text = 'not_planned')                      AS declined
FROM t_threads t;

\echo ''
\echo '== Corpus =='
SELECT
    (SELECT count(*) FROM t_threads)                                        AS threads,
    (SELECT count(*) FROM t_facts WHERE has_decision)                       AS decision_threads,
    (SELECT count(*) FROM t_facts WHERE NOT has_decision)                   AS non_decision_threads,
    (SELECT count(*) FROM node WHERE repo_node_id = :repo
       AND node_type = 'pull_request')                                      AS pull_requests,
    (SELECT count(*) FROM node WHERE repo_node_id = :repo
       AND node_type = 'issue')                                             AS issues,
    (SELECT count(*) FROM node WHERE repo_node_id = :repo
       AND node_type = 'commit')                                            AS commits;

\echo ''
\echo '== Buckets (non-Decision threads) =='
SELECT bucket, count(*) AS threads
FROM (
    SELECT CASE
        WHEN NOT has_issue                              THEN 'A  no issue in thread'
        WHEN members = 1                                THEN 'D  singleton issue'
        WHEN NOT closes_in_thread                       THEN 'C  issue, no in-thread closes'
        WHEN NOT landed AND declined                    THEN 'B1 closes, declined'
        WHEN NOT landed                                 THEN 'B2 closes, nothing merged'
        ELSE                                                 'F  RESIDUAL -- rubric bug'
    END AS bucket
    FROM t_facts WHERE NOT has_decision
) x GROUP BY bucket ORDER BY bucket;

\echo ''
\echo '== F must be empty =='
\echo '   (the header calls a nonzero F a rubric bug, not a curiosity -- a thread with an'
\echo '    issue, a linked implementation, an in-thread closes edge and merged work has'
\echo '    passed every mechanical part of the rubric, so the absence of a Decision is'
\echo '    unexplained. That was prose until #88; a re-measure that produced one would'
\echo '    have printed it in a table and stayed quiet. A NULL bucket is checked with it:'
\echo '    it would mean the CASE stopped covering every thread.)'
DO $$
DECLARE
    v_residual bigint;
    v_unbucketed bigint;
BEGIN
    SELECT count(*) FILTER (WHERE bucket = 'F'),
           count(*) FILTER (WHERE bucket IS NULL)
      INTO v_residual, v_unbucketed
      FROM (
        SELECT CASE
            WHEN NOT has_issue                THEN 'A'
            WHEN members = 1                  THEN 'D'
            WHEN NOT closes_in_thread         THEN 'C'
            WHEN NOT landed AND declined      THEN 'B1'
            WHEN NOT landed                   THEN 'B2'
            ELSE                                   'F'
        END AS bucket
        FROM t_facts WHERE NOT has_decision
      ) x;

    IF v_unbucketed > 0 THEN
        RAISE EXCEPTION '% thread(s) matched no bucket', v_unbucketed;
    END IF;
    IF v_residual > 0 THEN
        RAISE EXCEPTION
            '% thread(s) in F: passed every mechanical check and produced no Decision',
            v_residual;
    END IF;
    RAISE NOTICE 'F is empty; every non-Decision thread has a named reason';
END $$;

\echo ''
\echo '== E: closes edges crossing a thread boundary =='
\echo '   (vacuous by construction -- _link_body_refs unions the two threads at the moment'
\echo '    it creates the edge, so a nonzero result here means that invariant broke.)'
SELECT count(*) AS cross_thread_closes
FROM edge e
JOIN node s ON s.id = e.src_node_id
JOIN node d ON d.id = e.dst_node_id
WHERE e.edge_type = 'closes' AND e.valid_to IS NULL
  AND s.thread_key IS DISTINCT FROM d.thread_key;

\echo ''
\echo '== B detail: threads the landing gate refused, and which kind each is =='
\echo '   (declined = closed not_planned, so no merged PR exists to find and the gate was'
\echo '    right. The rest are the candidate recall gaps: the issue says the work was done,'
\echo '    so it landed by a route the graph did not connect. #88 replaced the original'
\echo '    hypothesis, that their merged PR sat in the unfetched third of the window.)'
SELECT f.thread_key,
       (SELECT string_agg(n.external_id, ',' ORDER BY n.external_id) FROM node n
         WHERE n.thread_key = f.thread_key AND n.node_type = 'issue')          AS issues,
       (SELECT string_agg(DISTINCT n.raw->>'state_reason', ',') FROM node n
         WHERE n.thread_key = f.thread_key AND n.node_type = 'issue')          AS state_reason,
       (SELECT count(*) FROM node n
         WHERE n.thread_key = f.thread_key AND n.node_type = 'pull_request')   AS prs,
       (SELECT count(*) FROM node n
         WHERE n.thread_key = f.thread_key AND n.node_type = 'commit')         AS commits
FROM t_facts f
WHERE NOT f.has_decision AND f.has_issue AND f.members > 1
  AND f.closes_in_thread AND NOT f.landed
ORDER BY f.thread_key;

\echo ''
\echo '== A detail: how much of bucket A records any "why" at all =='
SELECT
    count(*) FILTER (WHERE n.node_type = 'pull_request')                       AS prs_in_A,
    count(*) FILTER (WHERE n.node_type = 'commit')                             AS commits_in_A,
    count(*) FILTER (WHERE n.node_type = 'pull_request'
                       AND n.raw->>'body' ~* '(clos|fix|resolv)e[sd]?\s+#[0-9]+') AS prs_with_closing_keyword,
    count(*) FILTER (WHERE n.node_type = 'pull_request'
                       AND n.raw->>'body' ~ '#[0-9]+')                         AS prs_with_any_ref
FROM t_facts f
JOIN node n ON n.thread_key = f.thread_key
WHERE NOT f.has_decision AND NOT f.has_issue;
