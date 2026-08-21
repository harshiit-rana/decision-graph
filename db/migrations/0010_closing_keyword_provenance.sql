-- 0010: split body_closing_keyword / body_issue_mention provenance by source (issue #12).
--
-- _link_body_refs is called from both extract_issue_or_pr (PR/issue body text) and
-- extract_commit (commit message text), and both passed the same extractor label. A
-- `closes` edge sourced from a PR description was therefore indistinguishable from one
-- sourced from a commit message -- different artifacts, different authors, different
-- reliability -- which made §7 explainability imprecise and per-source auditing impossible.
--
-- The code now labels new edges "pr_body_{closing_keyword,issue_mention}" or
-- "commit_message_{closing_keyword,issue_mention}" depending on which extractor produced
-- them. This relabels the edges (and any still-queued pending_reference rows) that were
-- written under the old undifferentiated labels, keyed on the source node's type -- the
-- same signal the code now uses going forward.
--
-- 0006's DECLARED corroboration category matched the extractor by exact literal
-- ('body_closing_keyword'), which the relabelling above would silently empty. Both
-- v_thread_corroboration and v_edge_corroboration_audit are recreated with a suffix match
-- so DECLARED keeps recognising a closing-keyword edge regardless of which body it came
-- from. This is the only behavioural change in this migration; category membership itself
-- is unchanged, so apply_corroboration() is re-run only to reassert that invariant, not to
-- move any thread across the 3-of-4 threshold.

BEGIN;

UPDATE edge e
SET extractor = CASE
    WHEN n.node_type IN ('issue', 'pull_request') THEN 'pr_body_' || split_part(e.extractor, 'body_', 2)
    WHEN n.node_type = 'commit'                   THEN 'commit_message_' || split_part(e.extractor, 'body_', 2)
END
FROM node n
WHERE e.src_node_id = n.id
  AND e.extractor IN ('body_closing_keyword', 'body_issue_mention')
  AND n.node_type IN ('issue', 'pull_request', 'commit');

UPDATE pending_reference p
SET extractor = CASE
    WHEN n.node_type IN ('issue', 'pull_request') THEN 'pr_body_' || split_part(p.extractor, 'body_', 2)
    WHEN n.node_type = 'commit'                   THEN 'commit_message_' || split_part(p.extractor, 'body_', 2)
END
FROM node n
WHERE p.src_node_id = n.id
  AND p.extractor IN ('body_closing_keyword', 'body_issue_mention')
  AND n.node_type IN ('issue', 'pull_request', 'commit');

-- Recreate DECLARED's predicate as a suffix match, not an exact literal, so it survives
-- the relabelling above (and any future body-derived closing-keyword source).
DROP VIEW v_edge_corroboration_audit;
DROP VIEW v_thread_corroboration;

CREATE VIEW v_thread_corroboration AS
WITH threads AS (
    SELECT DISTINCT thread_key FROM node WHERE thread_key IS NOT NULL
),
categories AS (
    SELECT t.thread_key,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node s ON s.id = e.src_node_id
                   JOIN node d ON d.id = e.dst_node_id
                   WHERE e.edge_type = 'closes'
                     AND e.extractor LIKE '%\_closing_keyword' ESCAPE '\'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL
                     AND d.node_type = 'issue'
                     AND s.thread_key = t.thread_key
                     AND d.thread_key = t.thread_key) AS declared,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node s ON s.id = e.src_node_id
                   JOIN node d ON d.id = e.dst_node_id
                   WHERE e.edge_type = 'implements'
                     AND e.extractor = 'pr_commit_list'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL
                     AND s.thread_key = t.thread_key
                     AND d.thread_key = t.thread_key) AS structural,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node d ON d.id = e.dst_node_id
                   WHERE e.edge_type = 'reviewed'
                     AND e.extractor = 'pr_review'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL
                     AND d.thread_key = t.thread_key) AS attested,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node s ON s.id = e.src_node_id
                   WHERE e.edge_type = 'deployed_by'
                     AND e.extractor = 'release_notes_reference'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL
                     AND s.thread_key = t.thread_key) AS published
    FROM threads t
)
SELECT thread_key,
       declared, structural, attested, published,
       (declared::int + structural::int + attested::int + published::int) AS category_count,
       (declared::int + structural::int + attested::int + published::int) >= 3 AS corroborates
FROM categories;

COMMENT ON VIEW v_thread_corroboration IS
    'PRD v3.1 §5.4 corroboration categories per thread cluster. Observed extractors '
    'only -- synthesis_* edges are excluded from counting because they are derived from '
    'signals already counted here. DECLARED matches any *_closing_keyword extractor, '
    'not just body_closing_keyword -- see #12.';

CREATE VIEW v_edge_corroboration_audit AS
SELECT e.id AS edge_id,
       e.edge_type,
       e.tag,
       e.evidence_tier,
       e.extractor,
       COALESCE(
           -- Normal edges: both endpoints in one thread (clause 2).
           CASE WHEN s.thread_key IS NOT NULL AND s.thread_key = d.thread_key
                THEN s.thread_key END,
           -- Person-sourced edges are thread-less at the source, so the destination
           -- alone carries the thread.
           CASE WHEN s.node_type = 'person' AND d.thread_key IS NOT NULL
                THEN d.thread_key END
       ) AS thread_key,
       tc.category_count,
       tc.declared, tc.structural, tc.attested, tc.published,
       (
           e.tag = 'explicit'
           AND e.valid_to IS NULL
           AND corroboration_eligible_edge_type(e.edge_type)
           AND COALESCE(tc.corroborates, false)
       ) AS qualifies
FROM edge e
JOIN node s ON s.id = e.src_node_id
JOIN node d ON d.id = e.dst_node_id
LEFT JOIN v_thread_corroboration tc
       ON tc.thread_key = COALESCE(
              CASE WHEN s.thread_key IS NOT NULL AND s.thread_key = d.thread_key
                   THEN s.thread_key END,
              CASE WHEN s.node_type = 'person' AND d.thread_key IS NOT NULL
                   THEN d.thread_key END);

COMMENT ON VIEW v_edge_corroboration_audit IS
    'Rubric result per edge. Expected invariant after apply_corroboration(): no row '
    'where qualifies <> (evidence_tier = ''corroborated'') among explicit edges.';

-- Category membership is unchanged (same edges, renamed) -- this reasserts the tier
-- invariant, it does not move any thread across the 3-of-4 threshold.
SELECT * FROM apply_corroboration();

COMMIT;
