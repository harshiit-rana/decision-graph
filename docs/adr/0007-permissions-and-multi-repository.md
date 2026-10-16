# 0007 — Repository permissions and multi-repository support

**Status:** Proposed — awaiting owner decision

## Facts

- Ingestion, synthesis and query are repo-scoped (#28): `dg query --repo`, `node.repo_node_id`,
  and a CHECK that Decision threads stay within one repository (migration 0011).
- `evaluation.py` is deliberately whole-database "until a second repo actually shares a
  database with flask" (its docstring). `.env` holds a single `TARGET_REPO`, which the menu
  and `dg ask` use.
- The token is a GitHub PAT in `.env` (git-ignored). README says a public repository needs
  no scopes.
- The graph stores artifact bodies (issue/PR text, comments) in `node.raw`. For a private
  repository that is a copy of private content, readable by anyone with database access.
- There is no notion of a user. Anyone who can run `dg` or reach the database sees all of it.

## Options

| | Option | Note |
|---|---|---|
| A | One repository per database (status quo in practice) | simplest; no cross-repo questions |
| B | **Many repositories, one trust domain**: everyone with access may see every ingested repository | matches a team running it on repositories they all can read |
| C | Per-user access mirroring GitHub permissions | needs identity, permission sync, and row-level security; the PRD lists permission modelling as a non-goal |

## Recommendation

**B, public repositories only, until an access model exists.**

- Make multi-repository use explicit: every command takes `--repo` or a configured default,
  the menu offers a picker, and evaluation/report are repo-scoped.
- `dg init` and `dg ingest` **refuse a private repository** unless the owner explicitly
  opts in with a flag that states the consequence ("everyone who can reach this database can
  read this repository's issues"), because B cannot honour GitHub's permissions.
- Document least privilege: classic token with no scopes, or a fine-grained read-only token.
- Data minimisation: store bodies only for types the engines read (already the case for
  comments by design); document it.

C stays out of scope (PRD §4) until a real deployment needs it.

## Evidence that would change this

- A team wants to ingest private repositories with differing access → C, as its own ADR.

## Consequences

- No row-level security now; nothing implies there is.
- The MCP server (ADR 0006) inherits the same trust domain and must say so.

## Decision

_Pending._
