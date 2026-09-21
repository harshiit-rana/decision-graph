-- 0014: `comment` becomes a node type (issue #106).
--
-- Split from 0015, which creates the table, because Postgres will not let a newly added
-- enum value be USED in the transaction that added it:
--
--     ERROR:  unsafe use of new value "comment" of enum type node_type
--     HINT:   New enum values must be committed before they can be used.
--
-- The detail-table pattern every artifact follows -- `node_type node_type NOT NULL DEFAULT
-- 'comment' CHECK (node_type = 'comment')` -- is exactly such a use, so the value has to be
-- committed first. Each migration file opens its own transaction and the runner leaves them
-- to it, so two files is the whole of the fix.
--
-- WHY A COMMENT IS AN ARTIFACT AND NOT A DECISION SIGNAL
--
-- §5.1 builds Decisions from structured signals: `closes`, `implements`, `reviewed`, and a
-- merge. Comment prose is none of those, and the PRD roadmap note is explicit that adding a
-- `rejected` outcome without rejection-rationale extraction "would reintroduce exactly the
-- failure §5.1 exists to prevent -- asserting a decision whose rationale the system cannot
-- evidence". Sampling flask's closing comments shows why that caution is right: "ok, my
-- bad" is a reporter withdrawing, "Duplicate of ..." is triage, "it's fixed in main" is
-- already-done. Most `not_planned` closures are not decisions at all.
--
-- So comments land as artifacts a reader can be shown and nothing more. They change no
-- rubric clause, create no Decision, and carry no evidence tier of their own. What they
-- give is the thing #86 had to say was missing: when a refusal says "the closing discussion
-- is not ingested", it need not stay true forever.

BEGIN;

ALTER TYPE node_type ADD VALUE IF NOT EXISTS 'comment';

COMMIT;
