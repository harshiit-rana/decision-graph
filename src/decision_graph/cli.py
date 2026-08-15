"""`dg` — the single entry point.

This runs *inside* the container. The host-side wrapper (`dg.ps1`, `dg.bat`, `dg`) does
only what must happen on the host: confirm Docker is up, build the image once, and hand the
arguments here. Everything else — prompting, migrating, ingesting, querying — happens here,
so the logic exists once rather than three times in three shell dialects.

Two deliberate choices about configuration:

`DATABASE_URL` is **not** user-managed. It is a fixed property of the compose network and
compose injects it. Making people export it was never configuration; it was an
implementation detail leaking onto the host.

`GITHUB_TOKEN` and `TARGET_REPO` live in `.env` at the project root, which is bind-mounted.
They are read on every command, so they are set once and never exported again.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("DG_PROJECT_DIR", "/work"))
ENV_FILE = PROJECT_DIR / ".env"

# ---------------------------------------------------------------------------
# Output. Plain, aligned, and honest about severity — a health check that renders
# everything the same colour is a health check nobody reads.
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def cyan(t: str) -> str:
    return _c("36", t)


def heading(text: str) -> None:
    print(f"\n{bold(text)}")
    print(dim("─" * len(text)))


def step(n: int, total: int, text: str) -> None:
    print(f"\n{cyan(f'[{n}/{total}]')} {bold(text)}")


PASS, WARN, FAIL = "pass", "warn", "fail"
_MARK = {PASS: green("  ok  "), WARN: yellow(" warn "), FAIL: red(" FAIL ")}


def check(state: str, label: str, detail: str = "", fix: str = "") -> str:
    print(f"{_MARK[state]} {label}")
    if detail:
        print(f"        {dim(detail)}")
    if fix:
        print(f"        {yellow('→')} {fix}")
    return state


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def read_env() -> dict[str, str]:
    """Parse .env. Deliberately minimal — KEY=value, `#` comments, optional quotes."""
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_env_into_os() -> dict[str, str]:
    """Apply .env to the process. Real environment variables win, so a one-off
    `-e GITHUB_TOKEN=…` can still override the file without editing it."""
    values = read_env()
    for key, val in values.items():
        os.environ.setdefault(key, val)
    return values


def write_env(values: dict[str, str]) -> None:
    lines = [
        "# decision-graph configuration.",
        "# Read automatically by every `dg` command — never export these by hand.",
        "#",
        "# DATABASE_URL is deliberately absent: it is a property of the Docker network",
        "# and is injected by docker-compose.yml. Setting it here would be ignored.",
        "",
    ]
    for key, val in values.items():
        lines.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)  # it holds a token
    except OSError:
        pass  # bind-mounted from Windows; permissions are not ours to set


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _connect(dsn: str | None = None):
    from . import db

    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set — is this running through `dg`?")
    return db.connect(dsn)


def explain_db_error(exc: Exception) -> tuple[str, str]:
    """Translate a psycopg connection failure into something actionable.

    The raw text ("failed to resolve host 'db'", "Connection refused") describes the
    Docker network, which is an implementation detail the reader did not choose and
    cannot act on. Every one of these means the same thing in practice: the database
    container is not up yet.
    """
    text = str(exc).lower()
    if "resolve host" in text or "name resolution" in text:
        return (
            "the database container is not running",
            "run `dg init` — it starts the database and waits for it to be ready",
        )
    if "connection refused" in text or "could not connect" in text:
        return (
            "the database is starting but not accepting connections yet",
            "wait a few seconds and retry; if it persists, run `dg doctor`",
        )
    if "authentication" in text or "password" in text:
        return (
            "the database rejected our credentials",
            "the volume may predate a config change — `docker compose down -v` resets it "
            "(this deletes ingested data)",
        )
    if "does not exist" in text and "database" in text:
        return ("the database has not been created", "run `dg init`")
    return (str(exc).strip().splitlines()[0], "run `dg doctor`")


def _github(token: str, path: str) -> tuple[int, dict | list | None]:
    import httpx

    try:
        r = httpx.get(
            f"https://api.github.com{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        return 0, {"message": str(exc)}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def _prompt(label: str, *, default: str = "", secret: bool = False) -> str:
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"{label} is needed but there is no terminal to ask on. "
            f"Set it in .env, or re-run `dg init` from an interactive shell."
        )
    suffix = f" [{default}]" if default else ""
    if secret:
        import getpass

        val = getpass.getpass(f"  {label}{suffix}: ").strip()
    else:
        val = input(f"  {label}{suffix}: ").strip()
    return val or default


# ---------------------------------------------------------------------------
# dg init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    total = 4
    print(bold("\ndecision-graph setup"))
    print(dim("Four steps. Nothing is installed on your machine — it all runs in Docker."))

    existing = read_env()

    # --- 1. database -------------------------------------------------------
    step(1, total, "Database")
    dsn = os.environ.get("DATABASE_URL", "")
    try:
        conn = _connect(dsn)
        server = conn.execute("SELECT version() AS v").fetchone()["v"].split(",")[0]
        print(f"        connected — {dim(server)}")
    except Exception as exc:
        why, fix = explain_db_error(exc)
        print(red(f"        cannot reach the database: {why}"))
        print(f"        {yellow('→')} {fix}")
        return 1

    # --- 2. GitHub token ---------------------------------------------------
    step(2, total, "GitHub token")
    # Report where a value actually came from. Saying ".env" for something passed in the
    # environment sends people to edit a file that is not the one in effect.
    token = os.environ.get("GITHUB_TOKEN", "") or existing.get("GITHUB_TOKEN", "")
    token_src = ".env" if existing.get("GITHUB_TOKEN") == token and token else "the environment"

    if token and not args.reconfigure:
        code, _ = _github(token, "/user")
        if code == 200:
            print(f"        using the token from {token_src} {green('(valid)')}")
        else:
            print(yellow(f"        the token from {token_src} was rejected — asking again"))
            token = ""

    if not token or args.reconfigure:
        print(dim("        A token is required: unauthenticated GitHub allows 60 requests"))
        print(dim("        per hour, which cannot finish a backfill. Public repos need no"))
        print(dim("        scopes at all — a classic token with nothing ticked works."))
        print(dim("        Create one: https://github.com/settings/tokens"))
        print()
        while True:
            token = _prompt("GITHUB_TOKEN", secret=True)
            if not token:
                print(red("        a token is required to continue"))
                continue
            code, body = _github(token, "/user")
            if code == 200 and isinstance(body, dict):
                print(f"        {green('valid')} — authenticated as {bold(body.get('login', '?'))}")
                break
            if code == 401:
                print(red("        GitHub rejected that token (401). Check it and retry."))
            else:
                msg = body.get("message") if isinstance(body, dict) else code
                print(red(f"        could not verify the token: {msg}"))

    # --- 3. target repo ----------------------------------------------------
    step(3, total, "Target repository")
    repo = os.environ.get("TARGET_REPO", "") or existing.get("TARGET_REPO", "")

    if not repo or args.reconfigure:
        print(dim("        The repository to ingest. This is the default for `dg ingest`;"))
        print(dim("        you can always override it with `dg ingest --repo owner/name`."))
        print()
        while True:
            repo = _prompt("TARGET_REPO", default=repo or "pallets/flask")
            if "/" not in repo:
                print(red("        expected the form owner/name"))
                continue
            code, body = _github(token, f"/repos/{repo}")
            if code == 200 and isinstance(body, dict):
                print(f"        {green('found')} — {bold(repo)}, {body.get('stargazers_count', 0)} stars")
                break
            if code == 404:
                print(red(f"        GitHub has no repository {repo} visible to this token"))
            else:
                print(red(f"        could not reach GitHub (HTTP {code})"))
    else:
        repo_src = ".env" if existing.get("TARGET_REPO") == repo else "the environment"
        print(f"        using {bold(repo)} from {repo_src}")

    write_env(
        {
            "GITHUB_TOKEN": token,
            "TARGET_REPO": repo,
            "BACKFILL_MONTHS": existing.get("BACKFILL_MONTHS", "12"),
        }
    )
    print(f"        wrote {cyan('.env')}")

    # --- 4. migrations -----------------------------------------------------
    step(4, total, "Schema")
    from . import migrate

    try:
        result = migrate.apply_all(conn)
    except Exception as exc:
        print(red(f"        migration failed: {exc}"))
        return 1

    if result.applied:
        print(f"        applied {len(result.applied)}: {dim(', '.join(result.applied))}")
    if result.adopted:
        print(f"        adopted {len(result.adopted)} already present in this database")
    if result.up_to_date:
        print(f"        {green('already up to date')} — {len(result.skipped)} migrations")
    if result.changed:
        print(yellow(f"        {len(result.changed)} applied migration(s) have since changed:"))
        for name in result.changed:
            print(yellow(f"          {name}"))
        print(dim("        the database and the repository no longer agree on these"))

    conn.close()

    print(bold("\nReady.\n"))
    print("  Next:")
    # Pad the plain text, then colour — colouring first makes ljust count escape bytes
    # and the column drifts by exactly the width of the ANSI codes.
    nxt = [
        ("dg doctor", "check everything is healthy"),
        (f"dg ingest --repo {repo}", "pull the last 12 months of history"),
        ("dg status", "see what was ingested"),
        ('dg query "send_file" --mode why', "ask the graph a question"),
    ]
    width = max(len(cmd) for cmd, _ in nxt)
    for cmd, why in nxt:
        print(f"    {cyan(cmd)}{' ' * (width - len(cmd))}   {dim(why)}")
    print()
    print(dim("  Ingestion is the slow step — expect roughly 2 API requests per pull"))
    print(dim("  request. If it stops on the rate limit, just run it again; it resumes."))
    print()
    return 0


# ---------------------------------------------------------------------------
# dg doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    heading("Environment")
    states: list[str] = []

    states.append(
        check(PASS, f"Python {sys.version_info.major}.{sys.version_info.minor} in container")
    )

    if shutil.which("psql"):
        states.append(check(PASS, "psql available in container"))
    else:
        states.append(
            check(WARN, "psql not in the image", fix="only needed for manual SQL; `dg status` does not use it")
        )

    heading("Configuration")
    if ENV_FILE.exists():
        states.append(check(PASS, ".env found", str(ENV_FILE)))
    else:
        states.append(
            check(FAIL, ".env is missing", f"expected at {ENV_FILE}", "run `dg init`")
        )

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("TARGET_REPO", "")

    if not token:
        states.append(check(FAIL, "GITHUB_TOKEN not set", fix="run `dg init`"))
    else:
        code, body = _github(token, "/user")
        if code == 200 and isinstance(body, dict):
            states.append(check(PASS, "GitHub token valid", f"authenticated as {body.get('login')}"))
            rc, rate = _github(token, "/rate_limit")
            if rc == 200 and isinstance(rate, dict):
                core = rate.get("resources", {}).get("core", {})
                remaining, limit = core.get("remaining", 0), core.get("limit", 0)
                if remaining < 200:
                    states.append(
                        check(
                            WARN,
                            f"rate limit low — {remaining}/{limit} left",
                            fix="ingestion will stop cleanly and resume next run",
                        )
                    )
                else:
                    states.append(check(PASS, f"rate limit — {remaining}/{limit} remaining"))
        elif code == 401:
            states.append(
                check(FAIL, "GitHub rejected the token (401)", fix="run `dg init --reconfigure`")
            )
        else:
            states.append(
                check(
                    FAIL,
                    "cannot reach the GitHub API",
                    f"HTTP {code}",
                    "check your network or proxy, then retry",
                )
            )

    if repo:
        states.append(check(PASS, f"target repo — {repo}"))
    else:
        states.append(check(WARN, "TARGET_REPO not set", fix="pass `--repo owner/name` per command"))

    heading("Database")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        states.append(
            check(
                FAIL,
                "DATABASE_URL not set",
                "compose should inject this",
                "run through `dg`, not `python -m` directly",
            )
        )
        return _verdict(states)

    try:
        conn = _connect(dsn)
    except Exception as exc:
        why, fix = explain_db_error(exc)
        states.append(check(FAIL, "database unreachable", why, fix))
        return _verdict(states)

    states.append(check(PASS, "database reachable"))

    from . import migrate

    try:
        outstanding = migrate.pending(conn)
        if outstanding:
            states.append(
                check(
                    FAIL,
                    f"{len(outstanding)} migration(s) not applied",
                    ", ".join(outstanding),
                    "run `dg init` — it is safe to re-run",
                )
            )
        else:
            states.append(check(PASS, "schema up to date"))
    except Exception as exc:
        states.append(check(FAIL, "could not read the migration ledger", str(exc), "run `dg init`"))

    heading("Data")
    try:
        nodes = conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"]
        if nodes:
            states.append(check(PASS, f"{nodes:,} nodes ingested"))
        else:
            states.append(
                check(WARN, "the graph is empty", fix=f"run `dg ingest{f' --repo {repo}' if repo else ''}`")
            )
    except Exception:
        states.append(check(WARN, "no graph tables yet", fix="run `dg init`"))

    conn.close()
    return _verdict(states)


def _verdict(states: list[str]) -> int:
    fails = states.count(FAIL)
    warns = states.count(WARN)
    print()
    if fails:
        print(red(bold(f"{fails} problem(s) need fixing.")) + dim(f"  ({warns} warning(s))"))
        return 1
    if warns:
        print(yellow(bold(f"Healthy, with {warns} warning(s).")))
        return 0
    print(green(bold("All checks passed.")))
    return 0


# ---------------------------------------------------------------------------
# dg status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    try:
        conn = _connect()
    except Exception as exc:
        why, fix = explain_db_error(exc)
        print(red(f"Cannot reach the database: {why}"))
        print(f"{yellow('→')} {fix}")
        return 1

    try:
        conn.execute("SELECT 1 FROM node LIMIT 1")
    except Exception:
        conn.rollback()
        print(yellow("No schema yet.") + " Run `dg init`.")
        conn.close()
        return 1

    repos = conn.execute(
        """
        SELECT r.id, r.external_id AS name,
               (SELECT count(*) FROM node n WHERE n.repo_node_id = r.id) AS nodes
        FROM node r WHERE r.node_type = 'repository' ORDER BY r.external_id
        """
    ).fetchall()

    if not repos:
        print(yellow("Nothing ingested yet.") + " Run `dg ingest --repo owner/name`.")
        conn.close()
        return 0

    heading("Repositories")
    for r in repos:
        artifacts = f"{r['nodes']:,} artifacts"
        print(f"  {bold(r['name'])}  {dim(artifacts)}")
    # People carry no repo_node_id — a GitHub account is not owned by a repository — so
    # the per-repo counts deliberately do not sum to the total below. Say so, rather than
    # printing two numbers that look like they should reconcile and don't.
    unscoped = conn.execute(
        "SELECT count(*) AS n FROM node WHERE repo_node_id IS NULL AND node_type <> 'repository'"
    ).fetchone()["n"]
    if unscoped:
        print(dim(f"  + {unscoped:,} people, not owned by any single repository"))

    heading("Nodes by type")
    rows = conn.execute(
        "SELECT node_type::text AS t, count(*) AS n FROM node GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    width = max(len(r["t"]) for r in rows)
    for r in rows:
        print(f"  {r['t']:<{width}}  {r['n']:>6,}")
    print(f"  {dim('─' * (width + 9))}")
    print(f"  {'total':<{width}}  {sum(r['n'] for r in rows):>6,}")

    heading("Ingestion")
    runs = conn.execute(
        """
        SELECT status, started_at, finished_at, error
        FROM ingestion_run ORDER BY id DESC LIMIT 1
        """
    ).fetchall()
    if runs:
        r = runs[0]
        mark = green("succeeded") if r["status"] == "succeeded" else red(r["status"])
        when = r["finished_at"] or r["started_at"]
        print(f"  last run   {mark} at {when:%Y-%m-%d %H:%M} UTC")
        if r["error"]:
            print(f"  {dim(r['error'][:100])}")
    else:
        print(dim("  no runs recorded"))

    cursors = conn.execute(
        "SELECT resource, phase, steady_watermark FROM ingestion_cursor ORDER BY resource"
    ).fetchall()
    if cursors:
        print()
        for c in cursors:
            wm = f"{c['steady_watermark']:%Y-%m-%d}" if c["steady_watermark"] else "—"
            print(f"  {c['resource']:<10} {c['phase']:<9} up to {wm}")
        print(dim("\n  A watermark short of today means there is more to fetch — re-run"))
        print(dim("  `dg ingest` to continue from exactly there."))

    decisions = conn.execute("SELECT count(*) AS n FROM decision").fetchone()["n"]
    threads = conn.execute(
        "SELECT count(DISTINCT thread_key) AS n FROM node WHERE thread_key IS NOT NULL"
    ).fetchone()["n"]
    heading("Graph")
    print(f"  {bold(str(decisions))} decisions from {threads:,} thread clusters")
    if threads and not decisions:
        print(dim("  No decisions yet. That is a real answer, not a failure: the rubric"))
        print(dim("  needs a motivating issue AND merged work in one thread."))

    conn.close()
    return 0


# ---------------------------------------------------------------------------
# passthrough
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace, extra: list[str]) -> int:
    from . import run

    argv = list(extra)
    if args.repo:
        argv += ["--repo", args.repo]
    return run.main(argv)


def cmd_query(args: argparse.Namespace, extra: list[str]) -> int:
    from . import query

    return query.main([args.text] + list(extra))


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dg",
        description="Organizational Intelligence Engine — decision graph over a GitHub repo.",
        epilog="Every command runs in Docker. Nothing is installed on your machine.",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    i = sub.add_parser("init", help="first-time setup: configure, start the database, migrate")
    i.add_argument(
        "--reconfigure", action="store_true", help="re-prompt for token and repo even if set"
    )
    i.set_defaults(fn=cmd_init)

    d = sub.add_parser("doctor", help="check everything is healthy and say how to fix it")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("status", help="what is currently ingested")
    s.set_defaults(fn=cmd_status)

    g = sub.add_parser(
        "ingest",
        help="pull a repository's history into the graph",
        epilog="Any other dg-ingest flag (--months, --resources, --max-pages) is passed through.",
    )
    g.add_argument("--repo", help="owner/name (defaults to TARGET_REPO from .env)")
    g.set_defaults(fn=cmd_ingest, passthrough=True)

    q = sub.add_parser(
        "query",
        help="ask why something happened, or what a change affects",
        epilog="Any other dg-query flag (--mode, --depth, --as-of, --limit) is passed through.",
    )
    q.add_argument("text", help="a title, #number, or commit sha")
    q.set_defaults(fn=cmd_query, passthrough=True)

    return p


def main(argv: list[str] | None = None) -> int:
    load_env_into_os()
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    # Unknown flags are an error for our own commands, but ingest and query deliberately
    # forward theirs to the underlying tools rather than duplicating every option here.
    if extra and not getattr(args, "passthrough", False):
        parser.error(f"unrecognised arguments: {' '.join(extra)}")

    try:
        if getattr(args, "passthrough", False):
            return args.fn(args, extra)
        return args.fn(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(red(f"\nerror: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
