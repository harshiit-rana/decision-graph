-- Every figure the documentation quotes, in one place (issue #67).
--
-- The README states repository figures in the present tense -- "235 thread clusters yield
-- 13 Decisions", "the corroborated tier is sparse: 7 of 235 threads". Each was true when
-- it was written and each was measured by a query nobody wrote down, so when an ingestion
-- run moved the corpus the numbers could not be checked, only re-derived. They drifted
-- twice before anyone noticed, and the second time the ratio at the centre of the coverage
-- argument had a stale denominator and a current numerator.
--
-- This is eval/recall_audit.sql's argument (#46, #48) applied one level out: a figure that
-- can only be produced once is a snapshot, and a snapshot in a present-tense sentence goes
-- quietly wrong. Every number in the README's "Known limitations" and "Evaluation" sections
-- should be reproducible by running this file.
--
-- Usage:  psql -U postgres -d dg -v repo=1 -f eval/figures.sql
--
-- It reads and writes nothing; there is no transaction to roll back.

\if :{?repo}
\else
\set repo 1
\endif

\echo
\echo == Corpus ==

SELECT
    (SELECT count(*) FROM node n WHERE n.repo_node_id = :repo
      AND n.node_type = 'pull_request')                                  AS pull_requests,
    (SELECT count(*) FROM node n WHERE n.repo_node_id = :repo
      AND n.node_type = 'issue')                                         AS issues,
    (SELECT count(*) FROM node n WHERE n.repo_node_id = :repo
      AND n.node_type = 'commit')                                        AS commits,
    (SELECT count(*) FROM node n WHERE n.repo_node_id = :repo
      AND n.node_type = 'release')                                       AS releases,
    -- The denominator of every coverage claim. Decision nodes are excluded because a
    -- Decision sits in the cluster it was reconstructed from and would otherwise be
    -- counted as evidence of its own coverage; recall_audit.sql defines it the same way.
    (SELECT count(DISTINCT n.thread_key) FROM node n WHERE n.repo_node_id = :repo
      AND n.thread_key IS NOT NULL AND n.node_type <> 'decision')        AS thread_clusters
\gset corpus_

SELECT :corpus_pull_requests AS pull_requests, :corpus_issues AS issues,
       :corpus_commits AS commits, :corpus_releases AS releases,
       :corpus_thread_clusters AS thread_clusters;

\echo
\echo == Decisions (5.1) ==
\echo    coverage is Decisions over thread_clusters above; this measures precision on none of it

SELECT count(*)                                          AS decisions,
       count(*) FILTER (WHERE d.status = 'explicit')     AS explicit_status,
       count(*) FILTER (WHERE d.status = 'reconstructed') AS reconstructed_status,
       round(100.0 * count(*) / NULLIF(:corpus_thread_clusters, 0), 1) AS pct_of_threads
FROM decision d
JOIN node n ON n.id = d.node_id
WHERE n.repo_node_id = :repo;

\echo
\echo == Evidence tiers (5.4) ==
\echo    a thread counts once however many corroborated edges it carries

SELECT e.evidence_tier::text                                     AS tier,
       count(*)                                                  AS edges,
       count(DISTINCT COALESCE(sn.thread_key, dn.thread_key))    AS threads
FROM edge e
JOIN node sn ON sn.id = e.src_node_id
JOIN node dn ON dn.id = e.dst_node_id
WHERE COALESCE(sn.repo_node_id, sn.id) = :repo
   OR COALESCE(dn.repo_node_id, dn.id) = :repo
GROUP BY 1
ORDER BY 1;

\echo
\echo == The four corroboration categories, as populated on this repo ==
\echo    the tier needs 3 of 4, so the smallest of these is what bounds it

SELECT
    (SELECT count(*) FROM edge WHERE edge_type = 'closes')          AS declared_closes,
    (SELECT count(*) FROM edge WHERE edge_type = 'implements')      AS structural_implements,
    (SELECT count(*) FROM edge WHERE edge_type = 'reviewed')        AS attested_reviews,
    -- Reviews and release mentions are the scarce two. Both are counted by the THREAD they
    -- touch, since that is the unit the rubric asks about: a PR with four reviews is one
    -- ATTESTED signal, not four.
    (SELECT count(DISTINCT dn.thread_key) FROM edge e
       JOIN node dn ON dn.id = e.dst_node_id
      WHERE e.edge_type = 'reviewed' AND dn.thread_key IS NOT NULL)  AS threads_with_review,
    (SELECT count(DISTINCT sn.thread_key) FROM edge e
       JOIN node sn ON sn.id = e.src_node_id
      WHERE e.edge_type = 'deployed_by' AND sn.thread_key IS NOT NULL) AS threads_in_release_notes;

\echo
\echo == Issue outcomes ==
\echo    not_planned is the rejected-decision category the roadmap note is about: real
\echo    decisions v1 cannot express, because 5.1 Validation requires that work landed

SELECT COALESCE(i.state_reason, '(none)') AS state_reason,
       i.state,
       count(*)
FROM issue i
JOIN node n ON n.id = i.node_id
WHERE n.repo_node_id = :repo
GROUP BY 1, 2
ORDER BY 3 DESC;

\echo
\echo == The reference queue (issues #3, #61) ==

SELECT edge_type::text, open_refs, target_outside_window, retracted
FROM v_pending_reference_status
WHERE repo_node_id = :repo
ORDER BY 1;

\echo
\echo == Repository properties the README lists as limitations ==
\echo    each is a property of the target repo or a v1 scope boundary, not a defect

SELECT
    (SELECT count(*) FROM edge WHERE edge_type = 'owns')            AS ownership_edges,
    (SELECT count(*) FROM node WHERE node_type = 'wiki_page')       AS wiki_pages,
    (SELECT count(*) FROM edge WHERE tag = 'inferred')              AS inferred_edges;
