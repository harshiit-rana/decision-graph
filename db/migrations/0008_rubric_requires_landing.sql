-- 0008_rubric_requires_landing.sql
-- Correct an under-implementation of §5.1 Validation (issue #17).
--
-- The rubric always defined Validation as "that it landed". It was implemented as
-- "a `reviewed` or `closes` edge exists" -- but a closing keyword is typed by a
-- contributor BEFORE the outcome is known and survives rejection completely intact.
-- The result: 12 of 20 Decisions rested on pull requests that were never merged, and
-- 4 were motivated by issues the maintainers closed as `not_planned`. The system
-- asserted that a decision was taken where the actual decision was to refuse.
--
-- WHY THIS ALSO GATES IMPLEMENTATION, not only Validation.
-- Rubric clause 2 is an OR: Motivation plus *either* Implementation or Validation. All
-- 20 existing Decisions satisfy BOTH, so tightening Validation alone would have changed
-- nothing -- every rejected cluster would still qualify through Implementation. Gating
-- only one branch of an OR is inert.
--
-- Gating both is faithful to the PRD rather than an extension of it. §5.1's own worked
-- example reads: "a merged, reviewed PR with no linked issue describes *that* something
-- happened and *that* it landed, but not *why*". The rubric's picture of Implementation
-- was always a MERGED pull request; the code simply never checked.
--
-- Motivation is deliberately left alone. An issue closed as `not_planned` is excluded
-- as a side effect -- all four such clusters contain no merged PR at all -- so no
-- separate rule is needed, and none is added. Modelling "decided not to do this" as a
-- first-class Decision outcome is a Phase 5+ item: it needs rejection-rationale
-- extraction that does not exist yet.

BEGIN;

CREATE OR REPLACE FUNCTION decision_rubric(p_decision_id bigint)
RETURNS TABLE (
    decision_node_id   bigint,
    has_motivation     boolean,
    has_implementation boolean,
    has_validation     boolean,
    cluster_thread_key text,
    thread_coherent    boolean,
    passes             boolean,
    failure_reason     text
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_motivation boolean;
    v_impl       boolean;
    v_validation boolean;
    v_threads    text[];
    v_thread     text;
    v_coherent   boolean;
    v_landed     boolean;
    v_passes     boolean;
    v_reason     text;
BEGIN
    -- Clause 1: Motivation -- mandatory, unchanged. Answers "why".
    SELECT EXISTS (
        SELECT 1
        FROM edge e
        JOIN node n ON n.id = e.dst_node_id
        WHERE e.src_node_id = p_decision_id
          AND e.edge_type   = 'motivated_by'
          AND e.tag         = 'explicit'
          AND e.valid_to IS NULL
          AND n.node_type IN ('issue', 'pull_request', 'wiki_page')
    ) INTO v_motivation;

    -- Clause 3 first: the thread cluster, which the landing check needs.
    SELECT array_agg(DISTINCT n.thread_key)
    INTO v_threads
    FROM edge e
    JOIN node n ON n.id = e.dst_node_id
    WHERE e.src_node_id = p_decision_id
      AND e.edge_type IN ('motivated_by', 'implemented_by')
      AND e.tag = 'explicit'
      AND e.valid_to IS NULL;

    v_coherent := v_threads IS NOT NULL
                  AND array_length(v_threads, 1) = 1
                  AND v_threads[1] IS NOT NULL;
    v_thread := CASE WHEN v_coherent THEN v_threads[1] END;

    -- Did anything in this cluster actually merge?
    v_landed := thread_landed(v_thread);

    -- Clause 2a: Implementation. Answers "what was done" -- and it must have landed.
    SELECT v_landed AND EXISTS (
        SELECT 1
        FROM edge e
        JOIN node n ON n.id = e.dst_node_id
        WHERE e.src_node_id = p_decision_id
          AND e.edge_type   = 'implemented_by'
          AND e.tag         = 'explicit'
          AND e.valid_to IS NULL
          AND n.node_type IN ('commit', 'pull_request')
    ) INTO v_impl;

    -- Clause 2b: Validation. Answers "that it landed" -- now actually checked.
    IF v_thread IS NULL OR NOT v_landed THEN
        v_validation := false;
    ELSE
        SELECT EXISTS (
            SELECT 1
            FROM edge e
            JOIN node s ON s.id = e.src_node_id
            JOIN node d ON d.id = e.dst_node_id
            WHERE e.edge_type IN ('reviewed', 'closes')
              AND e.tag = 'explicit'
              AND e.valid_to IS NULL
              AND (s.thread_key = v_thread OR d.thread_key = v_thread)
        ) INTO v_validation;
    END IF;

    v_passes := v_motivation AND (v_impl OR v_validation) AND v_coherent;

    v_reason := CASE
        WHEN v_passes THEN NULL
        WHEN NOT v_motivation THEN
            'clause 1: no explicit motivated_by edge to an issue/PR/wiki page'
        WHEN NOT v_coherent THEN
            'clause 3: rubric edges span ' || COALESCE(array_length(v_threads, 1), 0) ||
            ' thread(s) or an unthreaded node'
        WHEN NOT v_landed THEN
            'clause 2: nothing in thread ' || COALESCE(v_thread, '<none>') ||
            ' was ever merged -- a closing keyword survives rejection, a merge does not'
        ELSE
            'clause 2: neither implemented_by nor a reviewed/closes edge in thread ' ||
            COALESCE(v_thread, '<none>')
    END;

    RETURN QUERY SELECT
        p_decision_id, v_motivation, v_impl, v_validation,
        v_thread, v_coherent, v_passes, v_reason;
END;
$$;

-- Retraction. A Decision asserted under the old rule must be withdrawn, not left
-- standing: the deferred guard only re-validates rows it is asked to touch, so nothing
-- would otherwise revisit the 12 clusters that no longer qualify.
--
-- Same principle as apply_corroboration() being reversible -- if evidence stops
-- supporting a claim, the claim goes. Deleting the node cascades its edges.
CREATE FUNCTION retract_unsupported_decisions()
RETURNS TABLE (retracted bigint, thread_keys text[])
LANGUAGE plpgsql AS $$
DECLARE
    v_ids     bigint[];
    v_threads text[];
BEGIN
    SELECT array_agg(a.node_id), array_agg(n.thread_key)
    INTO v_ids, v_threads
    FROM v_decision_rubric_audit a
    JOIN node n ON n.id = a.node_id
    WHERE a.status = 'reconstructed' AND NOT a.passes;

    IF v_ids IS NULL THEN
        RETURN QUERY SELECT 0::bigint, ARRAY[]::text[];
        RETURN;
    END IF;

    DELETE FROM node WHERE id = ANY(v_ids);

    RETURN QUERY SELECT array_length(v_ids, 1)::bigint, v_threads;
END;
$$;

COMMENT ON FUNCTION retract_unsupported_decisions() IS
    'Withdraws reconstructed Decisions that no longer satisfy the §5.1 rubric. Run after '
    'any rule change or evidence invalidation -- a Decision the graph no longer supports '
    'must not survive merely because nothing happened to touch its row.';

COMMIT;
