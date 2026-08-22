-- The thread_key census (issue #26), re-runnable.
--
-- #26 asks a question no query can answer: are there two clusters a human would call one
-- decision, with no explicit edge between them? If an edge existed they would already be
-- one cluster, so the thing being looked for is by definition invisible to the graph. The
-- issue's own prescription is sampled human review.
--
-- What this script does is make that review small enough to finish, and make the sample
-- frame an argument rather than a hunch.
--
-- THE FRAME. The obvious frame -- "threads that failed for want of Motivation" -- is bucket
-- A, and it is 153 threads. Most of that is wasted effort, because a thread with no merged
-- pull request fails clause 2 (landing) no matter what motivation is found for it. Finding
-- its issue would change nothing. The threads where thread_key's boundary is actually the
-- binding constraint are the ones that have a merged implementation and lack only the why:
--
--     no issue in the thread  AND  thread_landed() = true
--
-- That is 14 threads, not 153. Fourteen is a census, not a sample -- the whole population
-- can be read by hand, so the review has no sampling error to disclose.
--
-- THE CANDIDATES. For each thread, the issues most likely to be its missing motivation,
-- ranked by pg_trgm title similarity. This is a suggestion engine for a human reader and
-- nothing more. It cannot confirm a pair, and a thread with no candidate above the
-- threshold has not been shown to be motivation-free -- only that title text did not
-- connect it to anything. Read `sim` as "worth looking at", never as a score.
--
-- Candidates are restricted to issues opened BEFORE the pull request merged. An issue filed
-- afterwards cannot be what motivated the work, however well the titles match, and the
-- restriction is not a nicety: on the first run 6 of 12 candidates failed it, including the
-- highest-scoring pair in the whole set (PR 5928 / issue 6044, sim 0.301, filed 105 days
-- after the merge). Ranking on text alone puts an impossible pair at the top of the list.
--
-- Two of the first 16 this produced turned out to name their issue outright, in a URL form
-- the reference parser dropped (#50). Those became Decisions and left the frame, which is
-- why it is 14 now. That is the failure mode this census is best at finding: not a subtle
-- human judgement, but an explicit link the machine could not read.
--
-- Usage:  psql -U postgres -d dg -f eval/thread_key_census.sql
--         psql -U postgres -d dg -v repo=1 -v top=3 -v floor=0.15 -f eval/thread_key_census.sql

\if :{?repo}
\else
  \set repo 1
\endif
\if :{?top}
\else
  \set top 3
\endif
\if :{?floor}
\else
  \set floor 0.15
\endif

CREATE OR REPLACE TEMP VIEW c_frame AS
WITH t AS (
    SELECT DISTINCT thread_key
    FROM node
    WHERE repo_node_id = :repo AND thread_key IS NOT NULL AND node_type <> 'decision'
)
SELECT t.thread_key
FROM t
WHERE NOT EXISTS (
        SELECT 1 FROM node n
         WHERE n.thread_key = t.thread_key AND n.node_type = 'issue')
  AND NOT EXISTS (
        SELECT 1 FROM node n
         WHERE n.thread_key = t.thread_key AND n.node_type = 'decision')
  AND thread_landed(t.thread_key);

CREATE OR REPLACE TEMP VIEW c_pr AS
SELECT f.thread_key, n.id, n.external_id, n.title, n.url, p.merged_at
FROM c_frame f
JOIN node n ON n.thread_key = f.thread_key AND n.node_type = 'pull_request'
JOIN pull_request p ON p.node_id = n.id AND p.merged_at IS NOT NULL;

\echo ''
\echo '== Frame =='
\echo '   bucket A is every thread with no issue; the census is the subset that also has a'
\echo '   merged PR, i.e. the only ones where finding the motivation would change the verdict.'
SELECT
    (SELECT count(*) FROM c_frame)                                   AS census_threads,
    (SELECT count(*) FROM c_pr)                                      AS merged_prs_in_frame,
    (SELECT count(*) FROM (
        SELECT DISTINCT thread_key FROM node
         WHERE repo_node_id = :repo AND thread_key IS NOT NULL AND node_type <> 'decision'
     ) t
     WHERE NOT EXISTS (SELECT 1 FROM node n
                        WHERE n.thread_key = t.thread_key AND n.node_type = 'issue'))
                                                                     AS bucket_a_total;

\echo ''
\echo '== Census: every merged PR whose thread holds no issue =='
SELECT pr.thread_key, pr.external_id AS pr, pr.merged_at::date AS merged, left(pr.title, 58) AS title
FROM c_pr pr ORDER BY pr.thread_key;

\echo ''
\echo '== Candidate motivations, by title similarity =='
\echo '   A suggestion for a human reader. It cannot confirm a pair, and an empty result is'
\echo '   not evidence that a thread has no motivation -- only that titles did not connect it.'
SELECT thread_key, pr_num AS pr, issue_num AS issue, sim, opened_days_before_merge,
       issue_state, left(issue_title, 44) AS issue_title
FROM (
    SELECT pr.thread_key,
           pr.external_id AS pr_num,
           i.external_id  AS issue_num,
           i.title        AS issue_title,
           i.raw->>'state_reason' AS issue_state,
           round(similarity(pr.title, i.title)::numeric, 3) AS sim,
           (EXTRACT(epoch FROM (pr.merged_at - i.source_created_at)) / 86400)::int
               AS opened_days_before_merge,
           row_number() OVER (
               PARTITION BY pr.thread_key ORDER BY similarity(pr.title, i.title) DESC
           ) AS rk
    FROM c_pr pr
    JOIN node i
      ON i.node_type = 'issue' AND i.repo_node_id = :repo
     AND i.thread_key IS DISTINCT FROM pr.thread_key
     AND i.source_created_at < pr.merged_at
) ranked
WHERE rk <= :top AND sim >= :floor
ORDER BY thread_key, sim DESC;

\echo ''
\echo '== Threads with no candidate above the floor =='
\echo '   Unexplained by this method, NOT shown to be motivation-free.'
SELECT f.thread_key
FROM c_frame f
WHERE NOT EXISTS (
    SELECT 1
    FROM c_pr pr
    JOIN node i ON i.node_type = 'issue' AND i.repo_node_id = :repo
                AND i.thread_key IS DISTINCT FROM pr.thread_key
    WHERE pr.thread_key = f.thread_key
      AND i.source_created_at < pr.merged_at
      AND similarity(pr.title, i.title) >= :floor
)
ORDER BY f.thread_key;
