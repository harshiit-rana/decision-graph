"""The interactive launcher -- what `dg` shows when you run it with no command.

This is a front door, not a second implementation. Every action it offers dispatches to
the same `cmd_*` function the flag-driven CLI calls, so `dg query "..." --mode why` and
picking "Ask why" from the menu run identical code. The menu only gathers the arguments a
newcomer would not know to type, then hands off.

It draws once, reads one choice, runs it, and loops. The status block at the top is read
live from the database on every redraw, so it doubles as a lightweight `doctor` -- you see
whether the graph is empty before you pick, not after.

Shown only on a real terminal. Piped or scripted, `dg` with no command prints help instead,
so nothing here can block a non-interactive caller waiting on input that will never come.
"""

from __future__ import annotations

import argparse
import os

from .cli import (
    _connect,
    bold,
    cmd_doctor,
    cmd_ingest,
    cmd_query,
    cmd_status,
    cyan,
    dim,
    explain_db_error,
    green,
    red,
    yellow,
)

# Rendered once with pyfiglet's "small" font and pasted, because the container image has no
# figlet and this must not gain a dependency just to print a header. Pure ASCII; the font
# uses backticks and backslashes, so each line is a raw string.
BANNER = [
    r"    _        _    _                                _",
    r" __| |___ __(_)__(_)___ _ _ ___ __ _ _ _ __ _ _ __| |_",
    r"/ _` / -_) _| (_-< / _ \ ' \___/ _` | '_/ _` | '_ \ ' \ ",
    r"\__,_\___\__|_/__/_\___/_||_|  \__, |_| \__,_| .__/_||_|",
    r"                               |___/         |_|",
]


class Action:
    """One menu row: a key, a label, a one-line hint, and what running it does."""

    def __init__(self, key: str, label: str, hint: str, run) -> None:
        self.key = key
        self.label = label
        self.hint = hint
        self.run = run


def _clear() -> None:
    # Home the cursor and clear below it, rather than a full reset -- this keeps scrollback
    # intact so nothing an action printed is lost when the menu redraws over it.
    if os.environ.get("NO_COLOR") is None:
        print("\033[H\033[J", end="")


def _greeting() -> str:
    # The hour comes from the database clock via now(), not the host: datetime.now() is
    # banned across this codebase for order-independence, and the same honesty applies here
    # -- read the wall clock from a source we control.
    try:
        conn = _connect()
        hour = conn.execute("SELECT extract(hour FROM now()) AS h").fetchone()["h"]
        conn.close()
    except Exception:
        return "Hello"
    h = int(hour)
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


def _status_block() -> None:
    """A live preflight: repo, database, schema, and how full the graph is.

    Deliberately tolerant. A brand-new user reaches this before `init` has ever run, so
    every probe that can fail is caught and rendered as a next step, not a traceback.
    """
    repo = os.environ.get("TARGET_REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    target = bold(repo) if repo else yellow("not set")
    print(f"  target repo   {target}")
    if not token:
        print(f"    {yellow('!')} no GITHUB_TOKEN -- run {bold('setup')} (menu i) before ingesting")

    try:
        conn = _connect()
    except Exception as exc:
        why, _ = explain_db_error(exc)
        print(f"    {red('x')} database unreachable -- {dim(why)}")
        return

    from . import migrate

    try:
        pending = migrate.pending(conn)
    except Exception:
        pending = None

    if pending:
        print(f"    {yellow('!')} {len(pending)} migration(s) pending -- run {bold('setup')} (menu i)")
    elif pending == []:
        print(f"    {green('/')} database reachable, schema up to date")
    else:
        print(f"    {yellow('!')} no schema yet -- run {bold('setup')} (menu i)")

    try:
        nodes = conn.execute("SELECT count(*) AS n FROM node").fetchone()["n"]
        decisions = conn.execute("SELECT count(*) AS n FROM decision").fetchone()["n"]
    except Exception:
        nodes = decisions = 0

    if nodes:
        print(
            f"    {green('/')} graph: {bold(f'{nodes:,}')} artifacts, "
            f"{bold(str(decisions))} decisions"
        )
    else:
        hint = f" --repo {repo}" if repo else ""
        print(f"    {yellow('!')} graph is empty -- run an {bold('ingest')} (menu 1){dim(hint)}")

    conn.close()


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"  {prompt}{dim(suffix)}: ").strip()
    except EOFError:
        return default
    return raw or default


# --- the actions, each a thin gatherer in front of a real cmd_* -------------------------


def _run_init(_ns: argparse.Namespace) -> None:
    from .cli import cmd_init

    cmd_init(argparse.Namespace(reconfigure=False))


def _run_ingest(_ns: argparse.Namespace) -> None:
    repo = os.environ.get("TARGET_REPO", "")
    chosen = _ask("repository to ingest (owner/name)", repo)
    if not chosen:
        print(yellow("  nothing to ingest -- no repository given"))
        return
    cmd_ingest(argparse.Namespace(repo=chosen), [])


def _run_query(mode: str):
    def go(_ns: argparse.Namespace) -> None:
        subject = "changed" if mode == "why" else "you want to trace"
        text = _ask(f"what {subject}? (a title, #number, or commit sha)")
        if not text:
            print(yellow("  nothing to look up"))
            return
        extra = ["--mode", mode]
        if mode == "impact":
            extra += ["--depth", _ask("depth", "2")]
        cmd_query(argparse.Namespace(text=text), extra)

    return go


def _run_report(_ns: argparse.Namespace) -> None:
    from .cli import cmd_report

    # --open by default from the menu: someone who picked "browse the graph" from a list
    # wants to look at it, not to be told where a file is.
    cmd_report(argparse.Namespace(repo=None, output=None, open=True))


def _run_ask(_ns: argparse.Namespace) -> None:
    from .cli import cmd_ask

    question = _ask("what do you want to ask? (e.g. 'Why did we change the redirect code?')")
    if not question:
        print(yellow("  nothing to ask"))
        return
    cmd_ask(argparse.Namespace(question=question), [])


ACTIONS = [
    Action("1", "Ingest a repository", "pull the last 12 months into the graph", _run_ingest),
    Action("2", "Ask why", "why did a change happen?", _run_query("why")),
    Action("3", "Trace impact", "what does a change affect, downstream?", _run_query("impact")),
    Action("4", "Repository status", "counts, cursors, decisions", lambda ns: cmd_status(ns)),
    Action("5", "Health check", "what works, and how to fix what does not", lambda ns: cmd_doctor(ns)),
    Action("6", "Natural Language Q&A", "ask a question in plain English (requires Nvidia API key)", _run_ask),
    Action("7", "Browse the graph", "render every decision and its evidence to an HTML page", _run_report),
    Action("i", "Setup / reconfigure", "token, target repo, migrations (safe to re-run)", _run_init),
]


def _draw() -> None:
    _clear()
    print()
    for line in BANNER:
        print(cyan(line))
    print()
    print(f"  {dim('organizational intelligence engine')}")
    print(f"  {dim('why did this change happen -- reconstructed from a repository history')}")
    print()
    print(
        "  " + dim("ask why? ") + "(2)   "
        + dim("what breaks? ") + "(3)   "
        + dim("what is inside? ") + "(4)"
    )
    print()
    print(f"  {_greeting()}.")
    print()
    _status_block()
    print()
    for a in ACTIONS:
        print(f"  {bold(a.key)}  {a.label:<22}{dim(a.hint)}")
    print(f"  {bold('q')}  {'Quit':<22}")
    print()


def run(ns: argparse.Namespace) -> int:
    """The read-run-redraw loop. Returns an exit code when the user quits."""
    by_key = {a.key: a for a in ACTIONS}
    while True:
        _draw()
        try:
            choice = input(f"  {cyan('>')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice in ("q", "quit", "exit"):
            print(dim("  bye"))
            return 0

        action = by_key.get(choice)
        if not action:
            continue

        print()
        try:
            action.run(ns)
        except KeyboardInterrupt:
            print(yellow("\n  cancelled"))
        except Exception as exc:  # a failed action must not kill the menu
            print(red(f"  error: {exc}"))

        # Hold the action's output on screen until the user is ready; otherwise the redraw
        # wipes it instantly.
        try:
            input(dim("\n  press Enter to return to the menu "))
        except (EOFError, KeyboardInterrupt):
            return 0
