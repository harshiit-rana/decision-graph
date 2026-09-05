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


# --- following up on an answer --------------------------------------------------------
#
# Everything here was previously a flag you had to know existed before you ran anything
# (issue #78). Offered after an answer instead, where each one means something concrete
# about the answer in front of you, and reachable by one keystroke.
#
# They are TOGGLES over a small state, not an accumulating list of flags. The first version
# appended, which meant picking "diagram" and then "as of a date" left you in diagram mode
# with no way back to prose except leaving the answer entirely -- the state was real but
# invisible, and the only way to discover it was to be surprised by it. Here the state is
# rebuilt into flags on every pass and printed in the bar, so what is on is on the screen.


class View:
    """What the reader currently wants to see of one answer.

    Rendered by re-running `dg query` with the flags this produces, rather than by holding
    an Answer and re-rendering it here. Re-running costs a second traversal -- milliseconds
    -- and buys the thing this codebase keeps choosing: one implementation. A menu that
    rendered answers its own way would be a second renderer to keep in step with the first.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.diagram = False
        self.details = False
        self.all_matches = False
        self.as_of: str | None = None

    def flags(self) -> list[str]:
        argv = ["--mode", self.mode]
        if self.diagram:
            argv += ["--format", "mermaid"]
        if self.details:
            argv += ["-v"]
        if self.all_matches:
            argv += ["--all"]
        if self.as_of:
            argv += ["--as-of", f"{self.as_of}T00:00:00Z"]
        return argv

    def label(self, key: str) -> str:
        """`on` when a toggle is active, so the bar states what is already applied."""
        return {
            "d": "on" if self.diagram else "",
            "v": "on" if self.details else "",
            "a": "on" if self.all_matches else "",
            "t": self.as_of or "",
            "s": self.mode,
        }.get(key, "")

    def toggle(self, key: str, ask=None) -> bool:
        """Apply one keystroke. Returns False if the key means nothing here.

        `ask` is injected so the date prompt can be driven by a test without a terminal.
        """
        if key == "d":
            self.diagram = not self.diagram
        elif key == "v":
            self.details = not self.details
        elif key == "a":
            self.all_matches = not self.all_matches
        elif key == "s":
            self.mode = "impact" if self.mode == "why" else "why"
        elif key == "t":
            if self.as_of:
                # A second press clears it. Time travel you cannot leave is a trap, and it
                # is the toggle where being stuck is least obvious -- every later answer
                # would be about a past that the reader has stopped thinking about.
                self.as_of = None
            else:
                when = (ask or _ask)("as of when? (YYYY-MM-DD, or Enter to cancel)")
                if when:
                    self.as_of = when
        else:
            return False
        return True


FOLLOW_UPS = [
    ("d", "diagram", "as a mermaid graph you can paste into GitHub"),
    ("v", "details", "node ids and the extractor behind every edge"),
    ("a", "all matches", "answer from every candidate, not just the best"),
    ("t", "as of a date", "what the graph knew at a point in time"),
    ("s", "switch mode", "ask the other question about the same thing"),
]


DECISIONS_SQL = """
SELECT d.node_id,
       d.status::text  AS status,
       n.title,
       iss.external_id AS issue_ref,
       d.decided_at
FROM decision d
JOIN node n ON n.id = d.node_id
LEFT JOIN edge e ON e.src_node_id = d.node_id
                AND e.edge_type = 'motivated_by'
                AND e.valid_to IS NULL
LEFT JOIN node iss ON iss.id = e.dst_node_id AND iss.node_type = 'issue'
ORDER BY d.decided_at DESC NULLS LAST, d.node_id
"""


def decision_rows(conn) -> list[dict]:
    """Every reconstructed decision, newest first, with the issue that motivated it.

    The issue number is what the browse list hands to `dg query`, because a Decision's own
    external_id is its thread_key -- which names the cluster and, for 6 of 15 of them, names
    a pull request that was abandoned (issue #19). Starting a walk from the motivating issue
    starts it from an artifact that is what it says it is.
    """
    return [dict(r) for r in conn.execute(DECISIONS_SQL).fetchall()]


def _pick_decision() -> str | None:
    """Show the decisions and return the query string for the one chosen.

    This exists because the menu used to open with "what changed?" -- a question the person
    it was written for cannot answer. The graph holds 15 decisions and there was no way to
    see what they were from inside the tool.
    """
    try:
        conn = _connect()
    except Exception as exc:
        why, fix = explain_db_error(exc)
        print(f"  {red('x')} {why}")
        print(f"    {yellow('->')} {fix}")
        return None

    try:
        rows = decision_rows(conn)
    finally:
        conn.close()

    if not rows:
        print(yellow("  No decisions in the graph yet."))
        print(dim("  That is a result, not a failure: the rubric needs a motivating issue"))
        print(dim("  and merged work in one thread. Try an ingest (menu 8) first."))
        return None

    print(f"  {bold(str(len(rows)))} decisions, newest first:")
    print()
    for i, r in enumerate(rows, 1):
        when = f"{r['decided_at']:%Y-%m-%d}" if r["decided_at"] else " " * 10
        ref = f"#{r['issue_ref']}" if r["issue_ref"] else "-"
        # Both statuses are spelled out. Marking only the explicit ones would be shorter and
        # would read as though the rest had no status at all, which is the kind of omission
        # this codebase keeps deciding not to make.
        status = "explicit" if r["status"] == "explicit" else "reconstructed"
        painted = green(status) if r["status"] == "explicit" else dim(status)
        pad = " " * (13 - len(status))
        title = (r["title"] or "")[:44]
        print(f"  {bold(f'{i:>2}')}  {dim(when)}  {ref:<7} {painted}{pad}  {title}")
    print()

    choice = _ask("pick a number (or Enter to go back)")
    if not choice:
        return None
    try:
        index = int(choice)
        if index < 1:
            raise IndexError(index)
        row = rows[index - 1]
    except (ValueError, IndexError):
        print(yellow(f"  no decision numbered {choice!r}"))
        return None
    # Prefer the issue number: exact, unambiguous, and an artifact a reader can open.
    return f"#{row['issue_ref']}" if row["issue_ref"] else (row["title"] or "")


def _answer_and_follow_up(text: str, mode: str) -> None:
    view = View(mode)
    while True:
        cmd_query(argparse.Namespace(text=text), view.flags())

        print()
        print(dim("  " + "-" * 66))
        for key, label, hint in FOLLOW_UPS:
            state = view.label(key)
            marker = green(f"[{state}]") if state else "     "
            print(f"  {bold(key)}  {label:<14}{marker:<14}{dim(hint)}")
        print(f"  {bold('Enter')}  {'back':<11}")

        choice = _ask("").lower()
        if not choice:
            return
        if not view.toggle(choice):
            print(yellow(f"  not an option: {choice!r}"))


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
        text = _ask(f"what {subject}? (a title, #number, sha -- or Enter to browse)")
        if not text:
            # An empty answer used to end the action. It is the commonest answer there is:
            # the question asks you to name something before anything has been shown to
            # you, so not knowing is the default state, not a mistake (issue #78).
            text = _pick_decision()
            if not text:
                return
        _answer_and_follow_up(text, mode)

    return go


def _run_browse(_ns: argparse.Namespace) -> None:
    text = _pick_decision()
    if text:
        _answer_and_follow_up(text, "why")


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
    # Browsing comes first because it is the only entry that asks nothing of you. Every
    # other query action begins by wanting a name you may not have.
    Action("1", "Browse decisions", "see what was found, then drill into one", _run_browse),
    Action("2", "Ask why", "why did a change happen?", _run_query("why")),
    Action("3", "Trace impact", "what does a change affect, downstream?", _run_query("impact")),
    Action("4", "Natural Language Q&A", "ask in plain English (requires an Nvidia API key)", _run_ask),
    Action("5", "Report to HTML", "every decision and its evidence, as a page", _run_report),
    Action("6", "Repository status", "counts, cursors, decisions", lambda ns: cmd_status(ns)),
    Action("7", "Health check", "what works, and how to fix what does not", lambda ns: cmd_doctor(ns)),
    Action("8", "Ingest a repository", "pull the last 12 months into the graph", _run_ingest),
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
        "  " + dim("show me what you found? ") + "(1)   "
        + dim("why did this happen? ") + "(2)   "
        + dim("what breaks? ") + "(3)"
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
