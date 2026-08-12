"""GitHub REST client: pagination, rate-limit accounting, conditional requests.

Rate limiting is treated as a first-class concern rather than an afterthought,
because a 12-month backfill of a repo the size of pallets/flask sits close enough
to the 5,000 req/hour authenticated budget that running out mid-run is expected,
not exceptional. When the budget is nearly spent the client raises
:class:`RateLimitExhausted` and the run stops cleanly — the cursor has already been
committed, so the next run resumes rather than restarting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"


class RateLimitExhausted(RuntimeError):
    """Budget hit the configured floor. Not an error — a clean stopping point."""

    def __init__(self, remaining: int, reset_at: datetime) -> None:
        self.remaining = remaining
        self.reset_at = reset_at
        super().__init__(
            f"GitHub rate limit budget exhausted ({remaining} remaining); "
            f"resets at {reset_at.isoformat()}. Re-run to resume from the cursor."
        )


@dataclass
class Page:
    """One page of results, plus the ETag needed to make the next poll conditional."""

    items: list[dict[str, Any]]
    etag: str | None = None
    not_modified: bool = False


@dataclass
class GitHubClient:
    token: str
    user_agent: str
    rate_limit_floor: int = 100
    per_page: int = 100

    requests_made: int = 0
    _client: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=API_ROOT,
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": self.user_agent,
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core request ------------------------------------------------------

    def _request(
        self, path: str, params: dict[str, Any] | None = None, etag: str | None = None
    ) -> httpx.Response:
        headers = {"If-None-Match": etag} if etag else {}

        for attempt in range(4):
            resp = self._client.get(path, params=params, headers=headers)
            self.requests_made += 1

            # Secondary (abuse) rate limit — always honour the server's own backoff.
            if resp.status_code in (403, 429) and "Retry-After" in resp.headers:
                delay = int(resp.headers["Retry-After"])
                log.warning("secondary rate limit; sleeping %ss", delay)
                time.sleep(delay)
                continue

            if resp.status_code >= 500 and attempt < 3:
                delay = 2**attempt
                log.warning("GitHub %s; retrying in %ss", resp.status_code, delay)
                time.sleep(delay)
                continue

            self._check_budget(resp)
            return resp

        resp.raise_for_status()
        return resp

    def _check_budget(self, resp: httpx.Response) -> None:
        raw_remaining = resp.headers.get("X-RateLimit-Remaining")
        if raw_remaining is None:
            return
        remaining = int(raw_remaining)
        if remaining > self.rate_limit_floor:
            return

        reset = datetime.fromtimestamp(
            int(resp.headers.get("X-RateLimit-Reset", "0")), tz=timezone.utc
        )
        raise RateLimitExhausted(remaining, reset)

    # -- public helpers ----------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Single resource. Returns None for 404 — several resources are optional.

        pallets/flask has no CODEOWNERS file and no wiki, so a 404 is an expected
        outcome for those extractors rather than a failure (see issue #1).
        """
        resp = self._request(path, params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        etag: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[Page]:
        """Yield pages, following Link rel="next".

        Pages are yielded rather than accumulated so the caller can commit a cursor
        after each one. That is what makes a killed run resumable at page
        granularity instead of losing the whole window.
        """
        query = dict(params or {})
        query.setdefault("per_page", self.per_page)

        page_no = 0
        url: str | None = path
        first = True

        while url is not None:
            if max_pages is not None and page_no >= max_pages:
                log.info("stopping at max_pages=%s", max_pages)
                return

            # ETag only applies to the first page; deeper pages have their own.
            resp = self._request(url, query if first else None, etag if first else None)

            if resp.status_code == 304:
                yield Page(items=[], etag=etag, not_modified=True)
                return

            resp.raise_for_status()
            payload = resp.json()
            items = payload if isinstance(payload, list) else [payload]

            yield Page(items=items, etag=resp.headers.get("ETag") if first else None)

            page_no += 1
            first = False
            url = _next_link(resp)
            query = {}


def _next_link(resp: httpx.Response) -> str | None:
    link = resp.headers.get("Link")
    if not link:
        return None
    for part in link.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        if 'rel="next"' in segments[1]:
            return segments[0].strip().strip("<>")
    return None


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
