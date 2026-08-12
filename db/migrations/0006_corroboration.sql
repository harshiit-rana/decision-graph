-- 0006_corroboration.sql
-- Engine 3, Evidence Ranking: the explicit -> corroborated upgrade (PRD v3.1 §5.4).
--
-- No schema change is required. `evidence_tier` already exists, and the tag/tier
-- invariant from 0001 permits tag='explicit' with evidence_tier='corroborated' --
-- (tag='inferred') = (tier='inferred') evaluates false = false. Only inference is
-- locked to its tier; the corroborated upgrade was designed for from v1.
--
-- Packaged exactly like the Decision rubric in 0002: the rule exists once, as a view
-- usable as a query, and the backfill is derived from it rather than restating it.
--
-- RUBRIC (mechanical, no model judgment). An explicit edge upgrades to `corroborated`
-- iff all three hold:
--
--   1. Its edge_type is evidence-carrying. `created`, `mentions`, `references` and
--      `depends_on` are excluded: authorship and loose mentions corroborate nothing.
--   2. Its endpoints share one non-null thread cluster -- the same bounded-window
--      definition as clause 3 of the §5.1 Decision rubric. Person-sourced edges
--      (`reviewed`) are thread-less at the source, so only the destination must be in
--      the thread.
--   3. That thread exhibits >= 3 of 4 signal categories.
--
-- THE FOUR CATEGORIES, each pinned to an edge_type AND an extractor, both already
-- stored on every edge so the tier is auditable after the fact:
--
--   DECLARED    `closes` via body_closing_keyword, dst an issue   -- the author's prose
--   STRUCTURAL  `implements` via pr_commit_list                   -- GitHub's PR/commit structure
--   ATTESTED    `reviewed` via pr_review                          -- a reviewer's action
--   PUBLISHED   `deployed_by` via release_notes_reference         -- a maintainer's release note
--
-- ONLY OBSERVED EDGES COUNT. Categories are computed from extractors that read GitHub
-- directly; every `synthesis_*` extractor is excluded. This is not a stylistic choice:
-- synthesis_closes_cluster emits BOTH motivated_by and implemented_by from a SINGLE
-- underlying `closes` edge, so counting them as two converging signals would be one
-- signal wearing two hats -- circular self-corroboration.
--
-- Note the asymmetry, which is deliberate: synthesis-derived edges are EXCLUDED from
-- counting categories but remain ELIGIBLE for upgrade. A motivated_by edge is what a
-- Why answer is actually made of, so it should carry the tier its thread earned; it
-- just must not vote on whether that tier is earned.
--
-- A REJECTED FIFTH CATEGORY. An earlier draft counted "the same issue closed by two or
-- more distinct source artifacts" as independent convergence. It is not: all `closes`
-- edges come from one extractor, so that condition always implies DECLARED and can
-- never contribute an independent signal. Nested, not independent -- the same trap as
-- the synthesis edges, reached through multiplicity instead of derivation. See #12 for
-- the provenance-label work that would make such a category meaningful; it is not a
-- reason to reintroduce it here.

BEGIN;

-- Which edge types can carry evidence at all (rubric clause 1).
CREATE FUNCTION corroboration_eligible_edge_type(p_type edge_type) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT p_type IN ('closes', 'implements', 'reviewed',
                      'motivated_by', 'implemented_by', 'deployed_by');
$$;

-- Per-thread category tally, observed edges only.
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
                     AND e.extractor = 'body_closing_keyword'
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
    'signals already counted here.';

-- Every eligible edge with the rubric resolved. The backfill reads THIS, so the rule
-- is never restated in two places that can drift apart.
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

-- Idempotent, and reversible. It downgrades as well as upgrades, because evidence can
-- be invalidated later -- an edge whose thread loses its third category must lose the
-- tier too, or the tier would record a claim the graph no longer supports.
CREATE FUNCTION apply_corroboration()
RETURNS TABLE (upgraded bigint, downgraded bigint)
LANGUAGE plpgsql AS $$
DECLARE
    v_up   bigint;
    v_down bigint;
BEGIN
    WITH promoted AS (
        UPDATE edge e
        SET evidence_tier = 'corroborated'
        FROM v_edge_corroboration_audit a
        WHERE a.edge_id = e.id
          AND a.qualifies
          AND e.evidence_tier = 'explicit'
        RETURNING e.id
    )
    SELECT count(*) INTO v_up FROM promoted;

    WITH demoted AS (
        UPDATE edge e
        SET evidence_tier = 'explicit'
        FROM v_edge_corroboration_audit a
        WHERE a.edge_id = e.id
          AND NOT a.qualifies
          AND e.evidence_tier = 'corroborated'
          -- Never touch inferred edges: the 0001 CHECK ties their tier to their tag.
          AND e.tag = 'explicit'
        RETURNING e.id
    )
    SELECT count(*) INTO v_down FROM demoted;

    RETURN QUERY SELECT v_up, v_down;
END;
$$;

COMMENT ON FUNCTION apply_corroboration() IS
    'Backfill/refresh of §5.4 corroborated tiers. Idempotent and reversible: safe to '
    'run after every ingestion. Reads v_edge_corroboration_audit so the rubric lives in '
    'exactly one place.';

-- Initial backfill.
SELECT * FROM apply_corroboration();

COMMIT;
