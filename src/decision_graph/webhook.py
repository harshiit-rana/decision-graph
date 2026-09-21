"""Webhook-driven ingestion (PRD §11, Phase 5) — issue #112.

`dg ingest` polls: it walks cursors forward and asks GitHub what changed. This reacts
instead, running the *same* extractors on a delivery. Nothing here is a second ingestion
path — every event ends in a function `run.py` already calls, for the reason `trace` gives
for having one renderer: two implementations drift exactly where it matters.

NOT A PRODUCTION SERVER. `http.server` is single-threaded and terminates no TLS. This is a
receiver to run behind something that does. Saying so here rather than letting a reader
infer otherwise from the fact that it listens on a port.

WHAT IT DELIBERATELY DOES NOT DO

It advances no cursor. A delivery tells you one thing changed; it cannot tell you nothing
else did, and writing a watermark from one would claim a completeness no webhook can offer
-- the same over-claim #93 removed from `dg status`. `dg ingest` stays the thing that
guarantees the window.

It does not handle `push`. That payload's commit objects are a different shape from the
commits API -- no `commit.committer.date` nesting, no `files` -- so `extract_commit` would
write subtly wrong nodes rather than fail. Refusing an event the system cannot read
correctly is the rule §5.1 applies to evidence, one layer down.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from . import db, extractors
from .config import Settings
from .github import GitHubClient

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"

# GitHub caps a delivery at 25MB. Read no more than that, so a lying Content-Length cannot
# ask this process to allocate arbitrary memory before the signature has been checked.
MAX_BODY_BYTES = 25 * 1024 * 1024


class WebhookError(Exception):
    """Refused. Carries the status the sender should see and nothing it should not."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Is this delivery signed by someone holding the secret?

    Three things here are load-bearing rather than stylistic:

    `hmac.compare_digest` — a `==` on a hex digest returns as soon as two characters differ,
    and the time it took says how many matched. That is a practical attack against a
    signature check reachable over the network.

    The raw body, before parsing. Verifying a re-serialised payload checks a string the
    sender never signed; parsing first hands an unauthenticated body to the JSON decoder.

    An empty secret is False, never True. The configuration mistake that matters is a
    receiver that accepts everything, and it must not be reachable by leaving a variable
    unset -- `serve` refuses to start without one, and this refuses to pass without one even
    if it is called directly.
    """
    if not secret or not header:
        return False

    algorithm, _, digest = header.partition("=")
    if algorithm != "sha256" or not digest:
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


# Each handler takes the ingestion Context and the delivery payload, and returns a sentence
# for the log. They call exactly what `run._process_page` calls.
Handler = Callable[[extractors.Context, dict[str, Any]], str]


def _on_issue(ctx: extractors.Context, payload: dict[str, Any]) -> str:
    issue = payload.get("issue") or {}
    node_id = extractors.extract_issue_or_pr(ctx, issue)
    number = issue.get("number")
    if number is not None:
        extractors.extract_comments(ctx, int(number), node_id)
    return f"issue #{number}"


def _on_pull_request(ctx: extractors.Context, payload: dict[str, Any]) -> str:
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    # The `pull_request` event nests merge state at the top level of its object, while
    # `extract_issue_or_pr` reads it from a `pull_request` sub-object -- the shape the
    # issues endpoint uses, and the difference that discarded 27 real merges in #16. Fed in
    # the shape the extractor documents rather than teaching it a second one.
    shaped = dict(pr)
    shaped["pull_request"] = {"merged_at": pr.get("merged_at")}
    node_id = extractors.extract_issue_or_pr(ctx, shaped)
    if number is not None:
        extractors.extract_reviews(ctx, int(number), node_id)
        extractors.link_pr_commits(ctx, int(number), node_id)
        extractors.extract_comments(ctx, int(number), node_id)
    return f"pull request #{number}"


def _on_issue_comment(ctx: extractors.Context, payload: dict[str, Any]) -> str:
    issue = payload.get("issue") or {}
    number = issue.get("number")
    if number is None:
        raise WebhookError(400, "issue_comment carried no issue number")
    # Re-reads the artifact's comments rather than inserting the one delivered. The
    # extractor is idempotent, an edit or a delete is reflected, and there is one code path
    # writing comments instead of two that must agree.
    node_id = extractors.extract_issue_or_pr(ctx, issue)
    stored = extractors.extract_comments(ctx, int(number), node_id)
    return f"{stored} comment(s) on #{number}"


def _on_release(ctx: extractors.Context, payload: dict[str, Any]) -> str:
    release = payload.get("release") or {}
    extractors.extract_release(ctx, release)
    return f"release {release.get('tag_name')}"


HANDLERS: dict[str, Handler] = {
    "issues": _on_issue,
    "pull_request": _on_pull_request,
    "issue_comment": _on_issue_comment,
    "release": _on_release,
}


def handle_event(ctx: extractors.Context, event: str, payload: dict[str, Any]) -> str | None:
    """Run the extractor for this event, or None when there is nothing to do.

    None rather than an error for an unhandled event. GitHub retries on a non-2xx and
    disables a hook after enough consecutive failures, so answering 400 to `star` or
    `workflow_run` would eventually switch off the deliveries that do matter.
    """
    handler = HANDLERS.get(event)
    if handler is None:
        return None
    return handler(ctx, payload)


def _repo_of(payload: dict[str, Any]) -> str | None:
    return (payload.get("repository") or {}).get("full_name")


class _Handler(BaseHTTPRequestHandler):
    server_version = "decision-graph-webhook"

    # Injected by `serve`; instances are created per request.
    secret: str = ""
    settings: Settings | None = None

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        try:
            body = self._read_body()
            self._check_signature(body)
            event = self.headers.get(EVENT_HEADER) or ""
            payload = self._parse(body)
            summary = self._ingest(event, payload)
        except WebhookError as exc:
            self._reply(exc.status, str(exc))
            return
        except Exception:
            # Logged with a traceback, answered without one: a stack trace in an HTTP body
            # tells an unauthenticated sender about the inside of this process.
            log.exception("webhook delivery failed")
            self._reply(500, "delivery failed")
            return

        self._reply(200, summary)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise WebhookError(400, "bad Content-Length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise WebhookError(413, "body too large")
        return self.rfile.read(length)

    def _check_signature(self, body: bytes) -> None:
        if not verify_signature(self.secret, body, self.headers.get(SIGNATURE_HEADER)):
            # The same answer for missing, malformed and wrong, so the reply does not say
            # which part of a forgery attempt was closer.
            raise WebhookError(401, "bad signature")

    def _parse(self, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebhookError(400, "body is not JSON") from None
        if not isinstance(payload, dict):
            raise WebhookError(400, "payload is not an object")
        return payload

    def _ingest(self, event: str, payload: dict[str, Any]) -> str:
        settings = self.settings
        assert settings is not None  # set by serve()

        delivered_for = _repo_of(payload)
        if delivered_for and delivered_for != settings.target_repo:
            # A hook pointed at this receiver from another repository would otherwise write
            # its artifacts into a graph scoped to TARGET_REPO, where every per-repo figure
            # would then be counting two things.
            raise WebhookError(403, "delivery is for a different repository")

        # Before any I/O. An unhandled event was opening a database connection and calling
        # extract_repository -- a GitHub round-trip -- only to discard both, and if either
        # failed the answer was 500. GitHub disables a hook after enough non-2xx replies, so
        # `star` could switch off the deliveries that matter. Found by driving the server
        # rather than by reading this function.
        if event not in HANDLERS:
            return f"{event}: not handled"

        conn = db.connect(settings.database_url)
        try:
            client = GitHubClient(token=settings.github_token, user_agent=settings.user_agent)
            repo_node_id = extractors.extract_repository(conn, client, settings)
            ctx = extractors.Context(
                conn=conn, client=client, settings=settings, repo_node_id=repo_node_id
            )
            summary = handle_event(ctx, event, payload)
            if summary is None:  # pragma: no cover - guarded above, kept as a belt
                conn.rollback()
                return f"{event}: not handled"
            conn.commit()
            log.info("webhook %s -> %s", event, summary)
            return f"{event}: {summary}"
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _reply(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # BaseHTTPRequestHandler writes to stderr with its own format; route it through
        # logging so a receiver's output looks like the rest of the tool's.
        log.info("%s - %s", self.address_string(), fmt % args)


def serve(host: str = "0.0.0.0", port: int = 8000, *, settings: Settings | None = None,
          secret: str | None = None) -> None:
    """Listen for deliveries. Refuses to start without a secret.

    That refusal is the point: a receiver with verification off is an unauthenticated write
    endpoint into the graph, and the way that happens in practice is an unset variable
    rather than a decision. It has to be impossible to configure by accident.
    """
    settings = settings or Settings.from_env()
    secret = secret if secret is not None else os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError(
            "GITHUB_WEBHOOK_SECRET is not set. Without it every delivery would be accepted "
            "unverified, which is an open write endpoint into the graph. Set the same value "
            "here and in the repository's webhook settings."
        )

    handler = type("_Bound", (_Handler,), {"secret": secret, "settings": settings})
    httpd = HTTPServer((host, port), handler)
    log.info("listening on %s:%s for %s", host, port, settings.target_repo)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="dg-webhook",
        description="Receive GitHub webhook deliveries and ingest them (PRD Phase 5).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    try:
        serve(args.host, args.port)
    except RuntimeError as exc:
        # Arguments are parsed above before any of this is read, so a mistyped flag reports
        # the typo rather than a missing secret (#82).
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
