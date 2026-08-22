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
--   B  in-thread closes, nothing merged  -- the #17 landing gate
--   F  residual: passes every mechanical check and still produced no Decision
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
    thread_landed(t.thread_key)                                               AS landed
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
        WHEN NOT landed                                 THEN 'B  closes, nothing merged'
        ELSE                                                 'F  RESIDUAL -- rubric bug'
    END AS bucket
    FROM t_facts WHERE NOT has_decision
) x GROUP BY bucket ORDER BY bucket;

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
\echo '== B detail: closed-as-completed threads the landing gate refused =='
\echo '   (the audit hypothesised their merged PR sat in the unfetched third of the window)'
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
