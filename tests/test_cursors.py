"""Cursor phase semantics (issue #47 follow-up, migration 0012).

stdlib unittest, no test dependency: `python -m unittest discover tests`.

`phase` is owned by one strategy of three. These tests pin both halves of that: that only
COMMITTED_DESC is born with a phase, and — the part that makes the change safe — that
withdrawing the phase from the other two changes no query parameter they send. If the
second half ever fails, the field was load-bearing after all and 0012 was wrong.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from decision_graph.cursors import (
    RESOURCE_STRATEGY,
    Cursor,
    Strategy,
    initial_phase,
    query_params,
)

FLOOR = datetime(2025, 9, 1, tzinfo=timezone.utc)
MARK = datetime(2026, 8, 22, tzinfo=timezone.utc)


def _cursor(resource: str, phase: str | None, **kw) -> Cursor:
    return Cursor(
        repo_node_id=1,
        resource=resource,
        phase=phase,
        window_floor=kw.get("window_floor", FLOOR),
        backfill_cursor=kw.get("backfill_cursor"),
        steady_watermark=kw.get("steady_watermark"),
        last_etag=None,
    )


class InitialPhaseTest(unittest.TestCase):
    def test_only_committed_desc_is_born_with_a_phase(self) -> None:
        """Table-driven so a new resource cannot be added without deciding this."""
        for resource, strategy in RESOURCE_STRATEGY.items():
            with self.subTest(resource=resource):
                phase = initial_phase(resource)
                if strategy is Strategy.COMMITTED_DESC:
                    self.assertEqual(phase, "backfill")
                else:
                    self.assertIsNone(phase)

    def test_commits_starts_in_backfill(self) -> None:
        self.assertEqual(initial_phase("commits"), "backfill")

    def test_forward_walkers_have_no_phase_at_all(self) -> None:
        """Not 'steady' — that would assert a floor had been reached. NULL declines to
        claim anything, which is the only true statement available (#47)."""
        self.assertIsNone(initial_phase("issues"))
        self.assertIsNone(initial_phase("pulls"))

    def test_unwindowed_resources_have_no_phase_either(self) -> None:
        for resource in ("releases", "workflows", "codeowners"):
            with self.subTest(resource=resource):
                self.assertIsNone(initial_phase(resource))

    def test_a_null_phase_is_not_backfilling(self) -> None:
        self.assertFalse(_cursor("issues", None).is_backfilling)
        self.assertTrue(_cursor("commits", "backfill").is_backfilling)


class PhaseIsInertForForwardWalkersTest(unittest.TestCase):
    """The safety argument for 0012, as a test.

    Dropping the phase is only safe if nothing outside COMMITTED_DESC reads it. Rather than
    assert that by inspection, compare the parameters a forward walker actually sends with
    the phase set every possible way.
    """

    def test_updated_asc_params_do_not_depend_on_phase(self) -> None:
        for resource in ("issues", "pulls"):
            for state in ("backfill", "steady", None):
                with self.subTest(resource=resource, phase=state):
                    self.assertEqual(
                        query_params(_cursor(resource, state, steady_watermark=MARK)),
                        query_params(_cursor(resource, "backfill", steady_watermark=MARK)),
                    )

    def test_updated_asc_walks_from_the_watermark_regardless_of_phase(self) -> None:
        params = query_params(_cursor("issues", None, steady_watermark=MARK))
        self.assertEqual(params["since"], "2026-08-22T00:00:00Z")
        self.assertEqual(params["direction"], "asc")

    def test_updated_asc_falls_back_to_the_window_floor_with_no_watermark(self) -> None:
        params = query_params(_cursor("issues", None))
        self.assertEqual(params["since"], "2025-09-01T00:00:00Z")

    def test_full_resources_send_no_params_whatever_the_phase(self) -> None:
        self.assertEqual(query_params(_cursor("releases", None)), {})
        self.assertEqual(query_params(_cursor("releases", "backfill")), {})

    def test_committed_desc_still_reads_its_phase(self) -> None:
        """The other side of the claim: for the one strategy that owns the field, phase
        genuinely changes the request. If this ever passes trivially, the field is dead."""
        backfilling = query_params(
            _cursor("commits", "backfill", backfill_cursor=MARK, steady_watermark=MARK)
        )
        steady = query_params(
            _cursor("commits", "steady", backfill_cursor=MARK, steady_watermark=MARK)
        )
        self.assertIn("until", backfilling)
        self.assertNotIn("until", steady)
        self.assertNotEqual(backfilling, steady)


if __name__ == "__main__":
    unittest.main()
