"""Measure candidate second repositories on the properties flask leaves unexercised (#127).

Read-only: one GraphQL query and two search calls per repository, through the `gh` CLI.

    python eval/second_repo_candidates.py urllib3/urllib3 scrapy/scrapy ...

What the columns are, and are not:

- `reviewed%` counts merged PRs with at least one review of any kind, comment-only included.
- `closes%` is GitHub's own `closingIssuesReferences`, which includes manually linked issues.
  It is not TraceMind's closing-keyword parser, so it bounds rather than predicts the
  Decision yield.
- Both are over the latest <=100 merged PRs updated since SINCE, not the whole window.
- Branch protection (whether review is *required*) is not visible without admin access.
"""
import json
import subprocess
import sys
import time

SINCE = "2025-10-16"

QUERY = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    hasWikiEnabled
    co1: object(expression:"HEAD:CODEOWNERS") { id }
    co2: object(expression:"HEAD:.github/CODEOWNERS") { id }
    co3: object(expression:"HEAD:docs/CODEOWNERS") { id }
    releases(last: 30, orderBy:{field:CREATED_AT, direction:ASC}) { nodes { publishedAt } }
    pullRequests(last: 100, states: MERGED, orderBy:{field:UPDATED_AT, direction:ASC}) {
      nodes {
        mergedAt
        author { login }
        reviews(first: 1) { totalCount }
        closingIssuesReferences(first: 1) { totalCount }
      }
    }
  }
}
"""


def gh(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


def search_count(q):
    return gh("api", "-X", "GET", "search/issues", "-f", f"q={q}", "-f", "per_page=1")["total_count"]


def main(repos):
    print("repo\tcodeowners\twiki\tmerged_prs\tclosed_issues\treleases\tsample\treviewed%\tcloses%\tbot%")
    for full in repos:
        time.sleep(4.5)  # the search API allows 30 requests a minute; each repo uses two
        owner, name = full.split("/")
        try:
            r = gh("api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}",
                   "-F", f"name={name}")["data"]["repository"]
            prs = [p for p in r["pullRequests"]["nodes"] if p["mergedAt"] and p["mergedAt"] >= SINCE]
            n = len(prs) or 1
            reviewed = sum(p["reviews"]["totalCount"] > 0 for p in prs)
            closes = sum(p["closingIssuesReferences"]["totalCount"] > 0 for p in prs)
            bots = sum(bool(p["author"]) and "bot" in p["author"]["login"].lower() for p in prs)
            rel = sum(bool(x["publishedAt"]) and x["publishedAt"] >= SINCE for x in r["releases"]["nodes"])
            owners = any(r[k] for k in ("co1", "co2", "co3"))
            merged = search_count(f"repo:{full} is:pr is:merged merged:>={SINCE}")
            issues = search_count(f"repo:{full} is:issue is:closed closed:>={SINCE}")
            print(f"{full}\t{owners}\t{r['hasWikiEnabled']}\t{merged}\t{issues}\t"
                  f"{rel}{'+' if rel == 30 else ''}\t{len(prs)}\t"
                  f"{100 * reviewed // n}\t{100 * closes // n}\t{100 * bots // n}")
        except Exception as exc:  # report and keep going; one bad name should not lose the rest
            print(f"{full}\tERROR {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
