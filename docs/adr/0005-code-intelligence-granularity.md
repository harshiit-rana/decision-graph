# 0005 — Code-intelligence granularity

**Status:** Proposed — awaiting owner decision

## Facts

- The graph holds no file-level data. Commits come from the list endpoint
  (`/repos/{o}/{r}/commits`), which carries no `files`. The only `depends_on` edge is
  commit → parent commit (`extractors.py:744`). "Impact" today means *linked artifacts*,
  not affected code.
- File lists are available from the API at a cost: `GET /repos/{o}/{r}/commits/{sha}` (at
  least one request per commit; files paginate past 300 per page, up to 3,000; includes
  `status` and `previous_filename` for renames) or `GET /pulls/{n}/files` (one request per
  100 files of a PR, capped at 3,000).
- The same facts are available **without any API cost** from a local clone:
  `git log --name-status -M` lists every file each commit touched, with rename detection.
  The Docker image has no `git` today (`Dockerfile`: `python:3.13-slim` plus
  `postgresql-client`).
- CODEOWNERS is parsed into `codeowners_scope` nodes with `owns` edges
  (`extractors.py:871-892`), but it has never run on a real file — flask has none.
- Ingestion is already close to the 5,000 requests/hour budget for a 12-month window
  (README "What to expect": about 2 requests per PR).

## What each level can establish

| Level | Source | Establishes | Cannot establish |
|---|---|---|---|
| Commit (now) | Git metadata | which artifacts are linked | which code |
| **File** | GitHub API | which files a change touched; renames; co-change history; owners of touched paths | which functions; semantic dependency |
| Module | aggregation of files by directory | subsystem-level view, Architecture View | real module boundaries in languages where directories lie |
| Symbol | local clone + parser (e.g. tree-sitter) | which functions/classes changed | cross-language consistency; needs a clone and a new dependency |
| Static dependency graph | clone + language-specific analysis | "what imports this" | anything without a working build; per-language effort |

## Options

| | Option | Cost |
|---|---|---|
| A | Stay at commit level | none; impact stays artifact-level |
| B1 | File level via the **API**; module = directory aggregation at query time | ≥ 1 request per commit on top of a budget that is already tight; one migration |
| B2 | File level via a **local clone** (`git log --name-status -M`); module = directory aggregation | `git` in the image; clone disk space; a second source that must agree with the API on commit shas |
| C | B plus symbol level from the clone | a parser dependency per language |
| D | Static import graph | per-language analysers; the largest scope by far |

## Recommendation

**B2 now, with B1 as the fallback for an environment that cannot clone. C and D as research
spikes after B is measured.**

*Revised after review.* The first draft recommended B1. An independent review argued that a
clone is free of rate cost, complete (no pagination ceiling), handles renames natively, and
is the prerequisite for any symbol-level work anyway. The costs are real but smaller: `git`
in the image (one `apt` package), disk proportional to repository history, and a join on
commit sha between the clone and the API-ingested `commit` nodes — a commit present in one
and not the other must be reported, never silently dropped. For a private repository, a
clone is a full copy of its code, which ADR 0007 must cover.

File level is the smallest step that makes "what could this change affect?" and "why does
this file look this way?" answerable, and file-scoped context packs, CODEOWNERS-aware
impact, the Architecture View and decision drift all need it. Every edge stays explicit:
"commit X modified file Y" is stated by Git.

## Evidence that would change this

- The clone cannot be made to agree with the API commit set on a real repository → B1.
- Clone size makes B2 impractical for target repositories → B1 with PR file lists (fewer
  requests; loses direct-to-main commits; say which).
- Users ask "which function" more than "which file" → promote the symbol spike.

## Consequences

- New node type `file` (path is identity within a repo; rename handled by a validity window
  and a `renamed_from` link, not by mutating identity).
- Directory "modules" are computed, never stored, so no boundary is asserted that the
  repository does not state.
- Symbol-level claims stay out of every output until C is decided.

## Decision

_Pending._
