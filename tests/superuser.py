"""Skip a test that needs a database superuser when DATABASE_URL is not one (#172).

CI runs the DB-gated suite twice: once as the superuser, and once as the least-privilege `dg_app`
role, which is how the ordinary ingestion and query paths are shown to work without superuser
rights. A handful of tests create databases and roles, restore snapshots or rotate passwords,
which only a superuser can do; they skip on the second run instead of failing.
"""

from __future__ import annotations

import os
import unittest

DSN = os.environ.get("DATABASE_URL")


def _is_superuser() -> bool:
    if not DSN:
        return False
    import psycopg

    try:
        with psycopg.connect(DSN) as conn:
            return bool(conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0])
    except psycopg.Error:
        return False


IS_SUPERUSER = _is_superuser()
requires_superuser = unittest.skipUnless(IS_SUPERUSER, "needs a superuser DATABASE_URL (creates roles or databases)")
