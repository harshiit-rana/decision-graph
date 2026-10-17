-- 0016_edge_retraction.sql
--
-- ADR 0009 (accepted 2026-10-17), issue #121: an edge has TWO different ends.
--
--   valid_to      The repository stopped saying it (event time). An author edited
--                 `Fixes #12` out of a PR body. It was true from valid_from until then.
--   retracted_at  TraceMind stopped believing it (system time). The repository never said it,
--                 as far as we now know. The extractor changed, or an explicit retraction.
--
-- Reusing valid_to for retraction would have made `--as-of T` answer "what we believe now
-- about T" and not "what the repository said at T": an edge retracted today would vanish from
-- a query about last March, when the repository really did say it. So retraction is a column.
--
-- Visibility, as ADR 0009 defines it:
--   current        valid_to IS NULL AND retracted_at IS NULL
--   --as-of T      valid_from <= T AND (valid_to IS NULL OR valid_to > T) AND retracted_at IS NULL
--   --as-recorded  created_at <= R AND (retracted_at IS NULL OR retracted_at > R) AND
--                  valid_from <= T AND (valid_to IS NULL OR valid_to > T OR valid_to_recorded_at > R)
--
-- Audit guarantees, enforced here in the database and not only in calling code:
--   1. No DELETE on edge. Two exceptions: the cascade when a NODE is deleted (withdrawing a
--      Decision removes its edges with it), and a test fixture setting dg.allow_edge_delete = 'on'.
--   2. Retraction is one way and explained: retracted_at is set once, by the database clock,
--      with a non-empty retraction_reason, and a retracted row is frozen afterwards.
--   3. Validity is append-only: valid_from never changes, and valid_to cannot change once set.
--   4. Re-asserting a retracted edge is a NEW row. The retracted one stays as history, because
--      the unique index on current edges excludes retracted rows.
--   5. v_edge_retraction_audit lists every retraction.
--
-- Everything that decides what a "current edge" is is redefined below with the extra
-- predicate: the inferred-edge gate's per-node cap, the Decision rubric, the two corroboration
-- views, and apply_corroboration. Each is the latest definition, copied with only that
-- predicate added. tests/test_edge_retraction.py fails if any "current edge" predicate in the
-- migrations or in src/ lacks it.
--
-- Applied by `dg init`, which snapshots a populated database first (#122).

BEGIN;

ALTER TABLE edge
    ADD COLUMN retracted_at         timestamptz,
    ADD COLUMN retraction_reason    text,
    ADD COLUMN valid_to_recorded_at timestamptz;

-- When did we LEARN each existing validity end? Synthesis wrote valid_to = now(), so the
-- end and the moment it was recorded coincide for everything ended before this migration.
-- The user triggers are off for this one statement: it changes no meaning, and the rubric
-- guard would otherwise revalidate every Decision an ended edge touches.
ALTER TABLE edge DISABLE TRIGGER USER;
UPDATE edge SET valid_to_recorded_at = valid_to WHERE valid_to IS NOT NULL;
ALTER TABLE edge ENABLE TRIGGER USER;

ALTER TABLE edge
    ADD CONSTRAINT edge_retraction_explained CHECK (
        (retracted_at IS NULL) = (retraction_reason IS NULL)
        AND (retraction_reason IS NULL OR btrim(retraction_reason) <> '')),
    ADD CONSTRAINT edge_validity_end_recorded CHECK (
        (valid_to IS NULL) = (valid_to_recorded_at IS NULL));

COMMENT ON COLUMN edge.valid_to IS
    'NULL = currently valid. Set when the REPOSITORY stopped saying this (event time), e.g. a '
    'closing keyword edited out of a PR body. Never changes once set (ADR 0009).';
COMMENT ON COLUMN edge.valid_to_recorded_at IS
    'When TraceMind learned valid_to (system time). Set by trigger. Lets --as-recorded replay an '
    'edit we had not yet seen.';
COMMENT ON COLUMN edge.retracted_at IS
    'When TraceMind stopped believing this edge (system time). Set once, by the database clock, '
    'with retraction_reason. The repository never said it, as far as we now know. A retracted '
    'edge is hidden from current queries and from --as-of, and kept for --as-recorded and audit.';
COMMENT ON COLUMN edge.retraction_reason IS
    'Why it was retracted, in words: which extractor stopped yielding it, or the explicit reason.';

-- ---------------------------------------------------------------------------
-- The guard: the audit guarantees, in the database.
-- ---------------------------------------------------------------------------

CREATE FUNCTION edge_history_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('dg.allow_edge_delete', true) = 'on' THEN
            RETURN OLD;
        END IF;
        -- A node's edges go with the node (ON DELETE CASCADE). Withdrawing a Decision deletes
        -- the Decision node, and that is a node-level operation, not an edge being erased.
        -- During the cascade an endpoint is already gone; a direct DELETE FROM edge leaves
        -- both endpoints in place, and is refused.
        IF NOT EXISTS (SELECT 1 FROM node WHERE id = OLD.src_node_id)
           OR NOT EXISTS (SELECT 1 FROM node WHERE id = OLD.dst_node_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION
            'edges are never deleted (ADR 0009): retract edge % with retract_edge(), or end its validity',
            OLD.id USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.retracted_at IS NOT NULL THEN
            RAISE EXCEPTION 'an edge is created current and retracted later, not created retracted'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.valid_to IS NOT NULL AND NEW.valid_to_recorded_at IS NULL THEN
            NEW.valid_to_recorded_at := now();
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.retracted_at IS NOT NULL THEN
        RAISE EXCEPTION 'edge % was retracted at % and is frozen (ADR 0009)', OLD.id, OLD.retracted_at
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.valid_from IS DISTINCT FROM OLD.valid_from THEN
        RAISE EXCEPTION 'valid_from of edge % cannot change after creation (ADR 0009)', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to THEN
        RAISE EXCEPTION 'valid_to of edge % cannot change once set (ADR 0009)', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL THEN
        NEW.valid_to_recorded_at := now();
    END IF;
    IF NEW.valid_to_recorded_at IS DISTINCT FROM OLD.valid_to_recorded_at AND OLD.valid_to IS NOT NULL THEN
        RAISE EXCEPTION 'valid_to_recorded_at of edge % cannot change once set', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retracted_at IS NOT NULL THEN
        NEW.retracted_at := now();  -- the database clock, never the caller's
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER edge_history_guard
    BEFORE INSERT OR UPDATE OR DELETE ON edge
    FOR EACH ROW EXECUTE FUNCTION edge_history_guard();

-- ---------------------------------------------------------------------------
-- Indexes: a retracted edge no longer holds the "current" slot, so the same edge can be
-- asserted again as a new row.
-- ---------------------------------------------------------------------------

DROP INDEX edge_current_uidx;
DROP INDEX edge_out_idx;
DROP INDEX edge_in_idx;
DROP INDEX edge_inferred_idx;

CREATE UNIQUE INDEX edge_current_uidx ON edge (src_node_id, dst_node_id, edge_type)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
CREATE INDEX edge_out_idx ON edge (src_node_id, edge_type, tag)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
CREATE INDEX edge_in_idx ON edge (dst_node_id, edge_type, tag)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
CREATE INDEX edge_inferred_idx ON edge (src_node_id, dst_node_id)
    WHERE tag = 'inferred' AND valid_to IS NULL AND retracted_at IS NULL;

-- ---------------------------------------------------------------------------
-- Retraction, and the audit view.
-- ---------------------------------------------------------------------------

CREATE FUNCTION retract_edge(p_edge_id bigint, p_reason text) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'retracting an edge needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE edge SET retracted_at = now(), retraction_reason = p_reason
     WHERE id = p_edge_id AND retracted_at IS NULL;
    RETURN FOUND;
END;
$$;

COMMENT ON FUNCTION retract_edge(bigint, text) IS
    'The one way an edge leaves current belief besides ending its validity. Returns false if it '
    'was already retracted or does not exist. Nothing is deleted.';

CREATE VIEW v_edge_retraction_audit AS
SELECT e.id AS edge_id, e.edge_type, e.tag, e.evidence_tier, e.extractor, e.source_ref,
       e.src_node_id, e.dst_node_id, e.created_at, e.observed_at, e.valid_from, e.valid_to,
       e.valid_to_recorded_at, e.retracted_at, e.retraction_reason
  FROM edge e
 WHERE e.retracted_at IS NOT NULL;

COMMENT ON VIEW v_edge_retraction_audit IS
    'Every edge TraceMind retracted, with when and why. The standing record that retraction '
    'erased nothing (ADR 0009).';

-- ---------------------------------------------------------------------------
-- What a "current edge" is, redefined with the retraction predicate. Each is the latest
-- definition, with only that predicate added.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION enforce_inferred_edge_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_min_relevance numeric;
    v_max_per_node  numeric;
    v_endpoint      bigint;
    v_count         bigint;
BEGIN
    IF NEW.tag <> 'inferred' THEN
        RETURN NEW;
    END IF;

    -- Decision nodes are exempt from the inferred path entirely (§5.1): the system
    -- must never assert a decision occurred on the strength of an inferred link.
    IF EXISTS (
        SELECT 1 FROM node
        WHERE id IN (NEW.src_node_id, NEW.dst_node_id)
          AND node_type = 'decision'
    ) THEN
        RAISE EXCEPTION
            'inferred edge may not touch a decision node (src=%, dst=%, type=%)',
            NEW.src_node_id, NEW.dst_node_id, NEW.edge_type
            USING ERRCODE = 'check_violation';
    END IF;

    v_min_relevance := graph_config_num('inferred_edge_min_relevance');

    IF NEW.relevance IS NULL THEN
        RAISE EXCEPTION 'inferred edge requires a relevance score'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.relevance < v_min_relevance THEN
        -- NB: plpgsql RAISE has no printf precision specifiers; % is a bare placeholder.
        RAISE EXCEPTION
            'inferred edge relevance % is below threshold %; no edge is created',
            NEW.relevance, v_min_relevance
            USING ERRCODE = 'check_violation';
    END IF;

    -- Per-node cap, counted over both directions on currently-valid inferred edges.
    v_max_per_node := graph_config_num('inferred_edge_max_per_node');

    FOREACH v_endpoint IN ARRAY ARRAY[NEW.src_node_id, NEW.dst_node_id] LOOP
        SELECT count(*) INTO v_count
        FROM edge e
        WHERE e.tag = 'inferred'
          AND e.valid_to IS NULL AND e.retracted_at IS NULL
          AND e.id IS DISTINCT FROM NEW.id
          AND (e.src_node_id = v_endpoint OR e.dst_node_id = v_endpoint);

        IF v_count >= v_max_per_node THEN
            RAISE EXCEPTION
                'node % already holds % inferred edges (cap %); no edge is created',
                v_endpoint, v_count, v_max_per_node
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$;

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
          AND e.valid_to IS NULL AND e.retracted_at IS NULL
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
      AND e.valid_to IS NULL AND e.retracted_at IS NULL;

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
          AND e.valid_to IS NULL AND e.retracted_at IS NULL
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
              AND e.valid_to IS NULL AND e.retracted_at IS NULL
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

CREATE OR REPLACE VIEW v_thread_corroboration AS
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
                     AND e.tag = 'explicit' AND e.valid_to IS NULL AND e.retracted_at IS NULL
                     AND d.node_type = 'issue'
                     AND s.thread_key = t.thread_key
                     AND d.thread_key = t.thread_key) AS declared,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node s ON s.id = e.src_node_id
                   JOIN node d ON d.id = e.dst_node_id
                   WHERE e.edge_type = 'implements'
                     AND e.extractor = 'pr_commit_list'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL AND e.retracted_at IS NULL
                     AND s.thread_key = t.thread_key
                     AND d.thread_key = t.thread_key) AS structural,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node d ON d.id = e.dst_node_id
                   WHERE e.edge_type = 'reviewed'
                     AND e.extractor = 'pr_review'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL AND e.retracted_at IS NULL
                     AND d.thread_key = t.thread_key) AS attested,

           EXISTS (SELECT 1 FROM edge e
                   JOIN node s ON s.id = e.src_node_id
                   WHERE e.edge_type = 'deployed_by'
                     AND e.extractor = 'release_notes_reference'
                     AND e.tag = 'explicit' AND e.valid_to IS NULL AND e.retracted_at IS NULL
                     AND s.thread_key = t.thread_key) AS published
    FROM threads t
)
SELECT thread_key,
       declared, structural, attested, published,
       (declared::int + structural::int + attested::int + published::int) AS category_count,
       (declared::int + structural::int + attested::int + published::int) >= 3 AS corroborates
FROM categories;

CREATE OR REPLACE VIEW v_edge_corroboration_audit AS
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
           AND e.valid_to IS NULL AND e.retracted_at IS NULL
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

CREATE OR REPLACE FUNCTION apply_corroboration()
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
          AND e.retracted_at IS NULL
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
          AND e.retracted_at IS NULL
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

-- ---------------------------------------------------------------------------
-- Decision withdrawal, with its reason recorded (issue #121).
--
-- A reconstructed Decision is promoted from a `closes` cluster: a thread holding a closing
-- edge from a pull request or commit to an issue (synthesis.CANDIDATE_QUERY). The rubric does
-- not look at that edge, so ending or retracting it never failed the rubric, and the Decision
-- would have stood on evidence the repository no longer states. So a Decision whose thread no
-- longer holds ANY current closing edge is withdrawn too.
--
-- Withdrawal deletes the Decision node, because a reconstructed Decision with no motivation
-- cannot exist (the deferred rubric guard). That would erase the fact it was ever asserted, so
-- every withdrawal is first written to decision_withdrawal: which thread, what it was called,
-- when, and why in words.
-- ---------------------------------------------------------------------------

CREATE TABLE decision_withdrawal (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo_node_id    bigint REFERENCES node (id) ON DELETE SET NULL,
    thread_key      text NOT NULL,
    decision_title  text,
    status          text NOT NULL,
    withdrawn_at    timestamptz NOT NULL DEFAULT now(),
    reason          text NOT NULL CHECK (btrim(reason) <> '')
);

COMMENT ON TABLE decision_withdrawal IS
    'Every Decision the graph asserted and later withdrew, with when and why. The node is '
    'deleted (a reconstructed Decision cannot exist without its evidence); this row is what '
    'remains, so withdrawal erases the Decision but not the record that it existed.';

CREATE OR REPLACE FUNCTION retract_unsupported_decisions()
RETURNS TABLE (retracted bigint, thread_keys text[])
LANGUAGE plpgsql AS $$
DECLARE
    v_ids     bigint[];
    v_threads text[];
BEGIN
    CREATE TEMP TABLE _withdraw ON COMMIT DROP AS
    SELECT a.node_id, n.thread_key, n.repo_node_id, a.title, a.status::text AS status,
           CASE WHEN NOT a.passes
                THEN 'no longer satisfies the reconstructed rubric: ' || COALESCE(a.failure_reason, 'unspecified')
                ELSE 'its thread no longer holds a current closing-keyword edge from a pull '
                     'request or commit to an issue, which is the basis it was promoted on'
           END AS reason
      FROM v_decision_rubric_audit a
      JOIN node n ON n.id = a.node_id
     WHERE a.status = 'reconstructed'
       AND (NOT a.passes
            OR NOT EXISTS (
                SELECT 1
                  FROM edge e
                  JOIN node s ON s.id = e.src_node_id
                  JOIN node d ON d.id = e.dst_node_id
                 WHERE e.edge_type = 'closes' AND e.tag = 'explicit'
                   AND e.valid_to IS NULL AND e.retracted_at IS NULL
                   AND d.node_type = 'issue'
                   AND s.thread_key = n.thread_key AND d.thread_key = n.thread_key));

    SELECT array_agg(node_id), array_agg(thread_key) INTO v_ids, v_threads FROM _withdraw;

    IF v_ids IS NULL THEN
        DROP TABLE _withdraw;
        RETURN QUERY SELECT 0::bigint, ARRAY[]::text[];
        RETURN;
    END IF;

    INSERT INTO decision_withdrawal (repo_node_id, thread_key, decision_title, status, reason)
    SELECT repo_node_id, thread_key, title, status, reason FROM _withdraw;

    DELETE FROM node WHERE id = ANY(v_ids);
    DROP TABLE _withdraw;

    RETURN QUERY SELECT array_length(v_ids, 1)::bigint, v_threads;
END;
$$;

COMMENT ON FUNCTION retract_unsupported_decisions() IS
    'Withdraws reconstructed Decisions that no longer satisfy the section 5.1 rubric, or whose '
    'thread no longer holds the closing edge they were promoted on (issue #121). Every '
    'withdrawal is recorded in decision_withdrawal with its reason first. Run after any rule '
    'change or evidence invalidation.';

COMMIT;
