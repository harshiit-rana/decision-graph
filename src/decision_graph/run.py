"""Ingestion orchestrator (PRD v3.1 §8).

Commit discipline is what makes resume real: the transaction is committed after each
page, immediately after that page's cursor is advanced. So the cursor on disk never
claims more progress than the graph actually contains. A run killed mid-page loses
that page and redoes it; a run killed between pages loses nothing. The reverse
ordering — commit the data, advance the cursor later — would let a crash skip a page
permanently, which is the kind of hole nothing downstream would ever notice.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone

import psycopg

from . import cursors, db, extractors, inference, locking, synthesis, threads
from .config import Settings
from .cursors import Strategy
from .extractors import Context
from .github import GitHubClient, RateLimitExhausted, parse_ts

log = logging.getLogger("decision_graph")

# Another writer holds the lock for this repository. Distinct from 1 (failed) so a scheduler can
# skip the round instead of reporting a failure, and from 0 so nobody mistakes it for a poll.
EXIT_BUSY = 3


def ingest(
    settings: Settings,
    *,
    resources: list[str],
    max_pages: int | None,
    reconcile: bool = False,
    synthesize: bool = True,
) -> int:
    """One poll, as the only writer for this repository (#163).

    If another `dg ingest`, or the webhook receiver's post-processing, holds the repository's lock,
    this exits with EXIT_BUSY and writes nothing.
    """
    conn = db.connect(settings.database_url)
    try:
        if not locking.try_writer_lock(conn, settings.target_repo):
            log.warning(
                "another ingestion or post-processing run for %s is in progress; exiting without "
                "writing. Try again when it finishes.", settings.target_repo,
            )
            return EXIT_BUSY
        return _ingest_locked(
            conn, settings, resources=resources, max_pages=max_pages,
            reconcile=reconcile, synthesize=synthesize,
        )
    finally:
        # Closing the connection is what releases the lock, including when a run dies.
        conn.close()


def _ingest_locked(
    conn,
    settings: Settings,
    *,
    resources: list[str],
    max_pages: int | None,
    reconcile: bool,
    synthesize: bool,
) -> int:
    client = GitHubClient(
        token=settings.github_token,
        user_agent=settings.user_agent,
        rate_limit_floor=settings.rate_limit_floor,
    )

    repo_node_id = extractors.extract_repository(conn, client, settings)
    conn.commit()

    run_id = db.start_run(conn, repo_node_id)
    conn.commit()

    ctx = Context(conn=conn, client=client, settings=settings, repo_node_id=repo_node_id)
    status, error = "succeeded", None

    try:
        if reconcile:
            log.info("--- reconcile stored bodies ---")
            extractors.reconcile_stored_bodies(ctx)
            conn.commit()
            # Retraction is the same re-derivation read the other way round: reconcile adds
            # what the current parser now produces, this withdraws what it no longer does
            # (issue #61). It runs here rather than on every ingestion because it is only
            # meaningful against stored text, and --reconcile is the pass that re-reads it.
            extractors.retract_stale_references(ctx)
            conn.commit()
            # The same for edges already resolved (#121, ADR 0009): this re-reads stored text,
            # so a difference is the extractor's change, which retracts.
            extractors.retract_unsupported_edges(ctx)
            conn.commit()

        for resource in resources:
            log.info("--- %s ---", resource)
            _ingest_resource(ctx, resource, run_id, max_pages)

        postprocess(ctx, synthesize=synthesize)

    except RateLimitExhausted as exc:
        conn.commit()  # cursors are already consistent; keep the progress
        status, error = "succeeded", f"stopped early: {exc}"
        log.warning("%s", exc)
    except KeyboardInterrupt:
        conn.commit()
        status, error = "succeeded", "interrupted by user; cursors preserved"
        log.warning("interrupted — progress preserved, re-run to resume")
    except Exception as exc:  # noqa: BLE001 - recorded on the run row, then re-raised
        conn.rollback()
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.exception("ingestion failed")
    finally:
        db.finish_run(
            conn, run_id, status=status, nodes=ctx.stats.nodes, edges=ctx.stats.edges,
            error=error, requests=client.requests_made,
        )
        conn.commit()
        client.close()

    _report(ctx, client)
    conn.close()
    return 0 if status == "succeeded" else 1


def postprocess(ctx: Context, *, synthesize: bool = True) -> None:
    """Turn what extraction wrote into answers: queued references, Decisions, evidence tiers.

    The one implementation, called by `dg ingest` after it polls and by the webhook receiver
    after a delivery (#162). Two copies would drift, and a delivery would leave the graph in a
    state a poll never does: new explicit edges with no Decision and no corroboration pass.
    """
    conn = ctx.conn
    # Drain queued forward references before inference (issue #3). Order matters:
    # these are explicit edges, and §5.3 only lets inference bridge gaps that
    # explicit extraction could not fill. Draining afterwards would let inference
    # propose an edge for a pair the queue was about to link explicitly.
    log.info("--- pending references ---")
    extractors.drain_pending_references(ctx)
    conn.commit()

    if synthesize:
        log.info("--- decision synthesis ---")
        # Retract first: a rule change or invalidated evidence must be able to
        # WITHDRAW a Decision, not merely fail to create new ones (issue #17).
        synthesis.retract_unsupported(conn)
        conn.commit()
        outcome = synthesis.synthesize(conn, ctx.repo_node_id)
        conn.commit()
        # Runs after promotion so a Decision created this run can be upgraded in the
        # same pass rather than waiting for the next one.
        upgraded = synthesis.upgrade_explicit(conn, ctx.repo_node_id)
        conn.commit()
        log.info(
            "decisions: %s created, %s refreshed, %s refused, %s upgraded to explicit",
            outcome.created,
            outcome.refreshed,
            outcome.refused,
            upgraded,
        )
        for refusal in outcome.refusals:
            log.warning("  refused %s", refusal)

    # Evidence Ranking (§5.4). Runs after synthesis so this run's new edges are
    # scored, and before inference so tier assignment never sees a speculative edge.
    # Idempotent and reversible: it withdraws the tier from threads whose supporting
    # evidence was invalidated, as well as granting it.
    tiers = conn.execute("SELECT * FROM apply_corroboration()").fetchone()
    conn.commit()
    if tiers and (tiers["upgraded"] or tiers["downgraded"]):
        log.info(
            "evidence tiers: %s upgraded to corroborated, %s withdrawn",
            tiers["upgraded"],
            tiers["downgraded"],
        )

    # Gated inference runs last: it may only bridge gaps that explicit extraction
    # left behind, so it must not run before extraction has had its say (§5.1).
    inference.persist(conn, inference.propose(conn, ctx.repo_node_id))
    conn.commit()


def _ingest_resource(ctx: Context, resource: str, run_id: int, max_pages: int | None) -> None:
    conn = ctx.conn

    if resource == "codeowners":
        extractors.extract_codeowners(ctx)
        conn.commit()
        return
    if resource == "wiki":
        extractors.extract_wiki(ctx)
        conn.commit()
        return

    cursor = cursors.load(conn, ctx.repo_node_id, resource, ctx.settings.backfill_months)
    cursors.bind_run(conn, cursor, run_id)
    conn.commit()

    params = cursors.query_params(cursor)
    path = _resource_path(ctx.settings.target_repo, resource)
    etag = cursor.last_etag if cursor.strategy is Strategy.FULL else None

    # `phase` is printed only by the strategy that owns it. For everything else it is NULL
    # (migration 0012) and saying so on every run would restate a non-fact — the same
    # misreading `dg status` used to invite (#47).
    if cursor.phase is not None:
        log.info("%s: phase=%s params=%s", resource, cursor.phase, dict(params))
    else:
        log.info("%s: params=%s", resource, dict(params))

    saw_any = False

    items_key = _items_key(resource)
    for page in ctx.client.paginate(path, params, etag=etag, max_pages=max_pages, items_key=items_key):
        if page.not_modified:
            log.info("%s: unchanged since last poll (304)", resource)
            return
        if not page.items:
            break

        saw_any = True
        timestamps = _process_page(ctx, resource, page.items)

        if timestamps:
            oldest, newest = min(timestamps), max(timestamps)
            if cursor.strategy is Strategy.COMMITTED_DESC and cursor.is_backfilling:
                cursors.advance_backfill(conn, cursor, oldest, newest)
            else:
                cursors.advance_steady(conn, cursor, newest, page.etag)

        cursors.set_etag(conn, cursor, page.etag)
        # Cursor and data land together. See module docstring.
        conn.commit()

    # Pages exhausted: for a windowed backfill that means the floor has been reached.
    if cursor.strategy is Strategy.COMMITTED_DESC and cursor.is_backfilling and max_pages is None:
        cursors.complete_backfill(conn, cursor)
    if not saw_any:
        log.info("%s: nothing new", resource)
    conn.commit()


def _process_page(ctx: Context, resource: str, items: list[dict]) -> list[datetime]:
    timestamps: list[datetime] = []

    for payload in items:
        if resource in ("issues", "pulls"):
            node_id = extractors.extract_issue_or_pr(ctx, payload)
            ts = parse_ts(payload.get("updated_at"))

            # Comments hang off issues AND pull requests, and the endpoint is the same
            # `/issues/{n}/comments` for both -- GitHub models a pull request as an issue
            # for discussion. Outside the `pull_request` branch below for that reason.
            extractors.extract_comments(ctx, payload["number"], node_id)

            if "pull_request" in payload:
                number = payload["number"]
                extractors.extract_reviews(ctx, number, node_id)
                extractors.link_pr_commits(ctx, number, node_id)

        elif resource == "commits":
            extractors.extract_commit(ctx, payload)
            ts = parse_ts(((payload.get("commit") or {}).get("committer") or {}).get("date"))

        elif resource == "releases":
            extractors.extract_release(ctx, payload)
            ts = parse_ts(payload.get("published_at"))

        elif resource == "workflows":
            extractors.extract_workflow(ctx, payload)
            ts = parse_ts(payload.get("updated_at"))

        else:
            raise ValueError(f"unknown resource: {resource}")

        if ts:
            timestamps.append(ts)

    return timestamps


def _resource_path(repo: str, resource: str) -> str:
    return {
        # /issues returns pull requests too; both cursors ride the same endpoint.
        "issues": f"/repos/{repo}/issues",
        "pulls": f"/repos/{repo}/issues",
        "commits": f"/repos/{repo}/commits",
        "releases": f"/repos/{repo}/releases",
        "workflows": f"/repos/{repo}/actions/workflows",
    }[resource]


def _items_key(resource: str) -> str | None:
    # Most list endpoints return a bare array. `actions/workflows` wraps its array
    # in an envelope object instead: {"total_count": N, "workflows": [...]}.
    return {"workflows": "workflows"}.get(resource)


def _report(ctx: Context, client: GitHubClient) -> None:
    log.info("")
    log.info("nodes upserted : %s", ctx.stats.nodes)
    log.info("edges created  : %s", ctx.stats.edges)
    log.info("API requests   : %s", client.requests_made)
    if ctx.stats.skipped:
        log.info("skipped (expected, not hidden):")
        for reason, count in sorted(ctx.stats.skipped.items(), key=lambda kv: -kv[1]):
            log.info("  %-36s %s", reason, count)


def parse_interval(text: str) -> float:
    """`3600`, `90s`, `30m` or `1h` as seconds. Refuses anything under a minute: a poll that
    often spends the API budget on asking whether anything changed."""
    t = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        seconds = float(t[:-1]) * units[t[-1]] if t and t[-1] in units else float(t)
    except ValueError:
        raise ValueError(f"not an interval: {text!r} (try 3600, 30m or 1h)") from None
    if seconds < 60:
        raise ValueError(f"interval {text!r} is under a minute; polling that often only burns the rate limit")
    return seconds


def schedule_loop(
    once: Callable[[], int],
    interval: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int | None = None,
) -> int:
    """Run `once` every `interval` seconds until interrupted (#163).

    A round that finds another writer is skipped, and a failed round is logged and followed by
    the next: a scheduler that exits on the first error is a scheduler that is down by morning.
    Every round is the ordinary resume path, so a rate-limit stop is picked up where it left off.
    """
    rounds = 0
    try:
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
            try:
                code = once()
            except Exception:  # noqa: BLE001 - logged, and the next round tries again
                log.exception("scheduled ingestion raised; will try again next round")
                code = 1
            if code == EXIT_BUSY:
                log.info("scheduled round skipped: another writer holds the lock")
            elif code != 0:
                log.warning("scheduled round failed (exit %s); next round in %s s", code, int(interval))
            if max_rounds is not None and rounds >= max_rounds:
                break
            sleep(interval)
    except KeyboardInterrupt:
        log.info("scheduler stopped")
    return 0


DEFAULT_RESOURCES = ["issues", "commits", "releases", "codeowners", "wiki", "workflows"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dg-ingest", description=__doc__)
    parser.add_argument("--repo", help="owner/name (default: pallets/flask)")
    parser.add_argument("--months", type=int, help="backfill window in months (default: 12)")
    parser.add_argument(
        "--resources", nargs="*", default=DEFAULT_RESOURCES, help="subset to ingest"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="stop each resource after N pages. For smoke tests — leaves the cursor "
        "mid-window on purpose so the next run demonstrates resume.",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="re-parse already-stored bodies and queue/link any reference lacking an "
        "edge, before polling. Costs no API calls. Recovers references dropped before "
        "the pending queue existed (issue #3).",
    )
    parser.add_argument(
        "--no-synthesis",
        action="store_true",
        help="skip promoting qualifying clusters to Decision nodes",
    )
    parser.add_argument(
        "--every",
        metavar="INTERVAL",
        help="poll repeatedly instead of once: 3600, 30m or 1h (at least a minute). Each round is "
        "the ordinary resume path; a round that finds another writer is skipped. Run it as its own "
        "process (the compose `scheduler` service), never inside the webhook receiver",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    interval = None
    if args.every:
        try:
            interval = parse_interval(args.every)
        except ValueError as exc:
            parser.error(str(exc))

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request at INFO, which buries the ingestion narrative.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        settings = Settings.from_env(target_repo=args.repo, backfill_months=args.months)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    log.info("target repo    : %s", settings.target_repo)
    log.info("backfill window: %s months", settings.backfill_months)

    def once() -> int:
        return ingest(
            settings,
            resources=args.resources,
            max_pages=args.max_pages,
            reconcile=args.reconcile,
            synthesize=not args.no_synthesis,
        )

    if interval is not None:
        log.info("scheduled: polling every %s s", int(interval))
        return schedule_loop(once, interval)
    return once()


if __name__ == "__main__":
    raise SystemExit(main())
