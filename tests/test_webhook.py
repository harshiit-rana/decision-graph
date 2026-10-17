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
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from decision_graph import webhook
from decision_graph.config import Settings

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
                with mock.patch.object(webhook, "HTTPServer") as server, \
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


if __name__ == "__main__":
    unittest.main()
