# 0006 — Agent integration boundary (API / MCP)

**Status:** Accepted — 2026-10-16, by the project owner

## Facts

- There is no machine-readable output. `dg query --format` accepts `text`, `mermaid`,
  `dot` (`query.py:122-123`). Everything an agent could consume today is prose for humans.
- All answers come from one engine path (`reasoning._walk`) and one renderer per format;
  the menu re-runs `dg query` rather than reimplementing it (#78). That "one implementation"
  rule is what keeps outputs from drifting.
- `dg ask` is the only component that calls a third-party service, and it already sends
  repository text to it (`explain.render_context`), with no data boundary in the prompt
  (separate security issue).
- The Python MCP SDK would be a new dependency. The project currently has two runtime
  dependencies (`psycopg`, `httpx`) plus an optional `openai`.

## Hypotheses

- H1. A coding agent about to modify a file makes fewer wrong assumptions if it is handed
  the decisions, prior attempts and owners for that file. **Unmeasured** — this is the
  claim the agent study must test.
- H2. Agents consume structured fields (tier, URL, refusal reason) far more reliably than
  prose.
- H3. Repository text returned to an agent is a prompt-injection vector into *that* agent,
  which TraceMind does not control.

## Options

| | Option | Note |
|---|---|---|
| A | JSON output only (`--format json`); agents shell out to `dg` | zero new dependency; every agent can use it |
| B | **A + a thin MCP stdio server** wrapping the same functions | native in MCP-capable agents; one optional dependency |
| C | HTTP API service | needs auth, hosting, TLS; no user has asked for it |
| D | Agent-framework integration (LangChain etc.) | couples to one ecosystem |

## Recommendation

**B, strictly layered:**

1. A **versioned JSON contract** (`schema_version`, every answer's paths, tiers, refusal
   reason, URLs) is the product. It is tested with golden files and a published JSON Schema.
2. The MCP server is an adapter that calls the same functions and returns that JSON —
   no logic of its own, mirroring how the menu relates to `dg query`.
3. **Read-only.** No tool writes to the graph or to GitHub. The server connects with a
   read-only database role.
4. Every repository-authored string in a response sits in a field marked as untrusted
   content (e.g. `"untrusted_text": {...}`), never concatenated into instructions, and the
   docs tell agent authors so (H3).
5. Optional dependency, like `nlp`: `pip install 'decision-graph[mcp]'`.

Tools, initially, **only what the engine can answer today**: `why`, `impact`, `lookup`
(resolve an identifier as the tool prints it), `prior_attempts` (the query-time view in ADR
0003, option A), and `context_pack` for an issue or pull request. `file_history` and
file-scoped context packs join after file-level ingestion (ADR 0005); the agent study must
not wait for them. Each tool returns refusals as structured results, not errors.

## Evidence that would change this

- The agent study (pre-registered) shows no difference with vs without context packs →
  stop at A; do not maintain an MCP surface for no measured benefit.
- A real user needs remote access → revisit C with ADR 0007's access model.

## Consequences

- JSON output becomes a compatibility promise; breaking changes bump `schema_version`.
- The MCP server never gains a capability `dg` lacks.

## Decision

**Accepted: B — a versioned JSON contract plus a thin, read-only MCP stdio adapter.**
Decided by the owner on 2026-10-16, choosing the recommended option.

Binding on #135 and #139: the JSON contract is the product and the adapter holds no logic;
the adapter connects with a read-only database role; every repository-authored string sits in
a field marked untrusted; the initial tool set is limited to what the engine answers today.
The agent study (#140–#142) remains the evidence that decides whether the MCP surface is
kept — this decision commits to building it, not to keeping it regardless of the result.

## Addendum, 2026-10-17: a contract guarantee (#175)

This doesn't change the decision above. It records a guarantee the JSON contract now makes,
so a consumer of the MCP adapter can rely on it.

**TraceMind returns conflicting evidence and never resolves it.** Each result has a
`conflicts` list. When the graph holds evidence pointing two ways, every side is listed with
its tier, extractor, source and timestamp, and no field names a preferred side. Sides are
ordered chronologically, which is not a ranking. An empty list is a statement that the graph
holds no conflict for that answer. Competing agent-memory products resolve contradictions
silently (recency, deletion or last-write-wins), so an agent acts on a winner it never knew
was contested. `tests/test_conflicts.py` asserts that no key in any conflict, in the data or
in `docs/schema/answer.v1.json`, names a winner.
