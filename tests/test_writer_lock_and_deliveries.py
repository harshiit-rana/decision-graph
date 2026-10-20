"""One writer at a time (#163) and a delivery is applied once (#164), per ADR 0008.

The pure parts (interval parsing, the scheduling loop, the age text) run anywhere. The lock needs
only DATABASE_URL, on its own throwaway lock keys. The delivery log and the pre-0017 fallback build
their own databases next to DATABASE_URL's, so they need a superuser and skip as dg_app.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from unittest import mock

from decision_graph import cli, locking, migrate, run, webhook
from decision_graph.config import Settings
from tests.superuser import requires_superuser

DSN = os.environ.get("DATABASE_URL")
SECRET = "lock-test-secret"
REPO = "pallets/flask"


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


class IntervalTest(unittest.TestCase):
    def test_the_forms_a_person_types(self) -> None:
        self.assertEqual(run.parse_interval("3600"), 3600)
        self.assertEqual(run.parse_interval("30m"), 1800)
        self.assertEqual(run.parse_interval("1h"), 3600)
        self.assertEqual(run.parse_interval("90S"), 90)
        self.assertEqual(run.parse_interval("1d"), 86400)

    def test_under_a_minute_is_refused(self) -> None:
        for text in ("59", "30s", "0", "0.5m"):
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                run.parse_interval(text)
            self.assertIn("minute", str(caught.exception))

    def test_nonsense_is_refused_with_a_hint(self) -> None:
        for text in ("", "soon", "h", "1x"):
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                run.parse_interval(text)
            self.assertIn("not an interval", str(caught.exception))


class ScheduleLoopTest(unittest.TestCase):
    def loop(self, codes, **kw):
        sleeps: list[float] = []
        outcomes = iter(codes)

        def once() -> int:
            value = next(outcomes)
            if isinstance(value, Exception):
                raise value
            return value

        records: list[str] = []

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler, logger = Collect(), logging.getLogger("decision_graph")
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            result = run.schedule_loop(once, 3600, sleep=sleeps.append, max_rounds=len(codes))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        return result, sleeps, "\n".join(records)

    def test_it_polls_every_round_and_sleeps_between_them(self) -> None:
        result, sleeps, _ = self.loop([0, 0, 0])
        self.assertEqual(result, 0)
        self.assertEqual(sleeps, [3600, 3600], "between rounds, not after the last")

    def test_a_busy_round_is_skipped_and_said_so(self) -> None:
        _, sleeps, log = self.loop([run.EXIT_BUSY, 0])
        self.assertEqual(len(sleeps), 1)
        self.assertIn("skipped", log)

    def test_a_failed_or_raising_round_does_not_stop_the_scheduler(self) -> None:
        _, sleeps, log = self.loop([1, RuntimeError("boom"), 0])
        self.assertEqual(len(sleeps), 2, "all three rounds ran")
        self.assertIn("failed", log)
        self.assertIn("raised", log)

    def test_an_interrupt_stops_it_cleanly(self) -> None:
        def once() -> int:
            raise KeyboardInterrupt

        with self.assertLogs("decision_graph", level="INFO"):
            self.assertEqual(run.schedule_loop(once, 60, sleep=lambda s: None), 0)


class AgeTextTest(unittest.TestCase):
    def test_ages(self) -> None:
        self.assertEqual(cli.age_text(timedelta(seconds=10)), "just now")
        self.assertEqual(cli.age_text(timedelta(minutes=5)), "5 min ago")
        self.assertEqual(cli.age_text(timedelta(hours=3, minutes=12)), "3 h 12 min ago")
        self.assertEqual(cli.age_text(timedelta(days=5)), "5 days ago")
        self.assertEqual(cli.age_text(timedelta(seconds=-5)), "just now", "a skewed clock must not print a negative age")


# ---------------------------------------------------------------------------
# The advisory lock
# ---------------------------------------------------------------------------


@unittest.skipUnless(DSN, "DATABASE_URL not set")
class WriterLockTest(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.repo = "zz-lock-test/" + uuid.uuid4().hex[:8]
        self.connect = lambda: psycopg.connect(DSN, row_factory=dict_row)
        self.a, self.b = self.connect(), self.connect()

    def tearDown(self) -> None:
        self.a.close()
        self.b.close()

    def test_a_second_writer_is_refused_without_waiting(self) -> None:
        self.assertTrue(locking.try_writer_lock(self.a, self.repo))
        started = time.monotonic()
        self.assertFalse(locking.try_writer_lock(self.b, self.repo))
        self.assertLess(time.monotonic() - started, 2, "it must not block")

    def test_the_lock_is_per_repository(self) -> None:
        self.assertTrue(locking.try_writer_lock(self.a, self.repo))
        self.assertTrue(locking.try_writer_lock(self.b, self.repo + "-other"))

    def test_releasing_lets_the_next_writer_in(self) -> None:
        locking.try_writer_lock(self.a, self.repo)
        locking.release_writer_lock(self.a, self.repo)
        self.assertTrue(locking.try_writer_lock(self.b, self.repo))

    def test_the_context_manager_releases_on_exit_even_on_error(self) -> None:
        with self.assertRaises(RuntimeError):
            with locking.writer_lock(self.a, self.repo) as got:
                self.assertTrue(got)
                raise RuntimeError("the run died")
        self.assertTrue(locking.try_writer_lock(self.b, self.repo))

    def test_a_commit_does_not_release_it(self) -> None:
        # ingest commits after every page; the lock has to survive that.
        locking.try_writer_lock(self.a, self.repo)
        self.a.commit()
        self.assertFalse(locking.try_writer_lock(self.b, self.repo))

    def test_a_killed_process_frees_it(self) -> None:
        code = (
            "import sys, time, psycopg\n"
            "from decision_graph import locking\n"
            f"c = psycopg.connect({DSN!r}, row_factory=psycopg.rows.dict_row)\n"
            f"assert locking.try_writer_lock(c, {self.repo!r})\n"
            "print('held', flush=True)\n"
            "time.sleep(600)\n"
        )
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            self.assertFalse(locking.try_writer_lock(self.b, self.repo), "the child holds it")
            child.kill()
            child.wait(timeout=10)
            deadline = time.monotonic() + 15
            got = False
            while not got and time.monotonic() < deadline:
                got = locking.try_writer_lock(self.b, self.repo)
                time.sleep(0.1)
            self.assertTrue(got, "the lock outlived the process that held it")
        finally:
            if child.poll() is None:
                child.kill()

    def test_a_second_ingest_exits_busy_and_writes_nothing(self) -> None:
        settings = Settings(database_url=DSN, github_token="x", target_repo=self.repo)
        locking.try_writer_lock(self.a, self.repo)
        with mock.patch.object(run, "GitHubClient", side_effect=AssertionError("must not reach GitHub")), \
             self.assertLogs("decision_graph", level="WARNING") as logs:
            code = run.ingest(settings, resources=["issues"], max_pages=1)
        self.assertEqual(code, run.EXIT_BUSY)
        self.assertIn("in progress", "\n".join(logs.output))

    def test_post_processing_defers_while_a_poll_holds_the_lock(self) -> None:
        settings = Settings(database_url=DSN, github_token="x", target_repo=self.repo)
        locking.try_writer_lock(self.a, self.repo)
        post = webhook.PostProcessor(settings, interval=0)
        post.request(1)
        with self.assertLogs("decision_graph.webhook", level="INFO") as logs:
            self.assertFalse(post.run_if_due())
        self.assertTrue(post.pending, "the pass is deferred, not dropped")
        self.assertIn("deferred", "\n".join(logs.output))
        self.assertNotIn("ERROR", "\n".join(logs.output), "a busy lock is not a failure")


# ---------------------------------------------------------------------------
# The delivery log
# ---------------------------------------------------------------------------


def signed(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class CountingGitHub:
    """Stands in for the GitHub client and counts how often a delivery reaches it."""

    calls = 0

    def __init__(self, **kwargs) -> None: ...

    def get(self, path: str, **kwargs):
        type(self).calls += 1
        return {"node_id": "R_1", "full_name": REPO, "html_url": f"https://github.com/{REPO}",
                "default_branch": "main", "visibility": "public"}

    def paginate(self, *args, **kwargs):
        return iter(())

    def close(self) -> None: ...


def issue_payload(number: int) -> dict:
    return {"repository": {"full_name": REPO}, "issue": {
        "number": number, "node_id": f"I{number}", "title": f"issue {number}", "state": "open", "body": "",
        "html_url": f"https://x/{number}", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z", "user": None}}


class Receiver:
    """A real socket server over a database, without post-processing."""

    def __init__(self, dsn: str) -> None:
        settings = Settings(database_url=dsn, github_token="x", target_repo=REPO)
        bound = type("_Bound", (webhook._Handler,), {"secret": SECRET, "settings": settings})
        self.httpd = webhook._Server(("127.0.0.1", 0), bound)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05), daemon=True).start()

    def deliver(self, event: str, payload: dict, delivery: str | None = None, body: bytes | None = None):
        body = body if body is not None else json.dumps(payload).encode()
        headers = {webhook.EVENT_HEADER: event, webhook.SIGNATURE_HEADER: signed(body)}
        if delivery is not None:
            headers[webhook.DELIVERY_HEADER] = delivery
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/", data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class DeliveryLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_roles import ScratchDatabase

        cls.scratch = ScratchDatabase()
        conn = cls.scratch.connect()
        migrate.apply_all(conn, "db/migrations")
        conn.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.scratch.close()

    def setUp(self) -> None:
        with self.scratch.connect() as c:
            c.execute("DELETE FROM webhook_delivery")
            c.commit()
        CountingGitHub.calls = 0
        self.patch = mock.patch.object(webhook, "GitHubClient", CountingGitHub)
        self.patch.start()
        self.receiver = Receiver(self.scratch.dsn)

    def tearDown(self) -> None:
        self.receiver.close()
        self.patch.stop()

    def rows(self):
        with self.scratch.connect() as c:
            return c.execute("SELECT delivery_id, event, outcome FROM webhook_delivery ORDER BY delivery_id").fetchall()

    def test_the_same_delivery_twice_is_applied_once(self) -> None:
        payload = issue_payload(501)
        first = self.receiver.deliver("issues", payload, delivery="d-1")
        second = self.receiver.deliver("issues", payload, delivery="d-1")
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200, "GitHub must still get a 2xx for a duplicate")
        self.assertIn("already processed", second[1])
        self.assertEqual(CountingGitHub.calls, 1, "the duplicate reached GitHub")
        self.assertEqual([r["delivery_id"] for r in self.rows()], ["d-1"])
        self.assertIn("issue #501", self.rows()[0]["outcome"])

    def test_a_delivery_that_fails_midway_is_not_recorded_so_its_retry_applies(self) -> None:
        payload = issue_payload(502)
        real = webhook.handle_event
        with mock.patch.object(webhook, "handle_event", side_effect=RuntimeError("GitHub fell over")), \
             self.assertLogs("decision_graph.webhook", level="ERROR"):
            status, _ = self.receiver.deliver("issues", payload, delivery="d-2")
        self.assertEqual(status, 500)
        self.assertEqual(self.rows(), [], "a failed delivery was recorded as processed")

        self.assertEqual(real, webhook.handle_event)
        status, text = self.receiver.deliver("issues", payload, delivery="d-2")
        self.assertEqual(status, 200)
        self.assertNotIn("already", text)
        self.assertEqual(len(self.rows()), 1)

    def test_a_captured_body_replayed_under_another_id_does_not_block_that_delivery(self) -> None:
        # The signature covers the body, not the header. Keyed by id alone, replaying delivery A's
        # signed body under the id delivery B is about to have would make the real B look processed.
        a, b = issue_payload(503), issue_payload(504)
        self.receiver.deliver("issues", a, delivery="d-B")      # A's body, B's id
        status, text = self.receiver.deliver("issues", b, delivery="d-B")  # the real B
        self.assertEqual(status, 200)
        self.assertNotIn("already", text, "the genuine delivery was skipped")
        self.assertEqual(len(self.rows()), 2)

    def test_a_delivery_without_an_id_is_processed_every_time(self) -> None:
        payload = issue_payload(505)
        for _ in range(2):
            self.assertNotIn("already", self.receiver.deliver("issues", payload)[1])
        self.assertEqual(CountingGitHub.calls, 2)
        self.assertEqual(self.rows(), [])

    def test_unhandled_events_are_not_recorded(self) -> None:
        self.assertEqual(self.receiver.deliver("star", {"repository": {"full_name": REPO}}, delivery="d-3")[0], 200)
        self.assertEqual(self.rows(), [])

    def test_a_forged_delivery_is_never_recorded(self) -> None:
        body = json.dumps(issue_payload(506)).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.receiver.port}/", data=body,
            headers={webhook.EVENT_HEADER: "issues", webhook.DELIVERY_HEADER: "d-4",
                     webhook.SIGNATURE_HEADER: "sha256=" + "0" * 64})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(self.rows(), [])

    def test_an_absurd_delivery_id_is_a_400(self) -> None:
        self.assertEqual(self.receiver.deliver("issues", issue_payload(507), delivery="x" * 500)[0], 400)

    def test_old_rows_are_pruned_and_recent_ones_kept(self) -> None:
        with self.scratch.connect() as c:
            c.execute("INSERT INTO webhook_delivery (delivery_id, payload_sha256, event, received_at) "
                      "VALUES ('old', %s, 'issues', now() - interval '40 days'), "
                      "('recent', %s, 'issues', now() - interval '2 days')", ("a" * 64, "b" * 64))
            c.commit()
        self.receiver.deliver("issues", issue_payload(508), delivery="d-5")
        self.assertEqual([r["delivery_id"] for r in self.rows()], ["d-5", "recent"])

    def test_the_log_holds_no_payload_text(self) -> None:
        self.receiver.deliver("issues", issue_payload(509), delivery="d-6")
        with self.scratch.connect() as c:
            row = c.execute("SELECT webhook_delivery::text AS t FROM webhook_delivery").fetchone()["t"]
        self.assertNotIn("issue 509", row)
        self.assertNotIn("https://x/", row)


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class BeforeMigration0017Test(unittest.TestCase):
    """A database not yet migrated must keep receiving deliveries and finishing runs: a hook that
    answers 500 gets disabled, and a poll must not lose its outcome after hours of work."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_roles import ScratchDatabase

        cls.scratch = ScratchDatabase()
        conn = cls.scratch.connect()
        older = {p.name for p in migrate.discover("db/migrations") if not p.name.startswith("0017")}
        migrate.apply_all(conn, "db/migrations", only=older)
        conn.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.scratch.close()

    def test_deliveries_are_processed_without_dedupe_and_say_so(self) -> None:
        CountingGitHub.calls = 0
        with mock.patch.object(webhook, "GitHubClient", CountingGitHub):
            receiver = Receiver(self.scratch.dsn)
            try:
                with self.assertLogs("decision_graph.webhook", level="WARNING") as logs:
                    status, _ = receiver.deliver("issues", issue_payload(601), delivery="old-1")
                self.assertEqual(status, 200)
                self.assertIn("dg init", "\n".join(logs.output))
            finally:
                receiver.close()

    def test_a_run_is_recorded_without_the_request_count(self) -> None:
        from decision_graph import db

        conn = self.scratch.connect()
        repo = conn.execute("INSERT INTO node (node_type, external_id) VALUES ('repository', 'zz/old') RETURNING id").fetchone()["id"]
        run_id = db.start_run(conn, repo)
        db.finish_run(conn, run_id, status="succeeded", nodes=1, edges=2, requests=7)
        conn.commit()
        row = conn.execute("SELECT status, nodes_upserted FROM ingestion_run WHERE id = %s", (run_id,)).fetchone()
        self.assertEqual((row["status"], row["nodes_upserted"]), ("succeeded", 1))
        self.assertIsNone(cli.last_runs(conn)["latest"]["requests"])
        conn.close()


@unittest.skipUnless(DSN, "DATABASE_URL not set")
@requires_superuser
class RunRecordTest(unittest.TestCase):
    def test_a_run_records_its_request_count_and_status_reads_it(self) -> None:
        from decision_graph import db
        from tests.test_roles import ScratchDatabase

        scratch = ScratchDatabase()
        try:
            conn = scratch.connect()
            migrate.apply_all(conn, "db/migrations")
            repo = conn.execute("INSERT INTO node (node_type, external_id) VALUES ('repository', 'zz/new') RETURNING id").fetchone()["id"]
            run_id = db.start_run(conn, repo)
            db.finish_run(conn, run_id, status="succeeded", nodes=3, edges=4, requests=42)
            conn.commit()
            seen = cli.last_runs(conn)
            self.assertEqual(seen["latest"]["requests"], 42)
            self.assertIsNotNone(seen["last_success"])
            failed = db.start_run(conn, repo)
            db.finish_run(conn, failed, status="failed", nodes=0, edges=0, error="boom", requests=1)
            conn.commit()
            seen = cli.last_runs(conn)
            self.assertEqual(seen["latest"]["status"], "failed")
            self.assertIsNotNone(seen["last_success"], "the earlier success is still reported")
            conn.close()
        finally:
            scratch.close()


if __name__ == "__main__":
    unittest.main()
