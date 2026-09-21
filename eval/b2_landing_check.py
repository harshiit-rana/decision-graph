"""Is anything in bucket B2 actually a recall gap? (issue #104)

The recall audit splits the threads the §5.1 landing gate refused into B1 -- the issue was
closed `not_planned`, so no merged pull request exists to find and the gate was right -- and
B2, where the issue says the work *was* done. B2 is labelled a **candidate** gap, and that is
a hypothesis: if the issue is closed `completed` but nothing in its thread merged, either the
work landed by a route the graph did not connect, or it did not land at all.

`recall_audit.sql` cannot settle it. Whether a commit reached `main` is a question for GitHub,
and the answer is not in the graph. This is that question, asked the way the original audit
asked it by hand (eval/RECALL_AUDIT.md, "Two hypotheses from this document, now testable --
both wrong"), written down so it can be asked again.

`compare/{sha}...main` reports `behind` or `identical` when the sha is an ancestor of main --
the work landed -- and `diverged` when it never did.

Usage:

    GITHUB_TOKEN=... DATABASE_URL=... python eval/b2_landing_check.py

Exits 1 if any B2 commit is on main, because that is a real recall gap and this should say so
loudly rather than print it in a table. Needs a token, so it is deliberately not part of the
SQL suite CI runs -- the workflow declines to call the GitHub API for the same reason.

Arguments are parsed before the environment is read, so a typo reports the typo rather than a
missing DATABASE_URL, which sends you to look at something that was never wrong (#82).
"""

from __future__ import annotations

import argparse
import os
import sys

from decision_graph import db
from decision_graph.config import Settings
from decision_graph.github import GitHubClient

# The same CASE the audit buckets with, restricted to B2: has an issue, more than one member,
# an in-thread `closes` edge, nothing merged, and not declined. Kept as one query rather than
# reading the audit's temp views, which only exist inside a psql session.
B2_THREADS = """
SELECT t.thread_key,
       (SELECT string_agg(n.external_id, ',' ORDER BY n.external_id) FROM node n
         WHERE n.thread_key = t.thread_key AND n.node_type = 'issue')   AS issues,
       (SELECT array_agg(n.external_id ORDER BY n.external_id) FROM node n
         WHERE n.thread_key = t.thread_key AND n.node_type = 'commit')  AS commits
FROM (SELECT DISTINCT thread_key FROM node
       WHERE thread_key IS NOT NULL AND node_type <> 'decision') t
WHERE NOT thread_landed(t.thread_key)
  AND EXISTS (SELECT 1 FROM node n
               WHERE n.thread_key = t.thread_key AND n.node_type = 'issue')
  AND NOT EXISTS (SELECT 1 FROM node n JOIN issue i ON i.node_id = n.id
                   WHERE n.thread_key = t.thread_key
                     AND i.state_reason::text = 'not_planned')
  AND (SELECT count(*) FROM node n
        WHERE n.thread_key = t.thread_key AND n.node_type <> 'decision') > 1
  AND EXISTS (SELECT 1 FROM edge e
                JOIN node s ON s.id = e.src_node_id
                JOIN node d ON d.id = e.dst_node_id
               WHERE e.edge_type = 'closes' AND e.valid_to IS NULL
                 AND s.thread_key = t.thread_key
                 AND d.thread_key = t.thread_key)
ORDER BY t.thread_key
"""

ON_MAIN = {"behind", "identical"}


def build_parser() -> argparse.ArgumentParser:
    """Split out so a test can ask what this really accepts.

    It accepts nothing, which is exactly why it needs parsing: `python -m
    decision_graph.evaluation --help` once took an argv it never read and ran the entire
    §9 evaluation, overwriting the committed record (#82). A script that ignores its
    arguments runs on a typo, and this one spends API calls per commit.
    """
    return argparse.ArgumentParser(
        prog="b2_landing_check",
        description="Ask GitHub whether any bucket-B2 thread's work reached main (#104).",
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    conn = db.connect(dsn)
    client = GitHubClient(token=settings.github_token, user_agent=settings.user_agent)

    threads = conn.execute(B2_THREADS).fetchall()
    if not threads:
        print("No B2 threads: nothing the landing gate refused claims the work was done.")
        return 0

    print(f"B2 threads: {len(threads)}. Asking GitHub whether their commits reached main.\n")

    gaps: list[tuple[str, list[str]]] = []
    for row in threads:
        shas = row["commits"] or []
        verdicts = []
        for sha in shas:
            compared = client.get(f"/repos/{settings.target_repo}/compare/{sha}...main")
            # A sha GitHub cannot find is not evidence that work landed. Reported as its own
            # word rather than folded into `diverged`, which would claim more than we know.
            verdicts.append((sha[:7], compared.get("status") if compared else "unreachable"))

        landed = [sha for sha, status in verdicts if status in ON_MAIN]
        if landed:
            gaps.append((row["thread_key"], landed))

        print(
            f"  {row['thread_key']:<20} issue #{row['issues'] or '-':<8} "
            f"{'LANDED' if landed else 'no':<7} "
            + ", ".join(f"{sha}:{status}" for sha, status in verdicts)
        )

    print()
    if gaps:
        print(f"RECALL GAP: {len(gaps)} thread(s) carry work on main the graph did not link.")
        for key, shas in gaps:
            print(f"   {key}: {', '.join(shas)}")
        print()
        print("Each is a Decision the rubric should have reconstructed and did not.")
        return 1

    print("No recall gap. Every B2 commit diverged from main, so the landing gate was right")
    print("on all of them and B2 holds no actual miss on this corpus.")
    print()
    print("Read with the F check in recall_audit.sql: F empty and B2 clear together mean the")
    print("rubric has no detectable miss here. Neither says coverage is high -- 158 threads")
    print("reference no issue at all, and that is the binding limit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
