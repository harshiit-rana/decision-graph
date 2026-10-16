"""The random half of the urllib3 why-queries, drawn by a recorded procedure (#131).

Pre-registration: this script and its output are committed BEFORE urllib3 is ingested into
TraceMind. It reads GitHub only, through the `gh` CLI. It never touches the graph.

    python eval/urllib3/select_queries.py fetch    # writes pool.json, the exact input
    python eval/urllib3/select_queries.py draw     # writes draw.json from pool.json + SEED

The two steps are separate so that the INPUT is a committed file. Anyone can re-run `draw`
on the committed pool.json and must get the committed draw.json, whatever GitHub holds by
then. `fetch` is for auditing the pool against GitHub, and its output will drift as
issues are edited.

Eligibility (fixed before looking at the draw): a pull request that
- was merged within [MERGED_FROM, MERGED_TO], a window strictly inside the 12 months any
  ingestion made by 2026-11-01 will cover, so a few days' delay can't move a query outside
  the window;
- GitHub reports as closing at least one issue (`closingIssuesReferences`), which is GitHub's
  own linkage and not TraceMind's parser.

The draw is `random.Random(SEED).sample(sorted(eligible numbers), N_RANDOM)`. SEED is the
registration date as an integer, chosen before any draw so it can't be tuned.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = "urllib3/urllib3"
MERGED_FROM, MERGED_TO = "2025-11-01", "2026-09-30"
SEED = 20261017
N_RANDOM = 6

QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title mergedAt url
        author { login __typename }
        body
        closingIssuesReferences(first: 10) { nodes { number title state stateReason url } }
        reviews(first: 50) { totalCount nodes { author { login __typename } state } }
        commits { totalCount }
      }
    }
  }
}
"""


def gh_graphql(query: str, **variables: str) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-f", f"{k}={v}"]
    out = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout)["data"]


def fetch_pool() -> list[dict]:
    q = f"repo:{REPO} is:pr is:merged merged:{MERGED_FROM}..{MERGED_TO}"
    prs, after = [], None
    while True:
        data = gh_graphql(QUERY, q=q, after=after)["search"]
        prs += [n for n in data["nodes"] if n]
        if not data["pageInfo"]["hasNextPage"]:
            return prs
        after = data["pageInfo"]["endCursor"]


def draw(pool: dict) -> dict:
    """The draw, as a pure function of the committed pool, so a test can reproduce it."""
    eligible = sorted(p["number"] for p in pool["prs"] if p["closingIssuesReferences"]["nodes"])
    return {
        "seed": SEED,
        "n_random": N_RANDOM,
        "eligible_count": len(eligible),
        "eligible_numbers": eligible,
        "procedure": "random.Random(seed).sample(sorted(eligible_numbers), n_random)",
        "drawn_in_order": random.Random(SEED).sample(eligible, N_RANDOM),
    }


def main(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    if action == "fetch":
        prs = fetch_pool()
        pool = {
            "repo": REPO,
            "merged_window": [MERGED_FROM, MERGED_TO],
            "merged_prs_in_window": len(prs),
            "prs": sorted(prs, key=lambda p: p["number"]),
        }
        (HERE / "pool.json").write_text(json.dumps(pool, indent=1, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
        linked = sum(1 for p in prs if p["closingIssuesReferences"]["nodes"])
        print(f"{len(prs)} merged PRs in window; {linked} link at least one issue")
        return 0
    if action == "draw":
        pool = json.loads((HERE / "pool.json").read_text(encoding="utf-8"))
        out = draw(pool)
        (HERE / "draw.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        print(f"eligible: {out['eligible_count']}; drawn (in draw order): {out['drawn_in_order']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
