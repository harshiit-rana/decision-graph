"""How large does an impact walk get, and how long does it take? (#130)

    DATABASE_URL=... python eval/measure_traversal.py [--repo owner/name] [--sample N]

For every issue, pull request, commit and release (or a deterministic sample of N), runs the impact
walk at depth 2 and depth 3 and reports the distribution of path counts and latency (p50, p95, max)
and the five worst starts. Read-only; prints JSON.

The numbers belong to the issue that sets the path-cap default and fills in ADR 0008's storage
trigger. CI's database is empty, so it only checks that the script still runs. Nothing here is a
measurement of flask or urllib3 until it is run against them (#122, #132, #173).

What it does not establish: latency on any other machine or database size. It records wall-clock
milliseconds on whatever it ran on, with the row counts beside them, so the figure carries its own
caveat.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from decision_graph import db, reasoning  # noqa: E402


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def measure(conn, *, repo: str | None = None, sample: int | None = None,
            depths: tuple[int, ...] = (2, 3)) -> dict:
    params: list = []
    where = "n.node_type IN ('issue', 'pull_request', 'commit', 'release')"
    if repo:
        where += " AND n.repo_node_id = (SELECT id FROM node WHERE node_type = 'repository' AND external_id = %s)"
        params.append(repo)
    starts = [r["id"] for r in conn.execute(
        f"SELECT n.id FROM node n WHERE {where} ORDER BY n.id", params).fetchall()]
    if sample and len(starts) > sample:
        step = len(starts) / sample
        starts = [starts[int(i * step)] for i in range(sample)]  # deterministic, evenly spaced

    sizes = {
        "nodes": conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"],
        "edges": conn.execute("SELECT count(*) AS n FROM edge").fetchone()["n"],
    }
    out: dict = {"repo": repo, "starts_measured": len(starts), "database": sizes, "by_depth": {}}
    for depth in depths:
        counts, millis, worst = [], [], []
        for nid in starts:
            t = time.perf_counter()
            answer = reasoning.reason(conn, nid, reasoning.Mode.IMPACT, max_depth=depth)
            ms = (time.perf_counter() - t) * 1000
            n = len(answer.paths)
            counts.append(n)
            millis.append(ms)
            worst.append((n, round(ms, 1), nid))
        worst.sort(reverse=True)
        out["by_depth"][str(depth)] = {
            "paths": {"p50": percentile(counts, 0.5), "p95": percentile(counts, 0.95), "max": max(counts, default=0),
                      "mean": round(statistics.fmean(counts), 2) if counts else 0},
            "latency_ms": {"p50": round(percentile(millis, 0.5), 1), "p95": round(percentile(millis, 0.95), 1),
                           "max": round(max(millis, default=0), 1)},
            "worst_five": [{"paths": n, "ms": ms, "node_id": nid} for n, ms, nid in worst[:5]],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", help="owner/name; default: every repository in the database")
    p.add_argument("--sample", type=int, help="measure N evenly spaced starts instead of all")
    args = p.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = db.connect(dsn)
    try:
        print(json.dumps(measure(conn, repo=args.repo, sample=args.sample), indent=1))
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
