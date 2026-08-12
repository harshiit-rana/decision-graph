-- 0001_core_graph.sql
-- Organizational Intelligence Engine — Phase 1, Engine 1 (Knowledge Graph), PRD v3.1 §5.1
--
-- Shape: node registry + per-type detail tables + ONE adjacency-list edge table.
--   * node            — identity/registry row for every entity, one row per artifact
--   * <entity> tables — type-specific columns, 1:1 with node via composite FK (id, node_type)
--   * edge            — adjacency list: (src_node_id, dst_node_id, edge_type, tag, time window)
--
-- Why one edge table instead of one table per relationship type: Engine 2 (§5.3) is a single
-- traversal implementation over 15 edge types with a two-pass explicit-then-inferred order.
-- Per-type tables would force a 15-way UNION into every recursive CTE. The composite FK on
-- (id, node_type) keeps full referential integrity without that cost.

BEGIN;

-- Trigram index support for the minimal fuzzy entity lookup in Engine 1.5 (§5.2).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------------

-- §5.1 Entities (v1, GitHub-scoped)
CREATE TYPE node_type AS ENUM (
    'repository',
    'commit',
    'pull_request',
    'branch',
    'issue',
    'release',
    'person',
    'team',
    'codeowners_scope',
    'workflow',
    'wiki_page',
    'decision'
);

-- §5.1 Relationships, including the three Decision-specific edges
CREATE TYPE edge_type AS ENUM (
    'references',
    'created',
    'reviewed',
    'owns',
    'depends_on',
    'discussed_in',
    'implements',
    'closes',
    'deployed_by',
    'supersedes',
    'mentions',
    'relates_to',
    -- Decision-specific
    'motivated_by',
    'implemented_by',
    'superseded_by'
);

-- §5.1 Construction principle: provenance at creation time. This is the source of truth
-- for how an edge came to exist, and it never changes after insert.
CREATE TYPE edge_tag AS ENUM (
    'explicit',   -- built from a signal present in the GitHub data
    'inferred'    -- proposed by LLM inference; gated by threshold + per-node cap below
);

-- §5.4 Evidence tier, surfaced with every answer. DERIVED, not provenance:
-- 'corroborated' is an upgrade applied to an explicit edge once multiple independent
-- explicit signals converge on it. Phase 3 owns that upgrade; Phase 1 writes
-- 'explicit'/'inferred' only. See the tag/tier invariant on edge below.
CREATE TYPE evidence_tier AS ENUM (
    'explicit',
    'corroborated',
    'inferred'
);

-- §5.1 Decision status. There is deliberately NO 'inferred' member: a Decision that
-- cannot reach 'explicit' or 'reconstructed' is not created at all. Adding a third
-- member here would be a PRD violation, not a schema change.
CREATE TYPE decision_status AS ENUM (
    'explicit',
    'reconstructed'
);

-- ---------------------------------------------------------------------------
-- Runtime configuration (§5.1 inferred-edge gating)
-- ---------------------------------------------------------------------------

CREATE TABLE graph_config (
    key   text PRIMARY KEY,
    value numeric NOT NULL,
    note  text NOT NULL
);

INSERT INTO graph_config (key, value, note) VALUES
    ('inferred_edge_min_relevance', 0.75,
     'V1 DEFAULT (revisit): §5.1 relevance threshold. Below this, no edge is created at all — '
     'an absent answer is preferred over a weak inferred one. Tune against the §9 evaluation set.'),
    ('inferred_edge_max_per_node', 4,
     'V1 DEFAULT (revisit): §5.1 per-node cap, stated as 3-5. Counted across BOTH directions '
     'over currently-valid edges. Prevents a long tail of speculative connections on one node.');

CREATE FUNCTION graph_config_num(p_key text) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT value FROM graph_config WHERE key = p_key;
$$;

-- ---------------------------------------------------------------------------
-- Node registry
-- ---------------------------------------------------------------------------

CREATE TABLE node (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node_type         node_type NOT NULL,

    -- Owning repository. NULL only for repository nodes themselves and for
    -- person/team nodes, which are org-scoped rather than repo-scoped.
    repo_node_id      bigint REFERENCES node (id) ON DELETE CASCADE,

    -- Stable identifier within (repo, type): commit sha, PR/issue number,
    -- login, branch name, workflow path, release tag, wiki page path.
    external_id       text NOT NULL,

    -- GitHub's own global node id, when the API gives us one. Nullable because
    -- synthesized nodes (decision, codeowners_scope) have no GitHub identity.
    github_node_id    text,

    title             text,
    url               text,

    -- §5.1 bounded time window for the reconstructed rubric, v1 form.
    -- See COMMENT below — this is the single most likely thing to be revisited.
    thread_key        text,

    -- When the artifact came into existence in GitHub (not when we ingested it).
    source_created_at timestamptz,
    source_updated_at timestamptz,

    -- Our own bookkeeping.
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_polled_at    timestamptz NOT NULL DEFAULT now(),

    raw               jsonb,

    -- Required so detail tables can carry a composite FK that pins node_type.
    UNIQUE (id, node_type)
);

COMMENT ON COLUMN node.thread_key IS
    'V1 DEFAULT (revisit): stands in for the "bounded time window" in §5.1 rubric clause 3. '
    'A thread is one PR/issue conversation cluster: a PR, the issue(s) it closes, its commits, '
    'and its reviews all share one key (format: thread:<repo_node_id>:pr-<n> or :issue-<n>). '
    'Ingestion assigns it union-find style at extraction time. Chosen over a literal time '
    'window because "same thread" is mechanically checkable and has no tunable magic number; '
    'the cost is that a decision genuinely spanning two threads cannot reach reconstructed status.';

-- Identity is (type, repo, external_id). COALESCE keeps repo-less nodes unique too.
CREATE UNIQUE INDEX node_identity_uidx
    ON node (node_type, COALESCE(repo_node_id, 0), external_id);

CREATE INDEX node_thread_key_idx  ON node (thread_key) WHERE thread_key IS NOT NULL;
CREATE INDEX node_repo_type_idx   ON node (repo_node_id, node_type);
CREATE INDEX node_github_id_idx   ON node (github_node_id) WHERE github_node_id IS NOT NULL;
-- Supports Engine 1.5 (§5.2) fuzzy entity lookup without committing to an index type
-- beyond what a trivial name match needs.
CREATE INDEX node_title_trgm_idx  ON node USING gin (title gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Per-entity detail tables
--
-- Each pins its node_type via a CHECK + composite FK, so a row in `commit`
-- can only ever point at a node whose node_type is 'commit'.
-- ---------------------------------------------------------------------------

CREATE TABLE repository (
    node_id        bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type      node_type NOT NULL DEFAULT 'repository' CHECK (node_type = 'repository'),
    owner          text NOT NULL,
    name           text NOT NULL,
    default_branch text,
    visibility     text,
    UNIQUE (owner, name),
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE commit (
    node_id      bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type    node_type NOT NULL DEFAULT 'commit' CHECK (node_type = 'commit'),
    sha          text NOT NULL,
    message      text,
    authored_at  timestamptz,
    committed_at timestamptz,
    additions    integer,
    deletions    integer,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE pull_request (
    node_id          bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type        node_type NOT NULL DEFAULT 'pull_request' CHECK (node_type = 'pull_request'),
    number           integer NOT NULL,
    state            text NOT NULL,
    body             text,
    head_ref         text,
    base_ref         text,
    merge_commit_sha text,
    merged_at        timestamptz,
    closed_at        timestamptz,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE branch (
    node_id    bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type  node_type NOT NULL DEFAULT 'branch' CHECK (node_type = 'branch'),
    name       text NOT NULL,
    head_sha   text,
    is_default boolean NOT NULL DEFAULT false,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE issue (
    node_id   bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type node_type NOT NULL DEFAULT 'issue' CHECK (node_type = 'issue'),
    number    integer NOT NULL,
    state     text NOT NULL,
    body      text,
    closed_at timestamptz,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE release (
    node_id      bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type    node_type NOT NULL DEFAULT 'release' CHECK (node_type = 'release'),
    tag_name     text NOT NULL,
    body         text,
    is_prerelease boolean NOT NULL DEFAULT false,
    published_at timestamptz,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE person (
    node_id   bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type node_type NOT NULL DEFAULT 'person' CHECK (node_type = 'person'),
    login     text NOT NULL UNIQUE,
    name      text,
    email     text,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE team (
    node_id   bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type node_type NOT NULL DEFAULT 'team' CHECK (node_type = 'team'),
    org       text NOT NULL,
    slug      text NOT NULL,
    name      text,
    UNIQUE (org, slug),
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

-- One row per CODEOWNERS rule line. `owns` edges run from person/team to this scope.
CREATE TABLE codeowners_scope (
    node_id      bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type    node_type NOT NULL DEFAULT 'codeowners_scope'
                 CHECK (node_type = 'codeowners_scope'),
    path_pattern text NOT NULL,
    source_path  text NOT NULL,   -- which CODEOWNERS file
    source_sha   text,            -- blob sha the rule was read from
    line_number  integer,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE workflow (
    node_id   bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type node_type NOT NULL DEFAULT 'workflow' CHECK (node_type = 'workflow'),
    path      text NOT NULL,
    name      text,
    state     text,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE TABLE wiki_page (
    node_id   bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type node_type NOT NULL DEFAULT 'wiki_page' CHECK (node_type = 'wiki_page'),
    path      text NOT NULL,
    body      text,
    revision  text,
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

-- §5.1 Decision. Never created from inference alone; see 0002 for the enforced rubric.
CREATE TABLE decision (
    node_id      bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type    node_type NOT NULL DEFAULT 'decision' CHECK (node_type = 'decision'),
    status       decision_status NOT NULL,
    summary      text,
    decided_at   timestamptz,

    -- For status='explicit': the formal artifact (ADR, RFC, release note, explicit
    -- issue resolution) that records the decision. Mandatory — an explicit Decision
    -- without a formal artifact is a contradiction in terms.
    source_artifact_node_id bigint REFERENCES node (id) ON DELETE RESTRICT,

    -- For status='reconstructed': the thread the rubric was satisfied within.
    -- Populated by the rubric guard in 0002; NULL for explicit decisions.
    rubric_thread_key text,

    CONSTRAINT decision_explicit_needs_artifact
        CHECK (status <> 'explicit' OR source_artifact_node_id IS NOT NULL),
    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

-- ---------------------------------------------------------------------------
-- Edge (adjacency list)
-- ---------------------------------------------------------------------------

CREATE TABLE edge (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_node_id   bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,
    dst_node_id   bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,
    edge_type     edge_type NOT NULL,

    -- §5.1 provenance, set at creation, never rewritten.
    tag           edge_tag NOT NULL,
    -- §5.4 presentation tier, derived; Phase 3 may upgrade explicit -> corroborated.
    evidence_tier evidence_tier NOT NULL,

    -- Which extraction rule produced this edge (e.g. 'commit_message_closes_ref',
    -- 'codeowners_parse', 'llm_similarity_v1'). Makes every edge auditable back to
    -- the rule that made it, which §7 Explainability needs.
    extractor     text NOT NULL,
    -- Pointer to the concrete signal: comment id, commit sha, CODEOWNERS line, etc.
    source_ref    text,

    -- Only meaningful for tag='inferred'; required there by the gate trigger.
    relevance     numeric(5,4) CHECK (relevance IS NULL OR (relevance >= 0 AND relevance <= 1)),

    -- §5.1 time-versioning. Required at schema level from v1 — point-in-time queries
    -- depend on it and retrofitting would mean a schema rebuild.
    created_at    timestamptz NOT NULL DEFAULT now(),  -- when WE created the edge
    observed_at   timestamptz,                          -- when the signal occurred in GitHub
    valid_from    timestamptz NOT NULL DEFAULT now(),
    valid_to      timestamptz,                          -- NULL = currently valid

    CONSTRAINT edge_no_self_loop CHECK (src_node_id <> dst_node_id),
    CONSTRAINT edge_valid_window CHECK (valid_to IS NULL OR valid_to > valid_from),

    -- The tag/tier invariant: an inferred edge is exactly an inferred-tier edge.
    -- Only explicit edges are eligible for the 'corroborated' upgrade, so inference
    -- can never launder itself into a higher tier downstream (§5.1, §5.4).
    CONSTRAINT edge_tag_tier_invariant
        CHECK ((tag = 'inferred') = (evidence_tier = 'inferred'))
);

-- At most one currently-valid edge of a given type between two nodes.
-- Superseded edges keep their history via valid_to.
CREATE UNIQUE INDEX edge_current_uidx
    ON edge (src_node_id, dst_node_id, edge_type)
    WHERE valid_to IS NULL;

-- Traversal indexes: Engine 2 walks forward and backward, filtered by tag first (§5.3).
CREATE INDEX edge_out_idx ON edge (src_node_id, edge_type, tag) WHERE valid_to IS NULL;
CREATE INDEX edge_in_idx  ON edge (dst_node_id, edge_type, tag) WHERE valid_to IS NULL;
CREATE INDEX edge_inferred_idx ON edge (src_node_id, dst_node_id)
    WHERE tag = 'inferred' AND valid_to IS NULL;

-- ---------------------------------------------------------------------------
-- §5.1 Inferred-edge gate: relevance threshold + per-node cap + Decision exemption.
--
-- Enforced in the database, not only in the ingestion code, so the bound holds for
-- backfills, manual fixes, and anything Phase 5 adds later.
-- ---------------------------------------------------------------------------

CREATE FUNCTION enforce_inferred_edge_gate() RETURNS trigger
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
          AND e.valid_to IS NULL
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

CREATE TRIGGER edge_inferred_gate
    BEFORE INSERT OR UPDATE ON edge
    FOR EACH ROW EXECUTE FUNCTION enforce_inferred_edge_gate();

-- ---------------------------------------------------------------------------
-- Ingestion bookkeeping (§8 scheduled polling)
-- ---------------------------------------------------------------------------

CREATE TABLE ingestion_run (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo_node_id   bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'succeeded', 'failed')),
    nodes_upserted integer NOT NULL DEFAULT 0,
    edges_upserted integer NOT NULL DEFAULT 0,
    error          text
);

-- Per-resource poll cursors, so each scheduled run is incremental.
CREATE TABLE ingestion_cursor (
    repo_node_id  bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,
    resource      text NOT NULL,  -- 'issues' | 'pulls' | 'commits' | 'reviews' | 'releases' | 'codeowners' | 'workflows' | 'wiki'
    last_since    timestamptz,
    last_etag     text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_node_id, resource)
);

COMMIT;
