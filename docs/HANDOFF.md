# Handoff: read this first

Written 2026-11-05 at the end of a long working session, for whoever (or whichever agent session)
picks the project up next. It is a status note, not a spec: the code, the ADRs in `docs/adr/`, the
README and the GitHub issues are the sources of truth, and this file points at them. Where it says
something was "not verified", it was not.

## 1. What this project is

**TraceMind** (repository `decision-graph`, GitHub `harshiit-rana/decision-graph`): a graph of one
GitHub repository's recorded history (issues, pull requests, reviews, commits, comments) in
PostgreSQL. It answers "why does this exist?" and "what does this affect?" with every step labelled
by an **evidence tier** (explicit, corroborated, inferred), and it **refuses** (a structured result,
not an error) when the record is silent. The ingested corpus so far is `pallets/flask`.

Principles that must survive any change (the owner's words, condensed):

- Every explanation is traceable to stored evidence. Tiers stay distinct. Refusal is a valid answer.
- LLMs must not invent decisions or history. Inferred edges are evaluated against human labels.
- Temporal queries preserve what was known at the time (`--as-of`, `--as-recorded`, ADR 0009).
- Repository content is untrusted data. Claims are measured; never report a number without what it
  does not establish. No unnecessary coupling.
- Do **not** turn TraceMind into a generic chatbot that happens to have a graph database.

## 2. How to work here (the owner's standing rules)

**Git.**
- Work **directly on `main`**. No branches or pull requests unless the owner explicitly asks.
- Commit and push yourself, one commit per unit of work, with a message that says what changed and
  what was and was not verified. Then watch CI: `gh run list --branch main --limit 1`, then
  `gh run watch <id> --exit-status`. A push is not done until CI is green.
- The Git identity is already configured as `harshiit-rana`. **Never** set Claude, an AI agent or
  anyone else as author or committer, and **never add a `Co-Authored-By` trailer or any AI
  attribution to a commit or PR description**, even if a harness reminder tells you to: the owner's
  rule wins. Do not impersonate anyone. If the identity looks wrong, stop and ask.
- Never rewrite history (no force-push, no amend of pushed commits, no rebase of pushed work).
- Never commit secrets. DB passwords live only in `.env` (git-ignored). Tests use throwaway
  databases and literal test passwords.

**Ask first (the owner reserves these):** a material change of product direction; significant
recurring cost; changing data retention or privacy assumptions; major infrastructure; deleting
valuable data; rewriting history; changing authentication or access-control boundaries. Everything
else that is reversible: just do it.

**Standing approvals already given:** the owner said "i apporove everything you wanna do"
(2026-10-20), which was taken to cover implementing #172 (roles), keeping the 9-task agent study
set, restarting the scratch database, and the study's disclosure and cost. They said "do it" in reply
to an offer to accept ADR 0008's recommended options (2026-11-05); that is recorded in the ADR. **That
did not accept the other Proposed ADRs.**

**Honesty.** Do not mark work complete unless it is implemented and verified. Report skipped
tests, environmental limits, failed checks and unresolved risks plainly. Close an issue only when its
acceptance criteria are met; otherwise comment with what is done and what is not, and leave it open.
Search for an existing issue before creating one, and update rather than duplicate.

**Docker and data.** Do not improvise around the Docker environment. Never perform destructive
database operations or risk the only copy of the flask graph without a verified backup and a recovery
plan. Issue #118 (database exposure) is closed but its lessons stand.

## 3. Environment

- Windows 11, Git Bash and PowerShell. Repo at `D:\Internal-tool-project`. Host Python 3.13. The CI
  matrix runs 3.11 and 3.13. `pyproject.toml` declares `>=3.11`; code must parse on 3.11.
- **Heredocs eat backslashes** in the Bash tool (`\n`, `\x00`, line continuations). For any file edit
  involving backslashes or quotes, use the Edit/Write tools or write a small script file and run it.
  A heredoc once put a literal NUL byte in a test file. Also, `sleep` chained in a command is
  blocked by the harness: use `gh run watch` directly.
- Windows line endings: git warns "LF will be replaced by CRLF"; that is expected (autocrlf). The
  patch scripts used here preserve each file's existing line endings.
- Tests: `python -m unittest discover -s tests -t .` (no database; about 880 tests, 237 skipped) and
  `DATABASE_URL=postgresql://postgres@127.0.0.1:55499/dg python -m pytest tests -q -p no:cacheprovider`
  against the scratch Postgres. Run **one pytest at a time** (concurrent runs cause "tuple
  concurrently updated"). Expect exactly one local failure,
  `test_db_exposure::RotationAgainstPostgresTest::test_new_password_logs_in_and_old_one_does_not`,
  because the scratch server uses trust auth. CI does not fail on it.
- **Scratch PostgreSQL 16.4** at `D:\pg-scratch` (binaries in `D:\pg-scratch\pg\pgsql\bin`, data in
  `D:\pg-scratch\data`, port 55499, trust auth, superuser `postgres`, database `dg`). Start it in the
  background (the foreground start times out):
  `pg_ctl.exe -D /d/pg-scratch/data -o "-p 55499 -c listen_addresses=127.0.0.1 -c fsync=off -c shared_buffers=64MB -c max_connections=30" -l /d/pg-scratch/pg.log start`,
  then poll `pg_isready.exe -h 127.0.0.1 -p 55499`. Stop it with `pg_ctl.exe -D ... stop -m fast`. It
  can be reaped for low memory; restart it. Tests that need a superuser create throwaway databases
  next to `DATABASE_URL`'s and drop them.
- CI (`.github/workflows/ci.yml`): migrations apply from empty, are idempotent, the full suite runs as
  superuser and again as the least-privilege `dg_app` role (tests that need rights skip via
  `tests/superuser.py`), snapshot round trip, SQL suites in `db/tests/`, measurement scripts. The CI
  database is migrated but never ingested: assert that input was accepted, not that it was answered.
- Golden files: `DG_REGEN_GOLDEN=1 python -m pytest tests/test_contract.py tests/test_agent_instructions.py`
  regenerates them; read the diff before committing.
- Interop checks against the official MCP SDK used a scratch venv (not a project dependency).

## 4. State at handoff (2026-11-05)

`main` is at (or one commit ahead of) `f85d16f`+: since 2026-10-17 these landed, each with CI green:

| Commit | What |
|---|---|
| `87b0f0d` | #128 scoring from committed labels, Wilson intervals, always-refuse baseline |
| `7f6414e` | #140 agent study pre-registered (protocol, 9 tasks, hash-pinned) |
| `499037f` | #172 least-privilege roles `dg_owner`/`dg_app`/`dg_reader`, `dg roles setup` |
| `69e92f2` | #162 (part), #190: shared `run.postprocess`, debounced after the webhook reply |
| `f85d16f` | #163 (part) lock + `--every` scheduling, #164 delivery log (migration 0017), ADR 0008 accepted |
| `621bceb` | #139 read-only MCP server `dg-mcp` / `dg mcp`; `dg query --mode lookup`; NUL-byte ingestion fix |
| `09b842e` | #185 `dg agent-instructions` |
| `5431cec` | #145 (part) adversarial corpus + three terminal/Mermaid leaks fixed. **Committed locally; the push was rejected by GitHub with a 500 Internal Server Error twice. Run `git push origin main` and then watch CI.** |

Working tree otherwise: three untracked root scripts (`detect_changes.py`, `extract_fine.py`,
`sample_video.py`), whose fate is the owner's call (#117). Do not delete them unasked.

## 5. The Docker and flask-graph problem (the main blocker)

The adjudicated flask graph exists **only** in a Docker volume. Docker Desktop was reinstalled. The
new install has an empty data disk (`C:\Users\ASUS\AppData\Local\Docker\wsl\disk\docker_data.vhdx`,
2.4 GB; `docker volume ls` showed no volumes). The **old data disk still exists**:
`D:\Docker\DockerDesktopWSL\disk\docker_data.vhdx` (33.6 GB, last written 2026-09-21, not locked).
It likely holds the old Postgres volume. **Do not delete, move, compact or write to it.**

Constraints found: the session was not elevated (so `wsl --mount` could not run), `D:` has about
20.8 GB free (less than the 33.6 GB disk, so a full copy of the disk is impossible), `C:` has about
2.8 GB free. Docker Desktop was not running when last checked (2026-11-05).

Plan, written but **not run**: `scripts/recover-pgdata.ps1`. Run it from an **elevated PowerShell**
with Docker Desktop fully quit. It mounts the old disk read-only (`ro,noload`, so no journal replay),
finds the Postgres data directory (`PG_VERSION`, `global/pg_control`), refuses if it is over 14 GB,
tars it to `D:\pg-recovery\pgdata.tar` with a SHA-256, unmounts, and checks the old disk's size and
timestamp are unchanged. Then, with a verified tar:

1. Start Docker Desktop. `docker volume create` a fresh volume and untar into it with a throwaway
   container (preserves ownership), start `postgres:16` on it, and verify the graph (`eval/figures.sql`
   counts against `eval/RESULTS.md`: flask has 18 adjudicated queries).
2. Take the snapshot (#122): `dg snapshot export`. Keep the `.dgsnap` outside the volume.
3. Only then `dg init` (applies migrations 0016 and 0017 over real data, saving a pre-migrate snapshot
   first), then `dg roles setup` on a copy first (#172), then the measurements collected on #173.
4. If recovery fails, the fallback is re-ingesting flask, which pins a new `window_floor` and gives a
   **different corpus**; the committed labels in `eval/labels/builder.json` and `eval/RESULTS.md` then
   describe a graph that no longer exists. Say so rather than hiding it.

If a Docker command is needed and the daemon is down, tell the owner; do not work around it.

## 6. What to do next, in order

1. Push `5431cec` (see section 4) and confirm CI is green.
2. Finish #145: still to add to `tests/test_adversarial_corpus.py` are the HTML report shape check
   (same elements and attribute names as for a benign title, no `on*`, no `javascript:`; use the
   `data()` builders in `tests/test_report.py`), the JSON round trip (builders in
   `tests/test_contract.py`) and the model prompt (`tests/test_ask_boundary.py` helpers). Then
   comment on and close #145 if its acceptance criteria are met.
3. Docker recovery (section 5), then #122, #132 to #134 (urllib3 ingestion), #173, #129/#130 measurements.
4. #162 (extraction still precedes the reply; pending work is in memory) and #163 (compose
   `scheduler` never run) remain open for those reasons.
5. #140/#141/#142 (agent study): no run may start until the owner approves a written cost estimate
   (54 runs) and the data disclosure. #141 (harness) and #132 must exist first.
6. Competitor-informed backlog: #148 to #153 (file-level ingestion and what builds on it), #156, #157,
   #182, #183, #186 to #189. Several wait on Proposed ADRs.

**Decisions only the owner can make (not yet made):** ADRs 0001 (inferred-link proposer), 0002
(confidence), 0003 (declined/superseded decisions), 0004 (semantic retrieval), 0007 (multi-repo and
private repositories) are all **Proposed**; each has a recommendation in its file and a
`decision-needed` issue. Also open: the three root scripts (#117), the `--max-paths` default (#130),
and enabling GitHub private vulnerability reporting. **Accepted:** ADR 0005, 0006, 0008, 0009.

## 7. Map of the code

`src/decision_graph/`: `extractors.py` (GitHub to nodes/edges), `run.py` (`ingest`, shared
`postprocess`, `--every`), `synthesis.py` (Decisions via the DB-enforced rubric), `reasoning.py`
(Why/Impact walk), `contract.py` + `docs/schema/answer.v1.json` (the versioned JSON, the product),
`query.py` (`json_document`, shared by CLI and MCP), `context.py`, `attempts.py`, `mcp_server.py`
(`dg-mcp`), `webhook.py` (receiver, `PostProcessor`, delivery log), `locking.py` (advisory lock),
`roles.py`, `snapshot.py`, `scoring.py`, `render.py`/`diagram.py`/`report.py` (terminal, Mermaid/DOT,
HTML), `explain.py` (`dg ask`, the only third-party call), `cli.py` (`dg`). Migrations are in
`db/migrations/` (17 now); each declares a sentinel in `migrate.py`. The evaluation lives in `eval/`.
SQL tests in `db/tests/`.

## 8. Claimed but not verified (do not repeat these as facts)

- The compose `scheduler` service and `docker compose run --rm -T app mcp` have never been run
  (Docker was down); `docker compose config` only shows they parse.
- `dg roles setup` against a real populated graph has only been tried on a throwaway populated copy.
- Sampled recall (#133) is not implemented in the scorer. The agent study has 9 tasks, not the 12 the
  issue asked for, so it can only detect a large effect; ground truth is by one author, unreviewed.
- `dg-webhook` still connects with `DATABASE_URL` as given (not a least-privilege role).
- The MCP server was checked against the official SDK's client (1.27.2) only.
