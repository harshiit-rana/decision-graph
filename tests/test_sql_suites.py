"""Every SQL suite must be able to fail the run (issue #62).

CI invokes each file in `db/tests/` with `psql -v ON_ERROR_STOP=1`, which promotes a SQL
*error* to a non-zero exit. A check that merely records `passed = false` in the results
table is not an error: four of the five suites printed a `FAIL` row inside a collapsed CI
group and exited 0, so 30 of the 39 advertised checks could not turn the build red.

That is #55's failure one layer out — there, five checks were dead because the file aborted
on its first statement and nothing ran them; here they run and cannot fail. Both are silent,
and a suite that cannot fail is worth roughly what a suite nobody runs is worth.

Standalone: reads the files, needs no database. The point is to catch a NEW suite written
without the ending rather than to re-verify the four that have it, since the new one is the
one nobody will think to check.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SUITES = sorted((Path(__file__).resolve().parent.parent / "db" / "tests").glob("*.sql"))


class SqlSuiteFailureTest(unittest.TestCase):
    def test_suites_are_discovered(self) -> None:
        # A glob that matches nothing would make every assertion below vacuously true.
        self.assertGreaterEqual(len(SUITES), 5, "no SQL suites found to check")

    def test_every_suite_raises_on_a_failed_check(self) -> None:
        for path in SUITES:
            with self.subTest(suite=path.name):
                self.assertIn(
                    "RAISE EXCEPTION",
                    path.read_text(encoding="utf-8"),
                    f"{path.name} records check results but never raises, so a failing "
                    "check would exit 0 and leave CI green",
                )

    def test_every_suite_rolls_back(self) -> None:
        # The suites seed fixtures into the real database. They are safe to run against a
        # populated graph only because each one ends by discarding its work; a file that
        # forgot would quietly leave test nodes behind in whatever database CI or a
        # contributor pointed it at.
        for path in SUITES:
            with self.subTest(suite=path.name):
                self.assertIn(
                    "ROLLBACK",
                    path.read_text(encoding="utf-8"),
                    f"{path.name} does not roll back",
                )


if __name__ == "__main__":
    unittest.main()
