-- 0002_decision_rubric.sql
-- The reconstructed-Decision rubric from PRD v3.1 §5.1, encoded as executable logic.
--
-- Three artifacts, one rule:
--   decision_rubric(id)        — the rubric as a QUERY: returns each clause's result
--   v_decision_rubric_audit    — the same, over every Decision node, for inspection
--   decision_rubric_guard      — the same, as a deferred CONSTRAINT: a reconstructed
--                                Decision that fails the rubric cannot survive COMMIT
--
-- The guard is deferred because a Decision node and its edges are necessarily written
-- in the same transaction: the node must exist before its motivated_by edge can point
-- at it. Checking at COMMIT is what makes "the rubric" and "the constraint" the same
-- thing rather than two drifting copies.
--
-- Rubric clauses (§5.1):
--   1. Motivation is MANDATORY  — motivated_by -> issue / RFC / discussion
--   2. AND at least one of      — implemented_by -> commit/PR  (Implementation)
--                               — reviewed / closes           (Validation)
--   3. AND all edges reference the same PR/issue thread cluster
--
-- Every clause reads only explicit, currently-valid edges. Inferred edges cannot
-- participate in the rubric at all — enforced independently in 0001 by the gate
-- trigger, which forbids any inferred edge touching a decision node.

BEGIN;

-- ---------------------------------------------------------------------------
-- The rubric, as a query
-- ---------------------------------------------------------------------------

CREATE FUNCTION decision_rubric(p_decision_id bigint)
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
    v_motivation  boolean;
    v_impl        boolean;
    v_validation  boolean;
    v_threads     text[];
    v_thread      text;
    v_coherent    boolean;
    v_passes      boolean;
    v_reason      text;
BEGIN
    -- Clause 1: Motivation — mandatory. Answers "why".
    -- An RFC or design discussion lives as an issue, a PR description, or a wiki page
    -- in a GitHub-only graph, so all three are accepted motivation targets.
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

    -- Clause 2a: Implementation. Answers "what was done".
    SELECT EXISTS (
        SELECT 1
        FROM edge e
        JOIN node n ON n.id = e.dst_node_id
        WHERE e.src_node_id = p_decision_id
          AND e.edge_type   = 'implemented_by'
          AND e.tag         = 'explicit'
          AND e.valid_to IS NULL
          AND n.node_type IN ('commit', 'pull_request')
    ) INTO v_impl;

    -- Clause 3: thread coherence — the v1 bounded window.
    -- Collect the thread_key of every node this Decision's rubric edges point at.
    -- Coherent means: exactly one distinct key, and it is not NULL.
    -- (array_agg keeps NULLs, so a mix of thread and no-thread yields length 2
    -- and correctly fails.)
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

    -- Clause 2b: Validation. Answers "that it landed".
    -- Unlike motivation/implementation, review and closure edges do not hang off the
    -- Decision node — they run person->PR and PR->issue. So validation is satisfied by
    -- a reviewed/closes edge anywhere inside the Decision's own thread, which is
    -- exactly what clause 3 has already pinned down.
    IF v_thread IS NULL THEN
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
            'clause 1: no explicit motivated_by edge to an issue/PR/wiki page — a Decision '
            'exists to answer "why", and Implementation + Validation alone do not'
        WHEN NOT v_coherent THEN
            'clause 3: rubric edges span ' || COALESCE(array_length(v_threads, 1), 0) ||
            ' thread(s) or an unthreaded node; all must share one PR/issue thread'
        ELSE
            'clause 2: motivated_by present but neither implemented_by nor a '
            'reviewed/closes edge in thread ' || COALESCE(v_thread, '<none>')
    END;

    RETURN QUERY SELECT
        p_decision_id, v_motivation, v_impl, v_validation,
        v_thread, v_coherent, v_passes, v_reason;
END;
$$;

COMMENT ON FUNCTION decision_rubric(bigint) IS
    'PRD v3.1 §5.1 reconstructed-Decision rubric. Mechanical: reads only edge types and '
    'thread membership, no model judgment, no scoring. Applies to status=reconstructed; '
    'status=explicit is backed by a formal artifact instead (see decision.source_artifact_node_id).';

-- ---------------------------------------------------------------------------
-- The rubric, as an inspectable view over the whole graph
-- ---------------------------------------------------------------------------

CREATE VIEW v_decision_rubric_audit AS
SELECT
    d.node_id,
    n.title,
    d.status,
    r.has_motivation,
    r.has_implementation,
    r.has_validation,
    r.cluster_thread_key,
    r.thread_coherent,
    r.passes,
    r.failure_reason
FROM decision d
JOIN node n ON n.id = d.node_id
CROSS JOIN LATERAL decision_rubric(d.node_id) r;

COMMENT ON VIEW v_decision_rubric_audit IS
    'Every Decision node with each rubric clause resolved. Expected invariant: no row '
    'with status = ''reconstructed'' AND passes = false. Use as the standing check.';

-- ---------------------------------------------------------------------------
-- The rubric, as an enforced constraint
-- ---------------------------------------------------------------------------

CREATE FUNCTION assert_decision_rubric(p_decision_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_status decision_status;
    r        record;
BEGIN
    SELECT status INTO v_status FROM decision WHERE node_id = p_decision_id;

    -- Decision was deleted later in the same transaction; nothing to enforce.
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- Explicit decisions are backed by a formal artifact, checked by
    -- decision_explicit_needs_artifact in 0001. The rubric is for reconstructed only.
    IF v_status <> 'reconstructed' THEN
        RETURN;
    END IF;

    SELECT * INTO r FROM decision_rubric(p_decision_id);

    IF NOT r.passes THEN
        RAISE EXCEPTION
            'decision % fails the reconstructed rubric (§5.1): %',
            p_decision_id, r.failure_reason
            USING ERRCODE   = 'check_violation',
                  DETAIL    = format(
                      'motivation=%s implementation=%s validation=%s thread=%s',
                      r.has_motivation, r.has_implementation,
                      r.has_validation, COALESCE(r.cluster_thread_key, '<incoherent>')),
                  HINT      = 'If the bar is not met, create no Decision node — the '
                              'underlying PR/issue/commits remain queryable as plain artifacts.';
    END IF;

    -- Record the thread the rubric was satisfied within, for §7 Explainability.
    UPDATE decision
    SET rubric_thread_key = r.cluster_thread_key
    WHERE node_id = p_decision_id
      AND rubric_thread_key IS DISTINCT FROM r.cluster_thread_key;
END;
$$;

-- Fires when the Decision itself changes.
CREATE FUNCTION trg_decision_rubric_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM assert_decision_rubric(NEW.node_id);
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER decision_rubric_guard
    AFTER INSERT OR UPDATE ON decision
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION trg_decision_rubric_guard();

-- Fires when an edge changes, in case that edge is what a Decision's status rests on.
-- Without this, a reconstructed Decision could be stranded by deleting its motivated_by
-- edge — the exact failure mode the rubric exists to prevent.
CREATE FUNCTION trg_edge_decision_rubric_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_ids bigint[];
    v_id  bigint;
BEGIN
    v_ids := ARRAY(
        SELECT DISTINCT x FROM unnest(ARRAY[
            CASE WHEN TG_OP <> 'DELETE' THEN NEW.src_node_id END,
            CASE WHEN TG_OP <> 'DELETE' THEN NEW.dst_node_id END,
            CASE WHEN TG_OP <> 'INSERT' THEN OLD.src_node_id END,
            CASE WHEN TG_OP <> 'INSERT' THEN OLD.dst_node_id END
        ]) AS x WHERE x IS NOT NULL
    );

    FOREACH v_id IN ARRAY v_ids LOOP
        IF EXISTS (SELECT 1 FROM decision WHERE node_id = v_id) THEN
            PERFORM assert_decision_rubric(v_id);
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER edge_decision_rubric_guard
    AFTER INSERT OR UPDATE OR DELETE ON edge
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION trg_edge_decision_rubric_guard();

COMMIT;
