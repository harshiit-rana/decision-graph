"""Coverage: what the ingested window could and could not have contained (issue #174).

A refusal, or an empty attempts list, can be misread as "nothing happened" when the evidence
was simply outside what was ingested: older than the window, or in a repository whose last
run stopped early. The text says so in prose. A program reading `--format json` had no field
for it.

Everything here is a mechanical reading of two things the database already records:

- the **cursors** (`ingestion_cursor`): each resource's pinned window floor, its steady
  watermark, and for commits whether the backfill reached the floor;
- the **latest ingestion run** (`ingestion_run`), including the two outcomes `run.py` stores
  as `succeeded` with a note: "stopped early" (the rate-limit floor) and "interrupted".

A status is one of `complete`, `partial`, `outside_window` or `unknown`, and each carries a
reason code from the fixed vocabulary below and a fixed sentence. **There is no numeric
completeness score**: nothing could calibrate one (ADR 0002). Where a fact isn't recorded the
status is `unknown`, never a guess: a wrong coverage claim is worse than none.

What `complete` means, and doesn't: the cursors that pulled this artifact have reached their
steady state and the last run finished. It is not a claim that GitHub holds nothing else, and
it says nothing about activity after the last run.
"""

from __future__ import annotations

from typing import Any

# The window floor for a first run is `now - 30 * months` days (cursors.load), so "12 months"
# is 360 days. Reported as pinned in the cursor, never recomputed here.

# resource a node type was pulled by
_RESOURCE_OF = {"issue": "issues", "pull_request": "pulls", "commit": "commits",
                "release": "releases"}

REASONS: dict[str, tuple[str, str]] = {
    # reason: (status, fixed sentence)
    "ingestion_complete": (
        "complete",
        "The cursors that pulled this have reached their steady state and the last ingestion "
        "run finished. This says nothing about activity since that run.",
    ),
    "backfill_incomplete": (
        "partial",
        "Ingestion has not finished its backfill for the resource that pulled this, so "
        "artifacts inside the window may be missing.",
    ),
    "last_run_failed": (
        "partial",
        "The last ingestion run failed, so the graph may be missing artifacts from that run.",
    ),
    "last_run_stopped_early": (
        "partial",
        "The last ingestion run stopped early (the rate-limit floor or an interrupt), so the "
        "graph may be missing artifacts. Running `dg ingest` again resumes it.",
    ),
    "older_than_window": (
        "outside_window",
        "The query names a number lower than every issue or pull request created inside the "
        "ingested window. If it exists, it predates the window and was not updated inside it.",
    ),
    "no_ingestion_record": (
        "unknown",
        "The database has no ingestion cursor for this repository, so completeness can't be "
        "stated.",
    ),
    "no_repository": (
        "unknown",
        "More than one repository is ingested, or none, and the query didn't name one, so "
        "coverage can't be stated. Pass --repo.",
    ),
    "not_in_graph": (
        "unknown",
        "Nothing in the graph matches. It may not exist, may be newer than the last ingestion, "
        "or retrieval may have missed it.",
    ),
}

STATUSES = ("complete", "partial", "outside_window", "unknown")


def _check(subject: str, reason: str, **extra: Any) -> dict[str, Any]:
    status, detail = REASONS[reason]
    return {"subject": subject, "status": status, "reason": reason, "detail": detail, **extra}


def repository_facts(conn, repo_node_id: int | None) -> dict[str, Any] | None:
    """The window and ingestion state of one repository, or None if it has no cursors."""
    if repo_node_id is None:
        return None
    name = conn.execute(
        "SELECT external_id FROM node WHERE id = %s AND node_type = 'repository'", (repo_node_id,)
    ).fetchone()
    cursors = conn.execute(
        "SELECT resource, phase, window_floor, backfill_cursor, steady_watermark "
        "FROM ingestion_cursor WHERE repo_node_id = %s ORDER BY resource", (repo_node_id,)
    ).fetchall()
    run = conn.execute(
        "SELECT status, error, finished_at FROM ingestion_run WHERE repo_node_id = %s "
        "ORDER BY id DESC LIMIT 1", (repo_node_id,)
    ).fetchone()
    if name is None and not cursors:
        return None
    resources = {
        c["resource"]: {
            "phase": c["phase"],
            "window_floor": c["window_floor"],
            "through": c["steady_watermark"],
        }
        for c in cursors
    }
    paged = [resources[r] for r in ("issues", "pulls") if r in resources]
    floors = [r["window_floor"] for r in paged if r["window_floor"]]
    throughs = [r["through"] for r in paged if r["through"]]
    finished = conn.execute(
        "SELECT max(finished_at) AS t FROM ingestion_run WHERE repo_node_id = %s "
        "AND status = 'succeeded'", (repo_node_id,)
    ).fetchone()["t"]
    return {
        "name": name["external_id"] if name else None,
        "node_id": repo_node_id,
        "window_floor": min(floors) if floors else None,
        "ingested_through": min(throughs) if len(throughs) == len(paged) and throughs else None,
        "last_ingested_at": finished,
        "last_run_status": run["status"] if run else None,
        "last_run_note": run["error"] if run else None,
        "resources": resources,
    }


def _run_problem(facts: dict[str, Any]) -> str | None:
    if facts["last_run_status"] == "failed":
        return "last_run_failed"
    note = (facts["last_run_note"] or "").lower()
    if facts["last_run_status"] == "succeeded" and (
        note.startswith("stopped early") or note.startswith("interrupted")
    ):
        return "last_run_stopped_early"
    return None


def _resources_check(subject: str, facts: dict[str, Any] | None, needed: list[str]) -> dict[str, Any]:
    """complete / partial / unknown for the resources `needed`, from cursors and the last run."""
    if facts is None or not facts["resources"]:
        return _check(subject, "no_ingestion_record")
    states = [facts["resources"].get(r) for r in needed]
    if any(s is None for s in states):
        return _check(subject, "no_ingestion_record")
    for resource, state in zip(needed, states):
        # Only commits walk backwards through a backfill phase. Issues and pull requests
        # arrive in steady state, so for them "no watermark yet" is the unfinished case.
        unfinished = (state["phase"] != "steady") if resource == "commits" else (
            resource in ("issues", "pulls") and state["through"] is None)
        if unfinished:
            return _check(subject, "backfill_incomplete", resource=resource)
    problem = _run_problem(facts)
    return _check(subject, problem or "ingestion_complete")


def node_check(conn, node_id: int, facts: dict[str, Any] | None) -> dict[str, Any]:
    """The coverage status of one artifact that IS in the graph."""
    row = conn.execute("SELECT node_type::text AS t FROM node WHERE id = %s", (node_id,)).fetchone()
    resource = _RESOURCE_OF.get(row["t"] if row else "")
    # Decisions, people and the rest are derived from issues and pull requests, so they take
    # the weaker of the two.
    return _resources_check(f"node:{node_id}", facts, [resource] if resource else ["issues", "pulls"])


def repository_check(facts: dict[str, Any] | None) -> dict[str, Any]:
    """What an answer-less document (attempts, an empty list) can say about its window."""
    if facts is None:
        return _check("repository", "no_repository")
    return _resources_check("repository", facts, ["issues", "pulls"])


def query_check(conn, query: str, facts: dict[str, Any] | None) -> dict[str, Any]:
    """The coverage status of a query that matched nothing."""
    from . import retrieval

    if facts is None:
        return _check("query", "no_repository")
    if not facts["resources"]:
        return _check("query", "no_ingestion_record")
    numbers = [int(n) for n in retrieval.identifiers_in(query) if n.isdigit()]
    if numbers and facts["window_floor"] is not None:
        lowest = conn.execute(
            """
            SELECT min(x.number) AS n FROM (
                SELECT i.number, n.source_created_at FROM issue i JOIN node n ON n.id = i.node_id
                 WHERE n.repo_node_id = %(r)s
                UNION ALL
                SELECT p.number, n.source_created_at FROM pull_request p JOIN node n ON n.id = p.node_id
                 WHERE n.repo_node_id = %(r)s
            ) x WHERE x.source_created_at >= %(floor)s
            """,
            {"r": facts["node_id"], "floor": facts["window_floor"]},
        ).fetchone()["n"]
        if lowest is not None and all(n < lowest for n in numbers):
            return _check("query", "older_than_window", lowest_number_in_window=lowest)
    return _check("query", "not_in_graph")


def line(cov: dict[str, Any]) -> str | None:
    """One sentence for the text output of a refusal: what the window covered."""
    repo = cov.get("repository")
    if not repo or not repo.get("window_floor"):
        return None
    parts = [f"window began {repo['window_floor']:%Y-%m-%d}"]
    if repo.get("ingested_through"):
        parts.append(f"ingested through {repo['ingested_through']:%Y-%m-%d}")
    if repo.get("last_run_status"):
        parts.append(f"last run {repo['last_run_status']}")
    worst = next((c for c in cov["checks"] if c["status"] != "complete"), None)
    tail = f" ({worst['status']}: {worst['reason'].replace('_', ' ')})" if worst else ""
    return "Coverage: " + ", ".join(parts) + tail + ". No record is not the same as nothing happened."


def single_repository(conn) -> int | None:
    """The one ingested repository, or None when there are none or several."""
    rows = conn.execute("SELECT id FROM node WHERE node_type = 'repository'").fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def compute(conn, *, start_node_ids: list[int], query: str | None = None,
            matched: bool = True, repo_node_id: int | None = None) -> dict[str, Any]:
    """The `coverage` object for a document."""
    if repo_node_id is None and start_node_ids:
        row = conn.execute("SELECT repo_node_id FROM node WHERE id = %s",
                           (start_node_ids[0],)).fetchone()
        repo_node_id = row["repo_node_id"] if row else None
    if repo_node_id is None:
        repo_node_id = single_repository(conn)
    facts = repository_facts(conn, repo_node_id)
    checks = [node_check(conn, n, facts) for n in start_node_ids]
    if not matched and query is not None:
        checks.append(query_check(conn, query, facts))
    elif not checks:
        checks.append(repository_check(facts))
    return {"repository": facts and {k: v for k, v in facts.items() if k != "node_id"},
            "checks": checks}
