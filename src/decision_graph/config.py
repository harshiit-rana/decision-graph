"""Runtime configuration.

Ingestion target is ``pallets/flask``; ``decision-graph`` is the code repo only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_TARGET_REPO = "pallets/flask"
DEFAULT_BACKFILL_MONTHS = 12


@dataclass(frozen=True)
class Settings:
    database_url: str
    github_token: str
    target_repo: str = DEFAULT_TARGET_REPO
    backfill_months: int = DEFAULT_BACKFILL_MONTHS

    # Stop a run once fewer than this many API calls remain in the hourly budget,
    # rather than blocking until the limit resets. Resume picks up from the cursor.
    rate_limit_floor: int = 100

    user_agent: str = "decision-graph/0.1 (+https://github.com/harshiit-rana/decision-graph)"

    @property
    def owner(self) -> str:
        return self.target_repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.target_repo.split("/", 1)[1]

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise RuntimeError(
                "GITHUB_TOKEN is not set. Unauthenticated GitHub API access is capped at "
                "60 requests/hour, which cannot complete a backfill. Try: "
                'export GITHUB_TOKEN="$(gh auth token)"'
            )

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set.")

        base = {
            "database_url": dsn,
            "github_token": token,
            "target_repo": os.environ.get("TARGET_REPO", DEFAULT_TARGET_REPO),
            "backfill_months": int(
                os.environ.get("BACKFILL_MONTHS", DEFAULT_BACKFILL_MONTHS)
            ),
        }
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)  # type: ignore[arg-type]
