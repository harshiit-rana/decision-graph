"""Resource extractors: GitHub payloads -> nodes + explicit edges.

Extraction-first (§5.1): every edge below is justified by a signal present in the
data — a closing keyword, a review event, a commit parent, a CODEOWNERS line. None
of them are guesses.

No extractor creates a Decision node. Phase 1 asserts artifacts and the links
between them; deciding that a *decision* occurred is a separate step gated by the
rubric, and doing it here would be exactly the "assert a decision without evidence"
failure the PRD's top risk row is about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from . import db, refs, threads
from .config import Settings
from .github import GitHubClient, parse_ts

log = logging.getLogger(__name__)


@dataclass
class Stats:
    nodes: int = 0
    edges: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def note_skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


@dataclass
class Context:
    conn: psycopg.Connection
    client: GitHubClient
    settings: Settings
    repo_node_id: int
    stats: Stats = field(default_factory=Stats)

    def node(self, **kwargs: Any) -> int:
        node_id = db.upsert_node(self.conn, repo_node_id=self.repo_node_id, **kwargs)
        self.stats.nodes += 1
        return node_id

    def edge(self, **kwargs: Any) -> int | None:
        edge_id, created = db.upsert_explicit_edge(self.conn, **kwargs)
        if created:
            self.stats.edges += 1
        return edge_id


# ---------------------------------------------------------------------------
# Repository (the root node; everything else hangs off it)
# ---------------------------------------------------------------------------


def extract_repository(conn: psycopg.Connection, client: GitHubClient, settings: Settings) -> int:
    payload = client.get(f"/repos/{settings.target_repo}")
    if payload is None:
        raise RuntimeError(f"repository {settings.target_repo} not found or not visible")

    node_id = db.upsert_node(
        conn,
        node_type="repository",
        external_id=settings.target_repo,
        github_node_id=payload.get("node_id"),
        title=payload.get("full_name"),
        url=payload.get("html_url"),
        source_created_at=parse_ts(payload.get("created_at")),
        source_updated_at=parse_ts(payload.get("updated_at")),
        raw=payload,
    )
    db.upsert_detail(
        conn,
        "repository",
        node_id,
        owner=settings.owner,
        name=settings.name,
        default_branch=payload.get("default_branch"),
        visibility=payload.get("visibility"),
    )
    # repo_node_id stays NULL on the repository node itself. It must not self-reference:
    # repo_node_id is part of node_identity_uidx (via COALESCE(repo_node_id, 0)), so
    # writing the id back into it moves the row's identity key out from under the next
    # run's lookup, and every subsequent run creates a duplicate repository node.
    return node_id


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def upsert_person(ctx: Context, payload: dict[str, Any] | None) -> int | None:
    """People are org-scoped, not repo-scoped, so repo_node_id stays NULL."""
    if not payload or not payload.get("login"):
        return None
    login = payload["login"]
    node_id = db.upsert_node(
        ctx.conn,
        node_type="person",
        repo_node_id=None,
        external_id=login,
        github_node_id=payload.get("node_id"),
        title=login,
        url=payload.get("html_url"),
    )
    db.upsert_detail(ctx.conn, "person", node_id, login=login)
    ctx.stats.nodes += 1
    return node_id


# ---------------------------------------------------------------------------
# Issues and pull requests
#
# One endpoint feeds both: GET /issues returns pull requests too, flagged by a
# "pull_request" key. Driving both off it keeps a single cursor and avoids paging
# the same conversation set twice.
# ---------------------------------------------------------------------------


def extract_issue_or_pr(ctx: Context, payload: dict[str, Any]) -> int:
    is_pr = "pull_request" in payload
    number = payload["number"]
    body = payload.get("body") or ""
    updated = parse_ts(payload.get("updated_at"))

    node_type = "pull_request" if is_pr else "issue"
    key = (
        threads.pr_key(ctx.repo_node_id, number)
        if is_pr
        else threads.issue_key(ctx.repo_node_id, number)
    )

    node_id = ctx.node(
        node_type=node_type,
        external_id=str(number),
        github_node_id=payload.get("node_id"),
        title=payload.get("title"),
        url=payload.get("html_url"),
        thread_key=key,
        source_created_at=parse_ts(payload.get("created_at")),
        source_updated_at=updated,
        raw=payload,
    )

    if is_pr:
        # Merge state is nested under the `pull_request` sub-object on this endpoint,
        # NOT at the top level. Reading payload["merged_at"] silently yields None, which
        # is how 27 real merges were discarded for the whole of Phase 1-3 (issue #16).
        # merged_at is what distinguishes work that landed from work that was refused,
        # and the §5.1 rubric depends on it.
        db.upsert_detail(
            ctx.conn,
            "pull_request",
            node_id,
            number=number,
            state=payload.get("state") or "unknown",
            body=body,
            closed_at=parse_ts(payload.get("closed_at")),
            merged_at=parse_ts((payload.get("pull_request") or {}).get("merged_at")),
        )
    else:
        # state_reason separates an issue that was resolved (`completed`) from one the
        # maintainers declined (`not_planned`). Without it, a refusal is indistinguishable
        # from a fix.
        db.upsert_detail(
            ctx.conn,
            "issue",
            node_id,
            number=number,
            state=payload.get("state") or "unknown",
            body=body,
            closed_at=parse_ts(payload.get("closed_at")),
            state_reason=payload.get("state_reason"),
        )

    # created: author -> artifact
    author_id = upsert_person(ctx, payload.get("user"))
    if author_id:
        ctx.edge(
            src=author_id,
            dst=node_id,
            edge_type="created",
            extractor="issue_author",
            source_ref=payload.get("html_url"),
            observed_at=parse_ts(payload.get("created_at")),
        )

    _link_body_refs(
        ctx, node_id, body, source_ref=payload.get("html_url"), observed_at=updated,
        source_type="pr_body",
    )
    return node_id


def _link_body_refs(
    ctx: Context,
    src_node_id: int,
    text: str,
    *,
    source_ref: str | None,
    observed_at: datetime | None,
    source_type: str,
) -> None:
    """Turn #-references in body text into edges.

    A closing keyword yields `closes` and unions the two threads — that union is what
    later lets a Decision satisfy rubric clause 3 across a PR and the issue it closed.
    A bare mention yields `references` and deliberately does NOT union: mentioning a
    neighbouring issue is not the same conversation, and merging on it would let
    clause 3 pass on unrelated work.

    A reference whose target is not in the graph yet is QUEUED, not dropped (issue #3).

    `source_type` ("pr_body" or "commit_message") is folded into the extractor label so a
    `closes` edge's provenance says which artifact it came from — a PR author's claim and a
    committer's claim are different evidence (issue #12).
    """
    for number, edge_type, extractor in [
        *(
            (n, "closes", f"{source_type}_closing_keyword")
            for n in refs.closing_refs(text, ctx.settings.target_repo)
        ),
        *(
            (n, "references", f"{source_type}_issue_mention")
            for n in refs.mentioned_refs(text, ctx.settings.target_repo)
        ),
    ]:
        target = _lookup_by_number(ctx, number)
        if target is None:
            _enqueue_reference(
                ctx,
                src_node_id=src_node_id,
                number=number,
                edge_type=edge_type,
                extractor=extractor,
                source_ref=source_ref,
                observed_at=observed_at,
            )
            continue
        _link(ctx, src_node_id, target, edge_type, extractor, source_ref, observed_at)


def _link(
    ctx: Context,
    src: int,
    dst: int,
    edge_type: str,
    extractor: str,
    source_ref: str | None,
    observed_at: datetime | None,
) -> int | None:
    """Create one reference edge, unioning threads for `closes` only."""
    edge_id = ctx.edge(
        src=src,
        dst=dst,
        edge_type=edge_type,
        extractor=extractor,
        source_ref=source_ref,
        observed_at=observed_at,
    )
    if edge_type == "closes":
        threads.union(ctx.conn, src, dst)
    return edge_id


def _enqueue_reference(
    ctx: Context,
    *,
    src_node_id: int,
    number: int,
    edge_type: str,
    extractor: str,
    source_ref: str | None,
    observed_at: datetime | None,
) -> None:
    """Record the intent to link, for the end-of-run drain (issue #3).

    Stores the reference NUMBER rather than a target id, because the target does not
    exist yet — that is the whole reason this row is being written.
    """
    ctx.conn.execute(
        """
        INSERT INTO pending_reference (repo_node_id, src_node_id, ref_number, edge_type,
                                       extractor, source_ref, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (src_node_id, ref_number, edge_type) WHERE resolved_at IS NULL
        DO NOTHING
        """,
        (
            ctx.repo_node_id,
            src_node_id,
            number,
            edge_type,
            extractor,
            source_ref,
            observed_at,
        ),
    )
    ctx.stats.note_skip(f"{edge_type}_ref_queued_for_resolution")


def drain_pending_references(ctx: Context) -> tuple[int, int]:
    """Retry queued references now the rest of the window has landed (issue #3).

    Returns (resolved, still_pending). Only the LOOKUP is retried — the missing target
    is never fetched — so the bounded backfill window stays bounded. A reference to an
    artifact genuinely outside the window simply never resolves, which is correct, and
    stays visible as a row with a climbing attempts count.
    """
    rows = ctx.conn.execute(
        """
        SELECT id, src_node_id, ref_number, edge_type, extractor, source_ref, observed_at
        FROM pending_reference
        WHERE repo_node_id = %s AND resolved_at IS NULL
        ORDER BY id
        """,
        (ctx.repo_node_id,),
    ).fetchall()

    resolved = 0
    for row in rows:
        target = _lookup_by_number(ctx, row["ref_number"])
        if target is None:
            ctx.conn.execute(
                "UPDATE pending_reference SET attempts = attempts + 1, "
                "last_attempt_at = now() WHERE id = %s",
                (row["id"],),
            )
            continue

        edge_id = _link(
            ctx,
            row["src_node_id"],
            target,
            row["edge_type"],
            row["extractor"],
            row["source_ref"],
            row["observed_at"],
        )
        if edge_id is None:
            # Self-reference; it will never resolve to a real edge. Count the attempt
            # rather than looping on it every run.
            ctx.conn.execute(
                "UPDATE pending_reference SET attempts = attempts + 1, "
                "last_attempt_at = now() WHERE id = %s",
                (row["id"],),
            )
            continue

        ctx.conn.execute(
            "UPDATE pending_reference SET resolved_at = now(), resolved_edge_id = %s, "
            "attempts = attempts + 1, last_attempt_at = now() WHERE id = %s",
            (edge_id, row["id"]),
        )
        resolved += 1

    still_pending = len(rows) - resolved
    if rows:
        log.info(
            "pending references: %s resolved, %s still open", resolved, still_pending
        )
    return resolved, still_pending


def reconcile_stored_bodies(ctx: Context) -> int:
    """Re-parse already-ingested bodies and queue any reference that has no edge.

    Recovery path for references dropped before the queue existed. Costs no API calls:
    the bodies are already in the graph, so this re-derives the links purely from stored
    text. Also a safety net if a parser rule is ever widened — reconcile picks up what
    the new rule now matches, without re-polling GitHub.
    """
    sources = (
        ("pull_request", "SELECT node_id AS id, body AS text FROM pull_request"),
        ("issue", "SELECT node_id AS id, body AS text FROM issue"),
        ("commit", "SELECT node_id AS id, message AS text FROM commit"),
    )

    source_type_by_label = {
        "pull_request": "pr_body",
        "issue": "pr_body",
        "commit": "commit_message",
    }

    queued = 0
    for label, query in sources:
        source_type = source_type_by_label[label]
        for row in ctx.conn.execute(query).fetchall():
            text = row["text"]
            if not text:
                continue
            for number, edge_type, extractor in [
                *(
                    (n, "closes", f"{source_type}_closing_keyword")
                    for n in refs.closing_refs(text, ctx.settings.target_repo)
                ),
                *(
                    (n, "references", f"{source_type}_issue_mention")
                    for n in refs.mentioned_refs(text, ctx.settings.target_repo)
                ),
            ]:
                target = _lookup_by_number(ctx, number)
                if target is not None:
                    # Idempotent: an edge that already exists is refreshed, not doubled.
                    before = ctx.stats.edges
                    _link(ctx, row["id"], target, edge_type, extractor, None, None)
                    queued += ctx.stats.edges - before
                    continue
                _enqueue_reference(
                    ctx,
                    src_node_id=row["id"],
                    number=number,
                    edge_type=edge_type,
                    extractor=extractor,
                    source_ref=None,
                    observed_at=None,
                )
        log.info("reconciled %s bodies", label)

    return queued


def _lookup_by_number(ctx: Context, number: int) -> int | None:
    """Resolve #N to an already-ingested issue or PR.

    Returns None for two different situations, which the caller must not conflate:
    the target is outside the backfill window (permanent, correct), or it is inside the
    window but has not been written yet (transient — issues are walked
    updated-ascending, so a PR can be processed before the issue it closes).

    Callers therefore queue rather than drop; drain_pending_references sorts out which
    was which by retrying once the window has landed. We never fetch the missing target,
    as that would make the window unbounded by the back door.
    """
    row = ctx.conn.execute(
        """
        SELECT id FROM node
        WHERE repo_node_id = %s AND external_id = %s
          AND node_type IN ('issue', 'pull_request')
        ORDER BY node_type
        LIMIT 1
        """,
        (ctx.repo_node_id, str(number)),
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Reviews (per PR; no independent cursor)
# ---------------------------------------------------------------------------


def extract_reviews(ctx: Context, pr_number: int, pr_node_id: int) -> None:
    for page in ctx.client.paginate(
        f"/repos/{ctx.settings.target_repo}/pulls/{pr_number}/reviews"
    ):
        for review in page.items:
            reviewer_id = upsert_person(ctx, review.get("user"))
            if not reviewer_id:
                continue
            # `reviewed` is one of the two Validation signals in the rubric, so it is
            # recorded for every review state, not just APPROVED — a CHANGES_REQUESTED
            # review is still evidence the change was scrutinised.
            ctx.edge(
                src=reviewer_id,
                dst=pr_node_id,
                edge_type="reviewed",
                extractor="pr_review",
                source_ref=f"review:{review.get('id')}:{review.get('state')}",
                observed_at=parse_ts(review.get("submitted_at")),
            )


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


def extract_commit(ctx: Context, payload: dict[str, Any]) -> int:
    sha = payload["sha"]
    commit = payload.get("commit") or {}
    message = commit.get("message") or ""
    authored = parse_ts((commit.get("author") or {}).get("date"))
    committed = parse_ts((commit.get("committer") or {}).get("date"))

    node_id = ctx.node(
        node_type="commit",
        external_id=sha,
        github_node_id=payload.get("node_id"),
        title=message.split("\n", 1)[0][:200],
        url=payload.get("html_url"),
        source_created_at=authored,
        source_updated_at=committed,
        raw=payload,
    )
    db.upsert_detail(
        ctx.conn,
        "commit",
        node_id,
        sha=sha,
        message=message,
        authored_at=authored,
        committed_at=committed,
    )

    author_id = upsert_person(ctx, payload.get("author"))
    if author_id:
        ctx.edge(
            src=author_id,
            dst=node_id,
            edge_type="created",
            extractor="commit_author",
            source_ref=sha,
            observed_at=authored,
        )

    # Commit messages carry the same closing keywords as PR bodies, and on flask this
    # is a primary source of issue linkage.
    _link_body_refs(
        ctx, node_id, message, source_ref=sha, observed_at=committed,
        source_type="commit_message",
    )

    for parent in payload.get("parents") or []:
        parent_row = ctx.conn.execute(
            "SELECT id FROM node WHERE repo_node_id = %s AND node_type = 'commit' "
            "AND external_id = %s",
            (ctx.repo_node_id, parent.get("sha")),
        ).fetchone()
        if parent_row:
            ctx.edge(
                src=node_id,
                dst=parent_row["id"],
                edge_type="depends_on",
                extractor="commit_parent",
                source_ref=parent.get("sha"),
                observed_at=committed,
            )
        else:
            # Expected at the window boundary: the oldest commits in the window have
            # parents outside it.
            ctx.stats.note_skip("commit_parent_outside_window")

    return node_id


def link_pr_commits(ctx: Context, pr_number: int, pr_node_id: int) -> None:
    """`implements` edges PR -> its commits, and pull those commits into the thread."""
    for page in ctx.client.paginate(
        f"/repos/{ctx.settings.target_repo}/pulls/{pr_number}/commits"
    ):
        for payload in page.items:
            commit_node_id = extract_commit(ctx, payload)
            ctx.edge(
                src=pr_node_id,
                dst=commit_node_id,
                edge_type="implements",
                extractor="pr_commit_list",
                source_ref=payload.get("sha"),
            )
            threads.union(ctx.conn, pr_node_id, commit_node_id)


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


def extract_release(ctx: Context, payload: dict[str, Any]) -> int:
    tag = payload.get("tag_name") or str(payload.get("id"))
    published = parse_ts(payload.get("published_at"))
    body = payload.get("body") or ""

    node_id = ctx.node(
        node_type="release",
        external_id=tag,
        github_node_id=payload.get("node_id"),
        title=payload.get("name") or tag,
        url=payload.get("html_url"),
        source_created_at=parse_ts(payload.get("created_at")),
        source_updated_at=published,
        raw=payload,
    )
    db.upsert_detail(
        ctx.conn,
        "release",
        node_id,
        tag_name=tag,
        body=body,
        is_prerelease=bool(payload.get("prerelease")),
        published_at=published,
    )

    # Release notes referencing a PR are a `deployed_by` signal: this is where the
    # change reached users. Release notes are also the classic source of an *explicit*
    # Decision in §5.1 — but creating that node is Phase 2+, not here.
    repo = ctx.settings.target_repo
    for number in refs.mentioned_refs(body, repo) | refs.closing_refs(body, repo):
        target = _lookup_by_number(ctx, number)
        if target is None:
            ctx.stats.note_skip("release_ref_target_not_ingested")
            continue
        ctx.edge(
            src=target,
            dst=node_id,
            edge_type="deployed_by",
            extractor="release_notes_reference",
            source_ref=tag,
            observed_at=published,
        )
    return node_id


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def extract_workflow(ctx: Context, payload: dict[str, Any]) -> int:
    path = payload.get("path") or str(payload.get("id"))
    node_id = ctx.node(
        node_type="workflow",
        external_id=path,
        github_node_id=payload.get("node_id"),
        title=payload.get("name"),
        url=payload.get("html_url"),
        source_created_at=parse_ts(payload.get("created_at")),
        source_updated_at=parse_ts(payload.get("updated_at")),
        raw=payload,
    )
    db.upsert_detail(
        ctx.conn, "workflow", node_id, path=path, name=payload.get("name"), state=payload.get("state")
    )
    return node_id


# ---------------------------------------------------------------------------
# CODEOWNERS  —  no-op on pallets/flask (issue #1)
# ---------------------------------------------------------------------------

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def extract_codeowners(ctx: Context) -> bool:
    """Build `owns` edges from CODEOWNERS. Returns False when the repo has no such file.

    KNOWN LIMITATION: pallets/flask ships no CODEOWNERS at any of the three canonical
    paths, so this returns False every run and the graph gets no `owns` edges. The
    parser is still built and exercised by unit tests because the gap belongs to the
    target repo, not the system. Consequence for §9: the evaluation set cannot include
    ownership queries against flask.
    """
    import base64

    for path in CODEOWNERS_PATHS:
        payload = ctx.client.get(f"/repos/{ctx.settings.target_repo}/contents/{path}")
        if payload is None:
            continue

        content = base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
        for lineno, pattern, owners in refs.parse_codeowners(content):
            scope_id = ctx.node(
                node_type="codeowners_scope",
                external_id=f"{path}:{lineno}",
                title=pattern,
            )
            db.upsert_detail(
                ctx.conn,
                "codeowners_scope",
                scope_id,
                path_pattern=pattern,
                source_path=path,
                source_sha=payload.get("sha"),
                line_number=lineno,
            )
            for owner in owners:
                if "/" in owner:  # org/team
                    org, slug = owner.split("/", 1)
                    owner_id = ctx.node(
                        node_type="team", repo_node_id=None, external_id=owner, title=owner
                    )
                    db.upsert_detail(ctx.conn, "team", owner_id, org=org, slug=slug)
                else:
                    owner_id = upsert_person(ctx, {"login": owner})
                if owner_id:
                    ctx.edge(
                        src=owner_id,
                        dst=scope_id,
                        edge_type="owns",
                        extractor="codeowners_parse",
                        source_ref=f"{path}:{lineno}",
                    )
        return True

    log.info("no CODEOWNERS file in %s — `owns` extractor no-ops", ctx.settings.target_repo)
    ctx.stats.note_skip("codeowners_absent")
    return False


def extract_wiki(ctx: Context) -> bool:
    """Wiki pages are not exposed by the REST API and require cloning `<repo>.wiki.git`.

    KNOWN LIMITATION: pallets/flask has has_wiki=false, so there is nothing to clone.
    Left as an explicit no-op rather than an unimplemented gap. Consequence for §5.1:
    on this repo `motivated_by` resolves only to issues and PR bodies, never wiki pages.
    """
    log.info("wiki ingestion unsupported for %s — no-op", ctx.settings.target_repo)
    ctx.stats.note_skip("wiki_unsupported")
    return False
