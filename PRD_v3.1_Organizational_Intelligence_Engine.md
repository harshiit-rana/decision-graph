# PRD v3.1: Organizational Intelligence Engine
*Internal Design Document*

**Changelog from v3:** tightened the `reconstructed` Decision rubric to a mechanical rule (5.1); made the inferred-edge fallback order an explicit traversal step (5.3); reconciled the flagship-query count with the evaluation set (9).

## 1. Summary

A graph-backed system that reconstructs the reasoning behind engineering decisions and predicts the impact of proposed or historical changes, by linking artifacts across GitHub into a single time-aware knowledge graph. The system does not compete on document retrieval; it competes on causal reconstruction and impact prediction, with every generated answer backed by inspectable evidence.

**Framing:** Git tracks how code evolves. This system tracks how engineering knowledge and decisions evolve.

## 2. Problem

Engineering knowledge is fragmented across tools and decays over time:

- The rationale behind a technical decision is scattered across a PR, an issue, review comments, and commit history — no single artifact captures it.
- Teams cannot cheaply answer "what depends on this?" before changing or reverting something.
- Ownership and tribal knowledge are concentrated in ways that are invisible until they cause a problem.
- Existing retrieval tools (Glean, Guru, enterprise Copilot search) solve document lookup, not causal reasoning or change impact.

## 3. Goals

- Reconstruct the causal chain behind a technical decision from linked GitHub artifacts.
- Predict what is affected by a proposed or historical change through graph traversal.
- Surface every conclusion with an inspectable evidence trail and an honest confidence tier — never an unexplained answer.
- Build graph edges primarily from explicit signals in the data; use inference only where no explicit signal exists, and mark it as such.
- Never assert that a decision occurred without evidence that it did.
- Ship a working system against one real GitHub repository, demonstrated through two flagship queries (see Section 9 for how these relate to the full evaluation set).

## 4. Non-Goals

- General-purpose document search (not competing with Glean/Guru on retrieval breadth).
- Multi-source ingestion — Slack, Jira, Notion, Confluence are out of scope for v1.
- Webhook-driven real-time ingestion — v1 uses scheduled polling.
- Permission modeling / ACL simulation.
- Formal precision/recall evaluation at scale — v1 evaluation is a small hand-verified query set.
- A dedicated graph database, event bus, or microservice architecture — deliberately out of scope; see Design Decisions.

## 5. Architecture: Three Engines

The system is built as three engines, connected through an explicit pipeline. Every feature is a thin query or presentation layer over the engines — no feature owns independent logic or state.

**Pipeline:** GitHub → Extraction → Knowledge Graph → Candidate Retrieval → Reasoning → Evidence Ranking

### 5.1 Engine 1 — Knowledge Graph

The source of truth. All other engines read from it; nothing else holds state.

**Entities (v1, GitHub-scoped):**
Repository, Commit, PR, Branch, Issue, Release, Person, Team, CODEOWNERS-scope, Workflow/Action, Wiki Page, Decision.

**Relationships:**
`references`, `created`, `reviewed`, `owns`, `depends_on`, `discussed_in`, `implements`, `closes`, `deployed_by`, `supersedes`, `mentions`, `relates_to`, and, specific to Decision: `motivated_by` (→ Issue), `implemented_by` (→ Commit/PR), `superseded_by` (→ later Decision).

**Decision entity:** represents a technical decision that demonstrably occurred — it is never created from inference alone. A Decision node has a status:

- **explicit** — backed by a formal artifact (ADR, RFC, release note, explicit issue resolution).
- **reconstructed** — no single formal record exists, but the decision is assembled from multiple converging explicit signals. Every link used is itself explicit; only the assembly is done by the system.

There is no *inferred* status for Decision. If the available evidence doesn't meet the bar for explicit or reconstructed, no Decision node is created — the underlying artifacts remain queryable, but the system does not assert that a decision occurred without evidence it did.

**Rubric for `reconstructed` status (mechanical, no model judgment):**

Three signal categories, each mapped to an explicit edge type already in the graph:

| Category | Explicit edge | What it answers |
|---|---|---|
| Motivation | `motivated_by` (→ Issue/RFC/discussion) | Why |
| Implementation | `implemented_by` (→ Commit/PR) | What was done |
| Validation | `reviewed` / `closes` | That it landed |

A `reconstructed` Decision node is created only when:

1. **Motivation is present** — a `motivated_by` edge to an issue, RFC, or discussion exists. This is mandatory, not optional: a Decision node exists to answer "why," and Implementation + Validation alone (e.g. a merged, reviewed PR with no linked issue) describes *that* something happened and *that* it landed, but not *why* — it fails the rubric and no Decision node is created.
2. **At least one of {Implementation, Validation} is also present** — i.e. `motivated_by` + `implemented_by`, or `motivated_by` + `reviewed`/`closes`.
3. **All edges reference the same commit/PR cluster within a bounded time window** — e.g. the PR and the issue it closes, not two loosely related PRs weeks apart. (v1: same PR/issue thread, or commits sharing a PR; exact window to be fixed during implementation and documented alongside the rule.)

This is a mechanical check over edge types already in the graph — no LLM judgment call, no ambiguity about "how many signals is enough." If the bar isn't met, the underlying PR/issue/commits remain queryable as plain artifacts; no Decision node is asserted.

**Construction principle — extraction-first:** edges are built primarily from explicit signals: commit messages, issue/PR cross-links, review approvals, CODEOWNERS entries, file-path co-occurrence, and git history. LLM inference is used only to propose an edge when no explicit signal connects two entities that are plausibly related. Every inferred edge is tagged `inferred` at creation and never conflated with explicit data downstream. (Decision nodes specifically are exempt from ever being created via this inferred path — see above.)

**Gating inferred edges — tagging alone doesn't bound volume, so creation is constrained:**

- **Relevance threshold:** inferred edges are only created above a defined similarity/relevance threshold. Below it, no edge is created at all — an absent answer is preferred over a weak inferred one.
- **Per-node cap:** each entity accumulates at most a small fixed number of inferred edges (v1 default: 3–5, tunable). This prevents any single node from acquiring a long tail of speculative connections that dilutes future traversals.

**Time-versioning:** every edge carries `created_at` and, where applicable, a validity window (`valid_from` / `valid_to`). Required at schema level from v1 — point-in-time queries depend on it, and retrofitting it later would require a schema rebuild.

**Storage:** PostgreSQL, with adjacency-list-style edge tables. Chosen over a dedicated graph database because v1 traversals are shallow (2–3 hops) and graph size (single repo) doesn't justify the operational cost of a separate graph engine. A migration to a dedicated graph database is a documented future decision point, not a v1 assumption.

### 5.2 Engine 1.5 — Candidate Retrieval

A narrowing step between the graph and the reasoning engine: given a query, retrieval selects a plausible set of starting/candidate nodes (by entity name match, keyword match, or simple lookup) before traversal begins, rather than traversing the entire graph. In v1 this is intentionally simple — exact/fuzzy entity lookup — not a ranked or embedding-based retrieval system. It's included explicitly in the architecture because it becomes necessary at any scale beyond a toy graph, even though v1's implementation is minimal.

### 5.3 Engine 2 — Graph Reasoning

A single traversal engine exposed through two directional query modes, operating on the candidate set from retrieval:

- **Backward traversal (Why Engine):** from a Decision or artifact, walks `motivated_by` / `discussed_in` / `references` / `supersedes` edges to reconstruct the causal chain — motivation → discussion → decision → implementation → follow-ups.
- **Forward traversal (Impact Analysis):** from a commit, PR, or artifact, walks `depends_on` / `implements` / `closes` edges to surface what is affected — in either direction, supporting both "what does this depend on" and "what would reverting this affect."

Both modes share one traversal implementation; only the starting node and direction differ.

**Traversal order for inferred edges (explicit algorithm step, not a design principle floating outside it):**

1. Attempt the traversal using only `explicit` and `corroborated` edges first.
2. Only if this produces a disconnected path (no route between the candidate start node and a plausible answer) does the traversal expand to include `inferred` edges, subject to the per-node cap and threshold defined in 5.1.
3. If a fully explicit/corroborated path exists, `inferred` edges are excluded from that answer entirely — they are never blended into an already-connected explicit path, only used to bridge a gap that would otherwise leave the query unanswered.

This keeps `inferred` a genuine last resort rather than a background layer diluting every answer, and makes the fallback behavior something the implementation can be tested against directly.

### 5.4 Engine 3 — Evidence Ranking

Every answer produced by Engine 2 is annotated with an evidence tier, not a numeric confidence score:

- **Explicit** — direct, unambiguous reference (PR closes Issue, commit implements Decision).
- **Corroborated** — multiple independent explicit signals converge (issue + commits + review approval).
- **Inferred** — no explicit link exists; relationship proposed by LLM inference from content similarity.

Tiers are surfaced alongside every answer so the basis for a conclusion is always visible.

## 6. Feature Surface

All features are thin views over the engines above — no feature introduces new engine logic.

| Feature | Engine(s) | Description |
|---|---|---|
| Why Engine | Graph + Retrieval + Reasoning (backward) | Reconstructs the causal story behind a decision |
| Impact Analysis | Graph + Retrieval + Reasoning (forward) | Predicts what is affected by a change or revert |
| Explainability Mode | Reasoning | Exposes the exact traversal path behind an answer |
| Knowledge Search | Graph + Retrieval + Ranking | Baseline lookup, least differentiated feature |

Bus Factor, Architecture View, and Expert Finder are natural extensions of the same engines but are out of scope for the v1 deliverable defined here.

## 7. Explainability Mode

Every Why Engine or Impact Analysis answer includes an expandable trace of the traversal path that produced it — node, edge, node, in sequence, with each edge's evidence tier shown. Reuses path data already computed by Engine 2; introduces no new computation, only presentation.

## 8. Ingestion

**v1:** scheduled polling of the GitHub API (repositories, issues, PRs, commits, reviews, releases, CODEOWNERS), processed synchronously into the graph via the Extraction stage on each run.

**Explicitly deferred:** webhook-driven event queue with a relationship extractor for near-real-time updates. Documented as the intended long-term ingestion path, not built in v1.

## 9. Evaluation

Formal precision/recall evaluation at scale is out of scope for v1 given the effort required to produce reliable ground truth. Instead:

- A hand-verified set of 15–20 Why Engine / Impact Analysis queries run against one real, seeded GitHub repository.
- The two flagship queries referenced in Section 3 are a curated subset of this set, chosen for narrative clarity in demos — they are not validated separately from the other 13–18 queries. The evaluation set as a whole is: the 2 flagship queries + 13–18 additional queries covering less curated, harder cases.
- Each answer manually checked against the repo's actual history for correctness.
- Reported as a simple accuracy figure, with failure cases documented rather than hidden.

## 10. Risks

| Risk | Mitigation |
|---|---|
| System asserts a decision existed without evidence | Decision nodes require explicit or reconstructed status backed by explicit signals, per the mechanical rubric in 5.1 (Motivation mandatory + one of Implementation/Validation); never created from inference alone |
| Evidence tiers read as arbitrary | Tier assignment is rule-based on signal type, not model-judged; rules documented alongside output |
| Scope creep across feature list | Features are enforced as thin views; no feature may introduce engine-level logic |
| Ingestion complexity stalls delivery | v1 uses polling; webhook architecture documented, not built |
| Graph traversal outgrows Postgres | Documented migration trigger to a dedicated graph database, not a default assumption |
| Time-versioning retrofitted late | Time fields are part of the initial schema, not added post hoc |
| Inferred edges treated as fact | Inferred edges tagged at creation, gated by relevance threshold and per-node cap (5.1), and used only as a traversal fallback when no explicit/corroborated path exists (5.3) |
| Retrieval omitted entirely, traversal doesn't scale | Candidate Retrieval acknowledged in architecture from v1, even with a minimal implementation |

## 11. Phased Roadmap

- **Phase 1:** Time-versioned graph schema in Postgres; GitHub polling ingestion against one real repository; extraction-first edge construction; Decision status rules (including the reconstructed rubric and inferred-edge gating).
- **Phase 2:** Candidate Retrieval (simple lookup); Graph Reasoning engine — backward (Why Engine) and forward (Impact Analysis) traversal, including the explicit-first / inferred-fallback traversal order.
- **Phase 3:** Evidence Ranking engine with Explicit / Corroborated / Inferred tiering.
- **Phase 4:** Explainability Mode; evaluation set and accuracy reporting.
- **Phase 5 (future, out of scope here):** Webhook-driven ingestion, additional sources (Slack, Jira), permission modeling, Bus Factor / Architecture View / Expert Finder, ranked/embedding-based retrieval.

> **Roadmap note — rejected decisions as a first-class outcome (Phase 5+).**
> Implementation surfaced a category the current model cannot express. A cluster where
> maintainers *declined* to act — an issue closed as `not_planned`, with closing PRs left
> unmerged — is a real decision, and against one real repository window they are not rare:
> 28 of 54 closed issues, and 7 of the 20 clusters that initially qualified as Decisions.
>
> v1 handles these by exclusion. §5.1 Validation requires that work landed, so a declined
> cluster produces no Decision node and its artifacts stay queryable as plain artifacts.
> That is correct for v1 and prevents the system asserting a change was made when it was
> refused — but it means the graph is silent about a decision that demonstrably occurred,
> and the answer to "why doesn't Flask do X?" is often more valuable than "why does it?".
>
> Modelling these properly is not a rubric tweak. It needs rejection-rationale extraction
> that does not exist yet: the *why* of a refusal lives in closing comments and review
> discussion, not in the structured signals (`closes`, `implements`, `reviewed`) the
> extraction-first design currently reads. Adding a `rejected` outcome without that would
> reintroduce exactly the failure §5.1 exists to prevent — asserting a decision whose
> rationale the system cannot evidence.

## 12. Success Criteria

- Why Engine and Impact Analysis both return correct, evidence-tiered answers against a real repository's history.
- No Decision node exists without explicit or reconstructed status and supporting evidence, per the rubric in 5.1.
- Every answer is traceable via Explainability Mode to the specific artifacts that produced it.
- Evaluation set achieves a documented, honestly-reported accuracy rate — including disclosed failure cases.
