"""LLM-inference edge proposal — STUBBED, but wired through the real gate (§5.1).

`propose()` returns nothing yet: no model is called in Phase 1. What is built is
everything around it, because the gate is the part that carries risk. An ungated
inference path is the failure mode the PRD calls out by name — inferred edges
treated as fact, and a long tail of speculative connections diluting every traversal.

Three independent bounds, none of which live only in this file:

1. Relevance threshold (graph_config.inferred_edge_min_relevance, v1 0.75)
2. Per-node cap (graph_config.inferred_edge_max_per_node, v1 4)
3. Decision nodes may never be touched by an inferred edge

All three are enforced by the `edge_inferred_gate` trigger in migration 0001, not
here. This module re-reads the config only to avoid proposing work that will be
rejected; if these checks were deleted the bounds would still hold. That is the point
— a future caller that forgets to check cannot widen the blast radius.

Rejection is expected flow, not an error. Each proposal is inserted inside a
SAVEPOINT so that a refusal rolls back one edge instead of aborting the whole
ingestion transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Proposal:
    src_node_id: int
    dst_node_id: int
    edge_type: str
    relevance: float
    rationale: str


@dataclass
class GateOutcome:
    accepted: int = 0
    rejected_threshold: int = 0
    rejected_cap: int = 0
    rejected_decision: int = 0

    @property
    def total_rejected(self) -> int:
        return self.rejected_threshold + self.rejected_cap + self.rejected_decision


def propose(conn: psycopg.Connection, repo_node_id: int) -> list[Proposal]:
    """STUB (Phase 1): always returns no proposals.

    Intended contract when implemented: consider only pairs of entities that are
    plausibly related but have NO explicit path between them — per §5.1, inference
    exists to bridge a gap, not to add a second opinion where the graph already has
    an answer. Candidate generation must therefore run *after* explicit extraction
    and must exclude already-connected pairs.
    """
    log.debug("inference proposal path is stubbed in Phase 1; no edges proposed")
    return []


def persist(conn: psycopg.Connection, proposals: list[Proposal]) -> GateOutcome:
    """Attempt each proposal against the DB gate. Refusals are counted, not raised."""
    outcome = GateOutcome()

    for p in proposals:
        try:
            with conn.transaction():  # SAVEPOINT: one rejection must not kill the run
                conn.execute(
                    """
                    INSERT INTO edge (src_node_id, dst_node_id, edge_type,
                                      tag, evidence_tier, extractor, source_ref,
                                      relevance, valid_from)
                    VALUES (%s, %s, %s, 'inferred', 'inferred', %s, %s, %s, now())
                    """,
                    (
                        p.src_node_id,
                        p.dst_node_id,
                        p.edge_type,
                        "llm_similarity_v1",
                        p.rationale[:500],
                        p.relevance,
                    ),
                )
            outcome.accepted += 1
        except psycopg.errors.CheckViolation as exc:
            message = str(exc)
            if "decision node" in message:
                outcome.rejected_decision += 1
            elif "below threshold" in message:
                outcome.rejected_threshold += 1
            elif "inferred edges (cap" in message:
                outcome.rejected_cap += 1
            else:
                raise
            log.debug("proposal refused by gate: %s", message.strip())

    if outcome.total_rejected:
        log.info(
            "inference gate: %s accepted, %s refused (threshold=%s cap=%s decision=%s)",
            outcome.accepted,
            outcome.total_rejected,
            outcome.rejected_threshold,
            outcome.rejected_cap,
            outcome.rejected_decision,
        )
    return outcome
