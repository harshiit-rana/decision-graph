"""Credentials never reach a log record (issue #126).

The GitHub token travels in an Authorization header set once on the client
(`github.py`), so the risk is a log line that dumps a request or a header map: a debug
statement added later, an exception message, or a library's own logger. This runs real
`GitHubClient` code paths (success, retry after a 5xx, a secondary rate limit) against a
mock transport with every logger at DEBUG, and searches every record for the token and
the header name.
"""

from __future__ import annotations

import logging
import unittest
from unittest import mock

import httpx

from decision_graph.github import GitHubClient

TOKEN = "ghp_TEST_TOKEN_must_never_be_logged_0123456789"


class TokenNotLoggedTest(unittest.TestCase):
    def _client(self, responses: list[httpx.Response]) -> tuple[GitHubClient, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return responses.pop(0)

        client = GitHubClient(token=TOKEN, user_agent="test", rate_limit_floor=0)
        client._client._transport = httpx.MockTransport(handler)  # keeps the real headers
        return client, seen

    def _run_logged(self, fn) -> str:
        with self.assertLogs(level=logging.DEBUG) as captured:
            logging.getLogger("decision_graph").debug("start")  # assertLogs needs one record
            fn()
        return "\n".join(captured.output)

    def test_success_retry_and_rate_limit_paths_log_no_credential(self) -> None:
        ok = httpx.Response(200, json=[{"id": 1}],
                            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "0"})
        client, seen = self._client([
            httpx.Response(502, json={"message": "bad gateway"}),
            httpx.Response(403, headers={"Retry-After": "0"}, json={"message": "slow down"}),
            ok,
        ])
        with mock.patch("time.sleep"):
            text = self._run_logged(lambda: list(client.paginate("/repos/o/r/issues")))
        self.assertTrue(seen, "the mock transport was never called")
        self.assertEqual(seen[0].headers["Authorization"], f"Bearer {TOKEN}",
                         "the test isn't exercising the real header")
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)


if __name__ == "__main__":
    unittest.main()
