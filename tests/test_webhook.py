"""The webhook receiver (issue #112).

The signature check is the whole security boundary: without it this is an unauthenticated
write endpoint into the graph. Most of what follows is about that, and about the two ways a
receiver fails quietly -- accepting what it should not, and refusing what it must not, since
GitHub disables a hook after enough non-2xx replies.

Standalone. The server is driven over a real socket, and every case here returns before any
database or GitHub call, so none of it needs either.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from decision_graph import webhook
from decision_graph.config import Settings
from tests.superuser import requires_superuser

SECRET = "test-secret"
REPO = "pallets/flask"


def signed(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class VerifySignatureTest(unittest.TestCase):
    """Pure, and the part worth the most care."""

    BODY = b'{"action":"closed"}'

    def test_a_correct_signature_passes(self) -> None:
        self.assertTrue(
            webhook.verify_signature(SECRET, self.BODY, signed(SECRET, self.BODY))
        )

    def test_a_different_secret_fails(self) -> None:
        self.assertFalse(
            webhook.verify_signature(SECRET, self.BODY, signed("other", self.BODY))
        )

    def test_a_tampered_body_fails(self) -> None:
        # The signature is over the raw bytes, so one changed character invalidates it.
        self.assertFalse(
            webhook.verify_signature(SECRET, b'{"action":"opened"}', signed(SECRET, self.BODY))
        )

    def test_a_missing_header_fails(self) -> None:
        self.assertFalse(webhook.verify_signature(SECRET, self.BODY, None))
        self.assertFalse(webhook.verify_signature(SECRET, self.BODY, ""))

    def test_an_empty_secret_never_passes(self) -> None:
        # The configuration mistake that matters. A receiver that accepts everything must
        # not be reachable by leaving a variable unset, so this is False even when the
        # sender's own signature is internally consistent.
        body = self.BODY
        self.assertFalse(webhook.verify_signature("", body, signed("", body)))

    def test_the_algorithm_must_be_sha256(self) -> None:
        digest = signed(SECRET, self.BODY).split("=", 1)[1]
        self.assertFalse(webhook.verify_signature(SECRET, self.BODY, f"sha1={digest}"))
        self.assertFalse(webhook.verify_signature(SECRET, self.BODY, digest))

    def test_an_empty_digest_fails(self) -> None:
        self.assertFalse(webhook.verify_signature(SECRET, self.BODY, "sha256="))

    def test_a_garbage_header_fails_rather_than_raising(self) -> None:
        # A malformed header arrives from an unauthenticated sender, so it must be a False
        # and not an exception that becomes a 500.
        for header in ("=", "sha256", "sha256==", "!!!", "sha256=zzz"):
            with self.subTest(header=header):
                self.assertFalse(webhook.verify_signature(SECRET, self.BODY, header))


class HandlerRegistryTest(unittest.TestCase):
    def test_push_is_deliberately_absent(self) -> None:
        # Its commit objects are a different shape from the commits API, so extract_commit
        # would write subtly wrong nodes rather than fail. Pinned so adding it is a
        # decision rather than an afternoon's convenience.
        self.assertNotIn("push", webhook.HANDLERS)

    def test_the_events_that_are_handled(self) -> None:
        self.assertEqual(
            sorted(webhook.HANDLERS),
            ["issue_comment", "issues", "pull_request", "release"],
        )

    def test_an_unknown_event_is_none_not_an_error(self) -> None:
        # GitHub disables a hook after enough non-2xx replies, so erroring on `star` would
        # eventually switch off the deliveries that matter.
        self.assertIsNone(webhook.handle_event(None, "star", {}))


class ServeRefusesWithoutASecretTest(unittest.TestCase):
    def test_no_secret_is_a_refusal_to_start(self) -> None:
        settings = Settings(database_url="postgresql://x", github_token="x", target_repo=REPO)
        with self.assertRaises(RuntimeError) as caught:
            webhook.serve("127.0.0.1", 0, settings=settings, secret="")
        self.assertIn("GITHUB_WEBHOOK_SECRET", str(caught.exception))

    def test_the_message_says_why_rather_than_what(self) -> None:
        settings = Settings(database_url="postgresql://x", github_token="x", target_repo=REPO)
        with self.assertRaises(RuntimeError) as caught:
            webhook.serve("127.0.0.1", 0, settings=settings, secret="")
        self.assertIn("open write endpoint", str(caught.exception))


class LiveServerTest(unittest.TestCase):
    """Over a real socket. Every case returns before any database or GitHub call."""

    @classmethod
    def setUpClass(cls) -> None:
        settings = Settings(
            database_url="postgresql://unused", github_token="unused", target_repo=REPO
        )
        bound = type("_Bound", (webhook._Handler,), {"secret": SECRET, "settings": settings})
        cls.httpd = HTTPServer(("127.0.0.1", 0), bound)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, payload: dict, event: str, *, sign_with=SECRET, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", webhook.EVENT_HEADER: event}
        if sign_with is not None:
            headers[webhook.SIGNATURE_HEADER] = signed(sign_with, body)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/", data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_an_unsigned_delivery_is_refused(self) -> None:
        status, _ = self.post({"repository": {"full_name": REPO}}, "issues", sign_with=None)
        self.assertEqual(status, 401)

    def test_a_wrongly_signed_delivery_is_refused(self) -> None:
        status, _ = self.post({"repository": {"full_name": REPO}}, "issues", sign_with="nope")
        self.assertEqual(status, 401)

    def test_all_three_refusals_read_the_same(self) -> None:
        # Missing, malformed and wrong get one answer, so the reply does not tell a forger
        # which part of the attempt was closer.
        bodies = set()
        for sign_with in (None, "nope"):
            _, text = self.post({"repository": {"full_name": REPO}}, "issues",
                                sign_with=sign_with)
            bodies.add(text)
        self.assertEqual(len(bodies), 1, bodies)

    def test_an_unhandled_event_is_acknowledged(self) -> None:
        # 200, and before any I/O: this used to open a database connection and call
        # extract_repository only to discard both, answering 500 if either failed.
        status, text = self.post({"repository": {"full_name": REPO}}, "star")
        self.assertEqual(status, 200)
        self.assertIn("not handled", text)

    def test_a_delivery_for_another_repository_is_refused(self) -> None:
        # Otherwise a hook pointed here from elsewhere writes its artifacts into a graph
        # scoped to TARGET_REPO, where every per-repo figure then counts two things.
        status, _ = self.post({"repository": {"full_name": "someone/else"}}, "issues")
        self.assertEqual(status, 403)

    def test_a_body_that_is_not_json_is_a_400(self) -> None:
        status, text = self.post({}, "issues", raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("JSON", text)

    def test_a_json_body_that_is_not_an_object_is_a_400(self) -> None:
        status, _ = self.post({}, "issues", raw=b"[1,2,3]")
        self.assertEqual(status, 400)

    def test_the_signature_is_checked_before_the_body_is_parsed(self) -> None:
        # Unparseable AND unsigned must answer 401, not 400 -- parsing first hands an
        # unauthenticated body to the JSON decoder.
        status, _ = self.post({}, "issues", raw=b"{not json", sign_with=None)
        self.assertEqual(status, 401)

    def test_no_traceback_reaches_the_sender(self) -> None:
        status, text = self.post({"repository": {"full_name": REPO}}, "star")
        self.assertNotIn("Traceback", text)
        self.assertNotIn("decision_graph", text)


class BindingTest(unittest.TestCase):
    """The receiver is reachable only from this machine unless someone chose otherwise (#191)."""

    def test_the_function_default_is_loopback(self) -> None:
        import inspect

        self.assertEqual(inspect.signature(webhook.serve).parameters["host"].default, "127.0.0.1")
        self.assertEqual(webhook.DEFAULT_HOST, "127.0.0.1")

    def test_the_command_line_default_is_loopback(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf):
            webhook.main(["--help"])
        self.assertIn("default 127.0.0.1", buf.getvalue())
        self.assertNotIn("default 0.0.0.0", buf.getvalue())

    def test_serve_binds_the_host_it_is_given_and_warns_when_that_is_not_loopback(self) -> None:
        from unittest import mock

        settings = Settings(database_url="postgresql://unused", github_token="x", target_repo=REPO)
        for host, warns in (("127.0.0.1", False), ("0.0.0.0", True)):
            with self.subTest(host=host):
                with mock.patch.object(webhook, "_Server") as server, \
                     self.assertLogs("decision_graph.webhook", level="INFO") as logs:
                    server.return_value.serve_forever.side_effect = KeyboardInterrupt
                    with self.assertRaises(KeyboardInterrupt):
                        webhook.serve(host, 8123, settings=settings, secret="s")
                self.assertEqual(server.call_args[0][0], (host, 8123))
                said = "\n".join(logs.output)
                self.assertEqual("reachable from the network" in said, warns)
                if warns:
                    self.assertIn("no TLS", said)


class BodyCapTest(unittest.TestCase):
    """An oversized body is refused before it is read: the cap was never tested (#191)."""

    def test_the_cap_is_github_s_documented_delivery_limit(self) -> None:
        self.assertEqual(webhook.MAX_BODY_BYTES, 25 * 1024 * 1024)

    def test_an_oversized_content_length_is_a_413_without_reading_the_body(self) -> None:
        import socket

        settings = Settings(database_url="postgresql://unused", github_token="x", target_repo=REPO)
        bound = type("_Bound", (webhook._Handler,), {"secret": SECRET, "settings": settings})
        httpd = HTTPServer(("127.0.0.1", 0), bound)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                # Declares 100 MB and sends none of it. If the server tried to read the body it
                # would block until the timeout. A 413 comes straight back.
                s.sendall(
                    b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 104857600\r\n"
                    b"X-GitHub-Event: issues\r\n\r\n"
                )
                reply = s.recv(4096).decode("latin-1")
            self.assertIn("413", reply.split("\r\n")[0])
        finally:
            httpd.shutdown()
            httpd.server_close()


class MainArgumentTest(unittest.TestCase):
    """It takes arguments, so it parses them -- the #82 rule."""

    def test_help_exits_without_starting_a_server(self) -> None:
        import contextlib
        import io

        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stdout(io.StringIO()):
                webhook.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_an_unknown_flag_is_an_error(self) -> None:
        import contextlib
        import io

        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                webhook.main(["--nope"])
        self.assertEqual(caught.exception.code, 2)


# ---------------------------------------------------------------------------
# After a delivery (#162): the same post-processing a poll runs, debounced, after the reply
# ---------------------------------------------------------------------------


class PostProcessorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.calls: list[int] = []
        self.settings = Settings(database_url="postgresql://unused", github_token="x", target_repo=REPO)

    def make(self, interval: float = 30.0, runner=None) -> webhook.PostProcessor:
        return webhook.PostProcessor(
            self.settings, interval=interval, clock=lambda: self.now,
            runner=runner or (lambda settings, repo: self.calls.append(repo)))

    def test_nothing_runs_without_a_delivery(self) -> None:
        post = self.make()
        self.assertFalse(post.run_if_due())
        self.assertEqual(self.calls, [])

    def test_the_first_delivery_is_processed_at_once(self) -> None:
        post = self.make()
        post.request(7)
        self.assertTrue(post.run_if_due())
        self.assertEqual(self.calls, [7])

    def test_a_burst_inside_the_window_is_one_run_later_not_one_per_delivery(self) -> None:
        post = self.make(interval=30)
        post.request(7)
        post.run_if_due()
        for _ in range(20):
            post.request(7)
            self.now += 1
            self.assertFalse(post.run_if_due(), "still inside the window")
        self.assertEqual(len(self.calls), 1)
        self.now += 30
        self.assertTrue(post.run_if_due(), "the window has passed and work is pending")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(post.run_if_due(), "nothing new since")

    def test_a_failure_is_not_raised_and_stays_pending(self) -> None:
        def boom(settings, repo):
            raise RuntimeError("database went away")

        post = self.make(interval=30, runner=boom)
        post.request(7)
        with self.assertLogs("decision_graph.webhook", level="ERROR"):
            self.assertFalse(post.run_if_due())
        self.assertTrue(post.pending, "the next window tries again")

    def test_the_server_loop_runs_it_between_requests(self) -> None:
        post = self.make()
        server = webhook._Server(("127.0.0.1", 0), webhook._Handler)
        try:
            server.post = post
            post.request(3)
            server.service_actions()
            self.assertEqual(self.calls, [3])
        finally:
            server.server_close()


class FakeConn:
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class ReplyComesBeforeProcessingTest(unittest.TestCase):
    """A slow post-processing run must not hold up the 200 GitHub is waiting for."""

    def test_the_reply_is_sent_before_post_processing_finishes(self) -> None:
        from unittest import mock

        finished: list[float] = []

        def slow_runner(settings, repo) -> None:
            time.sleep(1.0)
            finished.append(time.monotonic())

        settings = Settings(database_url="postgresql://unused", github_token="x", target_repo=REPO)
        post = webhook.PostProcessor(settings, interval=0, runner=slow_runner)
        bound = type("_Bound", (webhook._Handler,), {"secret": SECRET, "settings": settings, "post": post})
        httpd = webhook._Server(("127.0.0.1", 0), bound)
        httpd.post = post
        port = httpd.server_address[1]
        thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        try:
            body = json.dumps({"repository": {"full_name": REPO}, "issue": {"number": 1}}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/", data=body,
                headers={webhook.EVENT_HEADER: "issues", webhook.SIGNATURE_HEADER: signed(SECRET, body)})
            with mock.patch.object(webhook.db, "connect", lambda dsn: FakeConn()), \
                 mock.patch.object(webhook, "GitHubClient", lambda **kw: object()), \
                 mock.patch.object(webhook.extractors, "extract_repository", lambda *a: 1), \
                 mock.patch.object(webhook, "handle_event", lambda *a: "issue #1"):
                with urllib.request.urlopen(request, timeout=10) as response:
                    replied = time.monotonic()
                    self.assertEqual(response.status, 200)
                deadline = time.monotonic() + 10
                while not finished and time.monotonic() < deadline:
                    time.sleep(0.05)
            self.assertTrue(finished, "post-processing never ran")
            self.assertLess(replied, finished[0] - 0.5, "the 200 waited for post-processing")
        finally:
            httpd.shutdown()
            httpd.server_close()


# ---------------------------------------------------------------------------
# Where corroboration's commit lists come from (#190)
# ---------------------------------------------------------------------------


class CommitListsComeFromRestTest(unittest.TestCase):
    """GitHub trimmed commit summaries from Events API payloads in October 2025. Whether webhook
    payloads are affected is unverified, so the receiver must not depend on either: a pull
    request's commits are fetched through the REST API, and the delivery's own list is ignored."""

    def test_the_commit_list_in_a_delivery_is_never_read(self) -> None:
        from unittest import mock

        payload = {
            "pull_request": {"number": 7, "title": "t", "merged_at": "2026-01-01T00:00:00Z",
                             "commits": 2, "commit_list": [{"sha": "deadbeef" * 5}]},
            "commits": [{"sha": "cafef00d" * 5, "message": "Fixes #1"}],
        }
        with mock.patch.object(webhook.extractors, "extract_issue_or_pr", return_value=11) as issue, \
             mock.patch.object(webhook.extractors, "extract_reviews") as reviews, \
             mock.patch.object(webhook.extractors, "link_pr_commits") as link, \
             mock.patch.object(webhook.extractors, "extract_comments") as comments:
            ctx = object()
            webhook._on_pull_request(ctx, payload)  # type: ignore[arg-type]

        link.assert_called_once_with(ctx, 7, 11)
        for mocked in (reviews, comments):
            mocked.assert_called_once_with(ctx, 7, 11)
        self.assertNotIn("cafef00d", repr(link.call_args_list))
        self.assertNotIn("deadbeef", repr(link.call_args_list))

    def test_the_receiver_never_names_a_commit_field_of_the_payload(self) -> None:
        import inspect
        import re

        code = re.sub(r'"""(?:.|\n)*?"""', "", inspect.getsource(webhook))
        code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
        for needle in ('["commits"]', "['commits']", '.get("commits")', ".get('commits')", '"commit_list"'):
            self.assertNotIn(needle, code, f"webhook.py reads a commit list from a delivery: {needle}")


# ---------------------------------------------------------------------------
# End to end against Postgres: a delivered merge becomes a Decision
# ---------------------------------------------------------------------------

DSN = os.environ.get("DATABASE_URL")


class FakeGitHub:
    """Answers the repository lookup and returns no comments, reviews or commits."""

    def __init__(self, **kwargs) -> None: ...

    def get(self, path: str, **kwargs):
        return {"node_id": "R_1", "full_name": REPO, "html_url": f"https://github.com/{REPO}",
                "default_branch": "main", "visibility": "public"}

    def paginate(self, *args, **kwargs):
        return iter(())

    def close(self) -> None: ...


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class DeliveredMergeBecomesADecisionTest(unittest.TestCase):
    """The #162 acceptance test: a delivered merge for a thread that satisfies the rubric ends in
    a Decision without anyone running `dg ingest`."""

    ISSUE, PR = 4101, 4102

    def test_issue_then_merged_pull_request(self) -> None:
        from unittest import mock

        from decision_graph import migrate
        from tests.test_roles import ScratchDatabase

        scratch = ScratchDatabase()
        httpd = None
        try:
            conn = scratch.connect()
            migrate.apply_all(conn, "db/migrations")
            conn.close()

            settings = Settings(database_url=scratch.dsn, github_token="x", target_repo=REPO)
            post = webhook.PostProcessor(settings, interval=0)
            bound = type("_Bound", (webhook._Handler,), {"secret": SECRET, "settings": settings, "post": post})
            httpd = webhook._Server(("127.0.0.1", 0), bound)
            httpd.post = post
            port = httpd.server_address[1]
            threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.05), daemon=True).start()

            def deliver(event: str, payload: dict) -> int:
                body = json.dumps(payload).encode()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/", data=body,
                    headers={webhook.EVENT_HEADER: event, webhook.SIGNATURE_HEADER: signed(SECRET, body)})
                with urllib.request.urlopen(request, timeout=20) as response:
                    return response.status

            def decisions() -> int:
                with scratch.connect() as c:
                    return c.execute("SELECT count(*) AS n FROM node WHERE node_type = 'decision'").fetchone()["n"]

            def wait_for(runs: int) -> None:
                deadline = time.monotonic() + 20
                while post.runs < runs and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertGreaterEqual(post.runs, runs, "post-processing did not run after the delivery")

            repo = {"full_name": REPO}
            issue = {"number": self.ISSUE, "node_id": "I", "title": "an issue", "state": "closed", "body": "",
                     "html_url": "https://x/i", "created_at": "2026-01-01T00:00:00Z",
                     "updated_at": "2026-02-01T00:00:00Z", "closed_at": "2026-02-01T00:00:00Z", "user": None}
            pr = {"number": self.PR, "node_id": "P", "title": "the fix", "state": "closed",
                  "body": f"Fixes #{self.ISSUE}", "html_url": "https://x/p",
                  "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z",
                  "closed_at": "2026-02-01T00:00:00Z", "merged_at": "2026-02-01T00:00:00Z", "merged": True,
                  "user": None}

            with mock.patch.object(webhook, "GitHubClient", FakeGitHub):
                self.assertEqual(deliver("issues", {"repository": repo, "issue": issue}), 200)
                wait_for(1)
                self.assertEqual(decisions(), 0, "an issue alone is not a decision")

                self.assertEqual(deliver("pull_request", {"repository": repo, "pull_request": pr}), 200)
                wait_for(2)

            self.assertEqual(decisions(), 1, "the delivered merge produced no Decision")
        finally:
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
            scratch.close()


if __name__ == "__main__":
    unittest.main()
