-- 0015: the comment detail table (issue #106). 0014 added the enum value it uses.
--
-- Shaped like every other artifact: a row in `node` carrying identity, a detail table
-- carrying the body, a `created` edge from the author, and `discussed_in` to the artifact
-- it sits on. `discussed_in` has existed in the edge enum since 0001 and has never had a
-- producer; `render.phrase` already reads it as "was discussed in" / "hosted discussion
-- of", so a trace that crosses one already has words for it.
--
-- DIRECTION. The edge runs artifact -> comment, so it reads "issue #5895 was discussed in
-- comment ...". That is the way a Why-walk wants to travel: from the thing you asked about
-- toward what was said about it, not the reverse.
--
-- NOT IN THE RUBRIC. `decision_rubric` is untouched by this file, and no comment edge
-- carries a tier other than `explicit`-as-provenance. The rubric's Validation clause still
-- requires a merge, so ingesting discussion cannot move a Decision, and `v_thread_
-- corroboration` counts the four signals it always counted. That is deliberate: a comment
-- is evidence a reader can weigh, not evidence the system is entitled to weigh for them.

BEGIN;

CREATE TABLE comment (
    node_id        bigint PRIMARY KEY REFERENCES node (id) ON DELETE CASCADE,
    node_type      node_type NOT NULL DEFAULT 'comment' CHECK (node_type = 'comment'),

    -- The artifact this was written on. A real reference rather than the issue number,
    -- because a comment's own external_id is the GitHub comment id and nothing else in
    -- the graph is keyed by "the number this belongs to".
    parent_node_id bigint NOT NULL REFERENCES node (id) ON DELETE CASCADE,

    body           text,
    created_at     timestamptz,

    -- GitHub's own word for how the author stands to the project: OWNER, MEMBER,
    -- COLLABORATOR, CONTRIBUTOR, NONE. Stored because it is the one structured signal on a
    -- comment that bears on whether a refusal was a maintainer's decision or a bystander's
    -- opinion -- and stored rather than interpreted, for the same reason `state_reason` is.
    author_association text,

    FOREIGN KEY (node_id, node_type) REFERENCES node (id, node_type)
);

CREATE INDEX comment_parent_idx ON comment (parent_node_id);

-- The closing discussion is read by recency, so this is the access path that matters.
CREATE INDEX comment_parent_created_idx ON comment (parent_node_id, created_at DESC);

COMMENT ON TABLE comment IS
    'An issue or pull request comment, ingested as an artifact only. It creates no Decision '
    'and satisfies no rubric clause: comment prose is not one of the structured signals '
    '5.1 reads, and the PRD roadmap note is explicit that asserting a rejected outcome '
    'without rejection-rationale extraction would assert a decision the system cannot '
    'evidence. Sampled closing comments bear that out -- a reporter withdrawing, a '
    'duplicate, an already-fixed bug. This table lets a reader be shown what was said; it '
    'does not let the graph conclude anything from it.';

COMMENT ON COLUMN comment.author_association IS
    'GitHub OWNER | MEMBER | COLLABORATOR | CONTRIBUTOR | NONE. Recorded, not interpreted: '
    'it distinguishes a maintainer closing a request from a bystander commenting on it, '
    'which is the difference between a decision and an opinion -- but the graph stores the '
    'association and leaves the reading to whoever is looking.';

COMMIT;
