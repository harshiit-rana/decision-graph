"""How often does a Why answer belong to a different thread than the artifact asked about? (#129)

    DATABASE_URL=... python eval/measure_cross_thread.py [--repo owner/name] [--depth 3]

For every issue, pull request and commit in the graph, runs the same Why-walk `dg query` runs and
counts the answers by scope:

- `own_thread`: every Decision reached belongs to the artifact's own thread;
- `other_thread`: every one was reached by crossing into a different thread;
- `mixed`: both;
- `none`: the walk reached no Decision (a refusal, or a Decision-less path).

For the crossing answers it also counts which edge type was crossed. A bare `references` mention is
the case the issue is about, but nothing assumes it: the walk can cross on any edge in the Why set.

Read-only. It prints JSON, and the numbers go in the issue that decides the fix; they are NOT
produced by this repository's CI, whose database is empty, so there is nothing to commit until the
flask graph is restored (#122) and urllib3 is ingested (#132).

What the counts do not establish: how many crossing answers a reader would have been misled by. A
crossing answer is one where the Decision is another thread's, which is a fact about the graph,
not about any reader. Prevalence tells which rule to choose; it doesn't judge the rule.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from decision_graph import db, reasoning  # noqa: E402


def measure(conn, *, repo: str | None = None, depth: int = reasoning.DEFAULT_MAX_DEPTH) -> dict:
    params: list = []
    where = "n.node_type IN ('issue', 'pull_request', 'commit')"
    if repo:
        where += " AND n.repo_node_id = (SELECT id FROM node WHERE node_type = 'repository' AND external_id = %s)"
        params.append(repo)
    starts = conn.execute(f"SELECT n.id FROM node n WHERE {where} ORDER BY n.id", params).fetchall()

    scopes: Counter = Counter()
    crossed_on: Counter = Counter()
    worst: list[dict] = []
    for row in starts:
        answer = reasoning.reason(conn, row["id"], reasoning.Mode.WHY, max_depth=depth)
        scopes[answer.scope] += 1
        for path in answer.foreign_decision_paths():
            crossed_on[path.thread_crossing.edge_type] += 1
        if answer.scope in ("other_thread", "mixed"):
            start = answer.paths[0].ref(answer.start_node_id)
            worst.append({"node_id": row["id"], "type": start.node_type, "external_id": start.external_id,
                          "scope": answer.scope})
    return {
        "repo": repo,
        "depth": depth,
        "artifacts_asked": len(starts),
        "by_scope": dict(scopes),
        "crossing_paths_by_edge_type": dict(crossed_on),
        "examples": worst[:10],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", help="owner/name; default: every repository in the database")
    p.add_argument("--depth", type=int, default=reasoning.DEFAULT_MAX_DEPTH)
    args = p.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = db.connect(dsn)
    try:
        print(json.dumps(measure(conn, repo=args.repo, depth=args.depth), indent=1))
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
