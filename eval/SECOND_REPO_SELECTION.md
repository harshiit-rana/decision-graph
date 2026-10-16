# Second validation repository — selection record (#127)

**Decision: `urllib3/urllib3`.** Chosen by the project owner on 2026-10-16 from the
measurements below, before any of its data was ingested.

## Why a second repository, and why this one

flask leaves three engines nearly idle: it has no CODEOWNERS file, records formal review on
few pull requests (14 `reviewed` edges across 226 PRs in the ingested window), and merged
only 19 PRs in the twelve months measured here. urllib3 is the only candidate measured that
combines a CODEOWNERS file, routine review and release notes that cite `#N` — so it is the
first repository on which the corroborated tier, the PUBLISHED corroboration category and
ownership edges can be exercised — while staying close enough to flask's size for a full
12-month backfill to fit in one or two hourly rate-limit budgets (403 PRs updated in the
window).

## Measurements (2026-10-16; window since 2025-10-16)

Produced by `python eval/second_repo_candidates.py <repos...>`. Read the script's docstring
for what each column does and does not measure.

| repository | CODEOWNERS | merged PRs | closed issues | releases | sample | reviewed | closes issue | bot PRs |
|---|---|---|---|---|---|---|---|---|
| pallets/flask (baseline) | no | 19 | 73 | 1 | 19 | 15% | 47% | 0% |
| **urllib3/urllib3** | **yes** | 154 | 55 | 6 | 95 | **91%** | 24% | 17% |
| scrapy/scrapy | no | 697 | 459 | 11 | 100 | 97% | 28% | 0% |
| python-poetry/poetry | no | 243 | 149 | 11 | 100 | 98% | 27% | 0% |
| pypa/hatch | no | 124 | 208 | 18 | 100 | 65% | 34% | 0% |
| pytest-dev/pytest | no | 598 | 441 | 6 | 84 | 100% | 36% | 11% |
| tox-dev/tox | yes | 405 | 234 | 30+ | 100 | 23% | 10% | 3% |
| getsentry/sentry-python | yes | 1564 | 915 | 30+ | 100 | 97% | 7% | 0% |
| encode/django-rest-framework | no | 172 | 33 | 8 | 99 | 95% | 13% | 22% |
| pypa/packaging | no | 368 | 67 | 7 | 100 | 93% | 13% | 8% |

Seventeen further repositories were measured and are omitted here, each weaker than urllib3
on at least one deciding property (no CODEOWNERS, low review rate, or several times the size):
encode/httpx, pydantic/pydantic, psf/black, python-attrs/attrs, fastapi/typer,
marshmallow-code/marshmallow, BurntSushi/ripgrep, sharkdp/bat, go-chi/chi, Kludex/starlette,
aio-libs/aiohttp, pypa/twine, pre-commit/pre-commit, Textualize/rich, sqlalchemy/alembic,
pydantic/pydantic-core, python-jsonschema/jsonschema. Two names returned errors
(`encode/starlette`, which has moved to `Kludex/starlette`, and `jazzband/pip-tools`).
Re-run the script to reproduce any of them.

## Checked by hand for urllib3

- `.github/CODEOWNERS` has **5 path rules, all deploy/config** (`.github/workflows/`,
  `.github/CODEOWNERS`, `CHANGES.rst`, `pyproject.toml`, `ci/`). Ownership edges will exist
  but cover little code — the results must say so rather than imply wide ownership.
- The latest release body cites `#N` references (e.g. `#3779`, `#3785`), so release-note
  citations — the PUBLISHED category — should produce edges.

## What this does not establish

- The yield of Decisions. `closes issue` is GitHub's linkage, not TraceMind's parser; the
  prediction belongs in the pre-registration (#131), written before ingestion.
- That review is *required*: branch protection is invisible without admin access. 91% is an
  observed rate over a sample of 95 merged PRs.
- That the bot share (17%) is harmless: bot PRs rarely link issues, so they will mostly land
  in recall bucket A, and that should be reported, not filtered out silently.
