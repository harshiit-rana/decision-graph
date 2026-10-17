"""Inputs to the urllib3 predictions, computed from GitHub and TraceMind's own parsers (#131).

Pre-registration. Reads GitHub only. NOTHING here touches the graph, and urllib3 has not
been ingested. It applies the same pure functions the extractors use (`refs.closing_refs`,
`refs.mentioned_refs`, `refs.parse_codeowners`) to public text, then applies the rubric's
rules as a set computation, to say what ingestion should produce.

    python eval/urllib3/predict_inputs.py                      # writes predict_inputs.json
    python eval/urllib3/predict_inputs.py --ingest-date 2026-10-25
        # the window TraceMind will actually pin, written to predict_inputs.<date>.json

The window is NOT 12 calendar months. `cursors.load` pins the floor at first contact as
`now - 30 * backfill_months` days, so 12 months is 360 days. `--ingest-date D` sets the
window to [D - 360 days, D], which is what a run on day D will see. The committed default
(2025-10-17 to 2026-10-17) used 365 days: see the 2026-10-17 annotation in predictions.md.

What it can and cannot see, stated up front because predictions are only as good as this:

- It parses PR bodies AND the messages of every commit on each PR's branch
  (`commit_message_closing_keyword`), which `link_pr_commits` pulls into the PR's thread. It
  does NOT see closing keywords on default-branch squash commits that are outside a PR's
  commit list. A merged PR's thread needs only one such link to become a Decision.
- An issue is counted as ingested when GitHub says it was updated on or after WINDOW_FROM.
  Ingestion pulls issues by `updated_at`, so an issue closed before the window that a later
  PR links to would be skipped (`*_target_not_ingested`).
- A thread is a connected component of closing links between pull requests and issues.
  Two PRs closing one issue are one thread and so one Decision.
- "Landed" is GitHub's `merged_at`, as in the rubric.
- Corroboration follows README "Evidence Ranking": DECLARED and STRUCTURAL are present for
  any Decision thread (a body closing keyword, and a merged PR's commit list), so a thread is
  corroborated iff it also has ATTESTED (any review on a PR in the thread, any state, any
  reviewer, as `extract_reviews` records every one) or PUBLISHED (a PR or issue in the thread
  is cited by an in-window release body).
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from decision_graph import refs  # noqa: E402

REPO = "urllib3/urllib3"
WINDOW_FROM, WINDOW_TO = "2025-10-17", "2026-10-17"
OUT_NAME = "predict_inputs.json"


def set_ingest_date(date: str) -> None:
    """Re-aim the window at the one `cursors.load` would pin for a first run on `date`."""
    import datetime as dt

    global WINDOW_FROM, WINDOW_TO, OUT_NAME
    d = dt.date.fromisoformat(date)
    WINDOW_FROM, WINDOW_TO = (d - dt.timedelta(days=360)).isoformat(), d.isoformat()
    OUT_NAME = f"predict_inputs.{date}.json"

PR_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      number mergedAt body
      author { login __typename }
      closingIssuesReferences(first: 10) { nodes { number } }
      reviews { totalCount }
      commits { totalCount }
    } }
  }
}
"""


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def graphql(query: str, **variables) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-f", f"{k}={v}"]
    return json.loads(gh(*args))["data"]


def merged_prs() -> list[dict]:
    q = f"repo:{REPO} is:pr is:merged merged:{WINDOW_FROM}..{WINDOW_TO}"
    out, after = [], None
    while True:
        s = graphql(PR_QUERY, q=q, after=after)["search"]
        out += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            return out
        after = s["pageInfo"]["endCursor"]


def classify(numbers: list[int]) -> dict[int, dict]:
    """Issue or PR, and an issue's updatedAt, for each referenced number."""
    info: dict[int, dict] = {}
    owner, name = REPO.split("/")
    for i in range(0, len(numbers), 30):
        chunk = numbers[i:i + 30]
        body = " ".join(
            f"n{n}: issueOrPullRequest(number: {n}) {{ __typename "
            f"... on Issue {{ updatedAt state stateReason }} ... on PullRequest {{ merged }} }}"
            for n in chunk
        )
        data = graphql(f'query {{ repository(owner: "{owner}", name: "{name}") {{ {body} }} }}')
        for n in chunk:
            info[n] = data["repository"][f"n{n}"] or {"__typename": "missing"}
    return info


def commit_closing_refs(pr_number: int) -> set[int]:
    found: set[int] = set()
    page = 1
    while True:
        commits = json.loads(gh("api", f"repos/{REPO}/pulls/{pr_number}/commits?per_page=100&page={page}"))
        for c in commits:
            found |= refs.closing_refs(c["commit"]["message"] or "", REPO)
        if len(commits) < 100:
            return found
        page += 1


def release_refs() -> dict[str, list[int]]:
    out = {}
    for rel in json.loads(gh("api", f"repos/{REPO}/releases?per_page=100")):
        published = (rel.get("published_at") or "")[:10]
        if not (WINDOW_FROM <= published <= WINDOW_TO):
            continue
        body = rel.get("body") or ""
        nums = sorted(refs.mentioned_refs(body, REPO) | refs.closing_refs(body, REPO))
        out[rel["tag_name"]] = nums
    return out


def components(edges: list[tuple[str, str]]) -> list[set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, set[str]] = {}
    for node in parent:
        groups.setdefault(find(node), set()).add(node)
    return list(groups.values())


def main() -> int:
    prs = merged_prs()
    by_number = {p["number"]: p for p in prs}

    body_refs = {p["number"]: set(refs.closing_refs(p["body"] or "", REPO)) for p in prs}
    commit_refs = {p["number"]: commit_closing_refs(p["number"]) for p in prs}
    parsed = {n: sorted(body_refs[n] | commit_refs[n]) for n in body_refs}
    referenced = sorted({n for ns in parsed.values() for n in ns})
    info = classify(referenced)

    def ingested_issue(n: int) -> bool:
        i = info.get(n, {})
        return i.get("__typename") == "Issue" and (i.get("updatedAt") or "")[:10] >= WINDOW_FROM

    closes = [(f"pr:{p}", f"issue:{n}") for p, ns in parsed.items() for n in ns if ingested_issue(n)]
    skipped = {n: info[n].get("__typename") for n in referenced if not ingested_issue(n)}

    threads = [c for c in components(closes)]
    rel = release_refs()
    cited = {n for ns in rel.values() for n in ns}

    def attested(thread: set[str]) -> bool:
        return any(by_number[int(t[3:])]["reviews"]["totalCount"] > 0
                   for t in thread if t.startswith("pr:") and int(t[3:]) in by_number)

    def published(thread: set[str]) -> bool:
        return any(int(t.split(":")[1]) in cited for t in thread)

    decisions = []
    for t in threads:
        prs_in = sorted(int(x[3:]) for x in t if x.startswith("pr:"))
        issues_in = sorted(int(x[6:]) for x in t if x.startswith("issue:"))
        decisions.append({
            "prs": prs_in, "issues": issues_in,
            "attested": attested(t), "published": published(t),
            "corroborated": attested(t) or published(t),
        })
    decisions.sort(key=lambda d: d["prs"])

    owners = []
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        try:
            payload = json.loads(gh("api", f"repos/{REPO}/contents/{path}"))
        except subprocess.CalledProcessError:
            continue
        text = base64.b64decode(payload["content"]).decode("utf-8", "replace")
        rules = list(refs.parse_codeowners(text))
        owners = {
            "path": path, "sha": payload["sha"],
            "rules": [{"line": n, "pattern": p, "owners": o} for n, p, o in rules],
            "owns_edges": sum(len(o) for _, _, o in rules),
            "distinct_owners": sorted({x for _, _, o in rules for x in o}),
        }
        break

    github_linked = {p["number"] for p in prs if p["closingIssuesReferences"]["nodes"]}
    parsed_any = {n for n, ns in parsed.items() if ns}
    commit_only = sorted(n for n in parsed_any if not body_refs[n])
    out = {
        "window": [WINDOW_FROM, WINDOW_TO],
        "merged_prs": len(prs),
        "bot_authored_prs": sum(1 for p in prs if (p["author"] or {}).get("__typename") == "Bot"),
        "decision_prs_authored_by_bots": sorted(
            n for d in decisions for n in d["prs"]
            if (by_number[n]["author"] or {}).get("__typename") == "Bot"),
        "prs_with_any_review": sum(1 for p in prs if p["reviews"]["totalCount"] > 0),
        "github_linked_prs": len(github_linked),
        "prs_with_body_closing_keyword": sum(1 for n in body_refs if body_refs[n]),
        "prs_with_closing_keyword_only_in_a_commit_message": commit_only,
        "prs_with_any_closing_keyword": len(parsed_any),
        "linked_by_github_but_no_keyword_anywhere": sorted(github_linked - parsed_any),
        "keyword_but_not_github_linked": sorted(parsed_any - github_linked),
        "closing_refs_skipped_as_not_ingested_issue": skipped,
        "closes_edges_predicted": len(closes),
        "decision_threads": len(decisions),
        "decisions_with_corroboration": sum(d["corroborated"] for d in decisions),
        "decisions_attested": sum(d["attested"] for d in decisions),
        "decisions_published": sum(d["published"] for d in decisions),
        "release_citations": rel,
        "codeowners": owners,
        "decisions": decisions,
    }
    (HERE / OUT_NAME).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print({k: v for k, v in out.items() if k not in ("decisions", "release_citations", "codeowners",
                                                     "closing_refs_skipped_as_not_ingested_issue")})
    print("codeowners owns edges:", (owners or {}).get("owns_edges"))
    print("skipped refs:", skipped)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--ingest-date"]:
        set_ingest_date(sys.argv[2])
    raise SystemExit(main())
