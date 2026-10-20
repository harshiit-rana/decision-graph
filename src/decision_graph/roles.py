"""Least-privilege database roles (issue #172).

After #118 the database listens on loopback with a random password, but every `dg` command
still connected as the `postgres` superuser. A bug in any of them, or a prompt-injected agent
tool once #139 exists, then ran with the right to `DROP` a table, to alter the CHECK constraints
and triggers that decide what counts as a Decision, or to run `COPY ... TO PROGRAM`. The
integrity of the evidence is the product, and nothing below the superuser could protect it.

Three roles:

- `dg_owner`  NOLOGIN. Owns every object in `public`, and is the role migrations run as. Nothing
  can log in as it, so the schema rules can be changed only by someone who is already a
  superuser and says so (`SET ROLE dg_owner`).
- `dg_app`    LOGIN. SELECT, INSERT, UPDATE, DELETE on the tables and USAGE on the sequences.
  Runs ingestion. It is not an owner, so it cannot ALTER a table or DROP a trigger or a
  constraint, and it was never granted TRUNCATE.
- `dg_reader` LOGIN. SELECT only. `query`, `context`, `report`, `status`, `ask` and a snapshot
  export use it, and the MCP server (#139) will. A reader that can't write is also how
  `--as-of` is kept from ever mutating anything.

**What this does and doesn't protect.** It limits what a bug or a hostile query can do through
the connection `dg` opens. It does not stop a process that can read the container's environment:
`DATABASE_URL` there still names the superuser, because `dg init` and `dg snapshot import` need
one. A tool that must not hold that (the MCP server) has to be started with only the reader's
DSN. See SECURITY.md.

**How the right DSN is chosen.** docker-compose.yml is unchanged and still injects the superuser
URL as `DATABASE_URL`. `dg` keeps that as the *admin* DSN (`DG_ADMIN_URL`) and, when `.env` holds
`DG_APP_PASSWORD` / `DG_READER_PASSWORD`, rewrites `DATABASE_URL` for each command to the role
that command needs. With no such passwords nothing changes, which is how an existing install
stays exactly as it was until its owner runs `dg roles setup`.

**Existing installs are never switched silently.** On an empty database `dg init` creates the
roles and runs the migrations as `dg_owner`. On a populated one it says what to run and stops:
`dg roles setup` takes a snapshot first, moves ownership of the existing objects to `dg_owner`,
and only then writes the passwords. Undo is deleting those two lines from `.env`; the superuser
can still do everything it could before.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlsplit, urlunsplit

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

OWNER = "dg_owner"
APP = "dg_app"
READER = "dg_reader"
ROLES = (OWNER, APP, READER)

PASSWORD_KEY = {APP: "DG_APP_PASSWORD", READER: "DG_READER_PASSWORD"}
ADMIN_ENV = "DG_ADMIN_URL"

# The role each `dg` command connects as. `None` means the admin (superuser) DSN: setup and
# restore need rights no least-privilege role has, and `doctor` has to ask about the superuser
# itself. Anything not listed stays on the admin DSN, which is the safe direction to be wrong in.
COMMAND_ROLE: dict[str, str | None] = {
    "init": None,
    "doctor": None,
    "roles": None,
    "ingest": APP,
    "query": READER,
    "context": READER,
    "report": READER,
    "status": READER,
    "ask": READER,
}
# `snapshot` depends on its action: exporting only reads, importing writes everything with
# session_replication_role = replica, which needs a superuser.
SNAPSHOT_ROLE = {"export": READER, "inspect": READER, "import": None}


def role_for(command: str | None, action: str | None = None) -> str | None:
    if command == "snapshot":
        return SNAPSHOT_ROLE.get(action or "", None)
    return COMMAND_ROLE.get(command or "", None)


def configured(env: dict[str, str]) -> bool:
    """True when `.env` holds a password for both login roles."""
    return all(env.get(key) for key in PASSWORD_KEY.values())


def dsn_for(admin_dsn: str, role: str | None, env: dict[str, str]) -> str:
    """The DSN `role` connects with, or `admin_dsn` unchanged when there is none to use.

    A URL stays a URL, because other code (`uses_default_db_password`, doctor) parses it as one.
    """
    password = env.get(PASSWORD_KEY.get(role or "", ""), "")
    if role is None or not password:
        return admin_dsn
    parts = urlsplit(admin_dsn)
    if parts.scheme in ("postgresql", "postgres"):
        host = parts.hostname or ""
        if ":" in host:  # an IPv6 literal needs its brackets back
            host = f"[{host}]"
        netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return make_conninfo(admin_dsn, user=role, password=password)


def user_of(dsn: str) -> str | None:
    parts = urlsplit(dsn)
    if parts.scheme in ("postgresql", "postgres"):
        return parts.username
    return conninfo_to_dict(dsn).get("user")


def new_password() -> str:
    return secrets.token_urlsafe(24)  # [A-Za-z0-9_-]: safe in a DSN and in .env


def is_superuser(conn) -> bool:
    return bool(conn.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()["rolsuper"])


def roles_exist(conn) -> bool:
    n = conn.execute("SELECT count(*) AS n FROM pg_roles WHERE rolname = ANY(%s)", (list(ROLES),)).fetchone()["n"]
    return n == len(ROLES)


def schema_owner_state(conn) -> str:
    """'empty' (no graph table yet), 'owned' (by dg_owner) or 'legacy' (by anyone else)."""
    row = conn.execute(
        "SELECT pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
        "WHERE c.oid = to_regclass('public.node')").fetchone()
    if row is None:
        return "empty"
    return "owned" if row["owner"] == OWNER else "legacy"


# ---------------------------------------------------------------------------
# Creating the roles
# ---------------------------------------------------------------------------


def setup(conn, *, app_password: str | None = None, reader_password: str | None = None) -> None:
    """Create the roles and grant what each needs. Idempotent: safe to run twice.

    Needs a superuser. Passwords are set only when given, so re-running without them keeps the
    ones already in the database. Nothing is committed here; the caller commits once `.env`
    holds the passwords (see `provision`).
    """
    ident = sql.Identifier
    database = conn.execute("SELECT current_database() AS d").fetchone()["d"]

    for role, login in ((OWNER, False), (APP, True), (READER, True)):
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {}").format(ident(role)))
        # Re-asserted every time, so a role that was altered by hand, or that already existed
        # with more rights, is brought back to what it is meant to be.
        conn.execute(sql.SQL(
            "ALTER ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
        ).format(ident(role), sql.SQL("LOGIN" if login else "NOLOGIN")))

    for role, password in ((APP, app_password), (READER, reader_password)):
        if password:
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(ident(role), sql.Literal(password)))

    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(ident(database), ident(APP), ident(READER)))
    # CREATE on the database lets the owner install pg_trgm, which is a *trusted* extension
    # since PostgreSQL 13, so no superuser is needed to apply migration 0001.
    conn.execute(sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO {}").format(ident(database), ident(OWNER)))
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(ident(OWNER)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(ident(APP), ident(READER)))

    # Objects the owner creates from now on, which is every future migration.
    for stmt in (
        "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {a}",
        "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT SELECT ON TABLES TO {r}",
        "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {a}",
        "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public GRANT SELECT ON SEQUENCES TO {r}",
    ):
        conn.execute(sql.SQL(stmt).format(o=ident(OWNER), a=ident(APP), r=ident(READER)))
    grant_existing(conn)


def grant_existing(conn) -> None:
    """Grant on what is already there, which default privileges don't reach.

    Also re-asserts the negative side: the reader has nothing but SELECT and the app has no
    TRUNCATE, REFERENCES or TRIGGER, whatever an earlier run granted.
    """
    ident = sql.Identifier
    fmt = dict(a=ident(APP), r=ident(READER))
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {a}, {r}").format(**fmt))
    conn.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {a}, {r}").format(**fmt))
    conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {a}").format(**fmt))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r}").format(**fmt))
    conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {a}").format(**fmt))
    conn.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}").format(**fmt))


# ---------------------------------------------------------------------------
# Moving an existing schema under dg_owner
# ---------------------------------------------------------------------------

# Objects that belong to an extension (pg_trgm's functions and operators) are not ours to
# re-own: `pg_depend` marks them with deptype 'e'.
_NOT_EXTENSION = (
    "NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = {cls}::regclass AND d.objid = {oid} "
    "AND d.deptype = 'e')"
)


def adopt(conn) -> dict[str, int]:
    """Make `dg_owner` the owner of every object in `public` that isn't an extension's.

    Changes ownership only: no row is touched and no object is dropped or recreated, but it is
    the change that makes the roles real on an install that predates them, so callers take a
    snapshot first. Returns how many objects of each kind were moved.
    """
    ident = sql.Identifier
    moved = {"relations": 0, "functions": 0, "types": 0}

    # Sequences owned by a column follow their table, so only free-standing ones are listed.
    rels = conn.execute(
        "SELECT c.oid, c.relname, c.relkind FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
        "AND c.relkind IN ('r','p','v','m','S','f') AND pg_get_userbyid(c.relowner) <> %s "
        "AND " + _NOT_EXTENSION.format(cls="'pg_class'", oid="c.oid") + " "
        "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass "
        "AND d.objid = c.oid AND d.deptype IN ('a','i'))) ORDER BY c.relkind = 'S', c.relname", (OWNER,)).fetchall()
    kind = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE", "f": "FOREIGN TABLE"}
    for r in rels:
        conn.execute(sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
            sql.SQL(kind[r["relkind"]]), ident("public"), ident(r["relname"]), ident(OWNER)))
        moved["relations"] += 1

    funcs = conn.execute(
        "SELECT p.oid, p.proname, p.prokind, pg_get_function_identity_arguments(p.oid) AS args FROM pg_proc p "
        "WHERE p.pronamespace = 'public'::regnamespace AND pg_get_userbyid(p.proowner) <> %s "
        "AND " + _NOT_EXTENSION.format(cls="'pg_proc'", oid="p.oid"), (OWNER,)).fetchall()
    for f in funcs:
        word = {"p": "PROCEDURE", "a": "AGGREGATE"}.get(f["prokind"], "FUNCTION")
        conn.execute(sql.SQL("ALTER {} {}.{}({}) OWNER TO {}").format(
            sql.SQL(word), ident("public"), ident(f["proname"]), sql.SQL(f["args"]), ident(OWNER)))
        moved["functions"] += 1

    # Enums and domains. A table's row type and array types follow their parents.
    types = conn.execute(
        "SELECT t.typname FROM pg_type t WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('e','d') "
        "AND pg_get_userbyid(t.typowner) <> %s AND " + _NOT_EXTENSION.format(cls="'pg_type'", oid="t.oid"), (OWNER,)).fetchall()
    for t in types:
        conn.execute(sql.SQL("ALTER TYPE {}.{} OWNER TO {}").format(ident("public"), ident(t["typname"]), ident(OWNER)))
        moved["types"] += 1
    return moved


def provision(conn, env_writer, *, existing_env: dict[str, str]) -> dict[str, str]:
    """Create the roles with fresh passwords and record them, or do neither.

    The same ordering as `rotate_db_password`: the database change is transactional and commits
    only once `.env` holds the new values, and `.env` is put back if the commit fails. Ending
    with the database on one password and `.env` on another would lock the roles out.
    `env_writer(values)` writes them and returns a function that undoes the write.
    """
    keep_app = existing_env.get(PASSWORD_KEY[APP])
    keep_reader = existing_env.get(PASSWORD_KEY[READER])
    passwords = {APP: keep_app or new_password(), READER: keep_reader or new_password()}
    conn.rollback()
    setup(conn, app_password=passwords[APP], reader_password=passwords[READER])
    try:
        undo = env_writer({PASSWORD_KEY[APP]: passwords[APP], PASSWORD_KEY[READER]: passwords[READER]})
    except BaseException:
        conn.rollback()
        raise
    try:
        conn.commit()
    except BaseException:
        undo()
        raise
    return {PASSWORD_KEY[APP]: passwords[APP], PASSWORD_KEY[READER]: passwords[READER]}


# ---------------------------------------------------------------------------
# Reporting, for `dg doctor`
# ---------------------------------------------------------------------------

SURFACES = (("ingest", APP), ("query / report / status / ask / context", READER))


def surface_report(admin_dsn: str, env: dict[str, str], connect) -> list[dict]:
    """For each surface: the role it asks for, who it really connects as, and whether that is a
    superuser. `connect(dsn)` opens a connection (injected so a test needs no network)."""
    out = []
    for surface, role in SURFACES:
        dsn = dsn_for(admin_dsn, role, env)
        row = {"surface": surface, "wanted": role, "user": user_of(dsn), "superuser": None, "error": None}
        try:
            conn = connect(dsn)
            try:
                row["superuser"] = is_superuser(conn)
                row["user"] = conn.execute("SELECT current_user AS u").fetchone()["u"]
            finally:
                conn.close()
        except Exception as exc:  # reported, not raised: doctor must say what it saw
            row["error"] = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        out.append(row)
    return out
