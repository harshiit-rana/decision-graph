# Architecture decision records

Each record separates three things that the rest of this repository is careful to keep
apart, for the same reason: **facts** (checked against the code or the GitHub API, with
where), **hypotheses** (plausible, unmeasured), and the **decision** (which belongs to the
project owner).

Records are **Proposed** until the owner decides. 0005 and 0006 were accepted on
2026-10-16, 0009 on 2026-10-17, and 0008 on 2026-10-20 (its numeric storage triggers are still unwritten). A recommendation is not a decision. Each one has a
matching `decision-needed` issue, and the implementation issues that depend on it are
blocked on that issue rather than on this file.

| # | Decision issue | Question | Recommendation, in one line |
|---|---|---|---|
| [0001](0001-inferred-relationships.md) | #167 | Whether and how to build the inferred-link proposer | Defer until a human-labelled recall audit shows links explicit extraction misses; deterministic before LLM |
| [0002](0002-evidence-tiers-and-confidence.md) | #146 | How tiers and confidence work, and where statistical signals live | Keep three categorical tiers; statistical signals computed at query time (reviewer dissent recorded) |
| [0003](0003-rejected-and-superseded-decisions.md) | #154 | How rejected and superseded decisions are represented | "Prior attempts" view first; a separate `declined_decision` type only after a hand-checked rubric; reverts as explicit supersession |
| [0004](0004-semantic-retrieval.md) | #168 | Whether to add semantic retrieval | Measure first; Postgres full-text search before pgvector; no external vector store |
| [0005](0005-code-intelligence-granularity.md) | #147 | File, module, or symbol level | File-level from a local clone (API as fallback); directories as modules; symbols deferred |
| [0006](0006-agent-integration-boundary.md) | #136 | API / MCP boundary for coding agents | One versioned JSON contract; a thin, read-only MCP stdio adapter over it |
| [0007](0007-permissions-and-multi-repository.md) | #159 | Repository permissions and multi-repository support | Many repositories, one trust domain; public repositories only until an access model exists |
| [0008](0008-storage-ingestion-scale.md) | #160 | Storage, ingestion, caching, scalability | **Accepted:** keep Postgres (numeric triggers not yet measured); polling guarantees completeness, webhooks add latency; one writer |
| [0009](0009-edge-validity-and-retraction.md) | #181 | Retraction against validity on edges | **Accepted:** separate `retracted_at`; `valid_to` unchanged; `--as-of` against `--as-recorded` defined exactly; no deletes |

## Format

```
Status        Proposed | Accepted | Rejected | Superseded by NNNN
Facts         what is true now, and where it was checked
Hypotheses    what is believed and not yet measured
Options       each with its cost and what it would break
Recommendation and why
Evidence that would change the recommendation
Consequences  of the recommendation, including what it rules out
Decision      left blank until the owner decides
```

A record is changed by superseding it, not by editing its decision after the fact — the
same rule `edge.tag` follows.
