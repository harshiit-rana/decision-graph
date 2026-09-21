"""How a traversal is described, in the one place every reader shares (issue #69).

Three things render an Engine 2 answer: `dg query`, the §9 evaluation report, and the
prompt `dg ask` hands to a model. They must agree about what a node *is* and what a
Decision is *credited to*, because a reader who checks one against another is doing the
adjudication §9 depends on, and two implementations would drift exactly where that matters.
That is the same argument `reasoning._walk` makes for having one traversal rather than two.

The rule for everything here: **say what the graph holds, in the reader's vocabulary, and
nothing else.** A label may translate `node:620` into `issue #5895` because the graph stores
that number. It may not translate a `motivated_by` edge into "the maintainers wanted", which
the graph does not hold and a reader could not check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Node types whose external_id is a number a human uses. A commit's is a 40-character sha,
# which is an identifier but not a readable one, so it is abbreviated the way git does.
_NUMBERED = {"issue", "pull_request"}

# What each tier means, in one sentence, for a reader who has not read §5.4. Printed once
# per answer rather than per edge: the tier is the most important thing on the page and the
# least self-explanatory, and a reader who does not know what "corroborated" is has no way
# to weigh the answer they were given.
TIER_MEANING = {
    "explicit": "stated in the repository itself — a closing keyword, a review, a commit list",
    "corroborated": "explicit, and the thread carries 3 of 4 independent kinds of signal",
    "inferred": "the system's own guess; no explicit path existed, so this is not a record",
}

# A one-word rank printed next to the tier name so a reader does not have to work out which
# tier is better. Corroborated is printed as "strongest" rather than "better than explicit"
# because the absolute claim is more useful than a relative one when only one tier appears.
TIER_STRENGTH = {
    "explicit": "strong",
    "corroborated": "strongest",
    "inferred": "weak — treat as a lead, not a record",
}

# Kept from the original renderer. Colour is not available when output is piped -- which is
# every CI log and every `dg query > file` -- so the tier must survive in ASCII too.
TIER_MARK = {"explicit": "==", "corroborated": "++", "inferred": "~~"}



def ref(node_type: str | None, external_id: str | None) -> str:
    """A node's identity as it exists outside this database.

    `issue:620` is a primary key; the thing a reader can act on is `issue #5895`. Both the
    number and the URL have been stored on every node since migration 0001 and neither was
    ever printed.
    """
    if not external_id:
        return node_type or "?"
    if node_type in _NUMBERED:
        return f"{node_type.replace('_', ' ')} #{external_id}"
    if node_type == "commit":
        return f"commit {external_id[:7]}"
    if node_type == "decision":
        # A Decision's external_id is its thread_key, which names the CLUSTER and, where a
        # change took two attempts, names the abandoned pull request (issue #19). Printing
        # it as though it identified the work is the misleading label that issue is about,
        # so the cluster key is not the headline -- `decision_annotations` supplies what the
        # Decision is actually credited to.
        return "decision"
    return f"{node_type} {external_id}"


# ---------------------------------------------------------------------------
# What a Decision is credited to (issue #19)
# ---------------------------------------------------------------------------

DECISION_FACTS_SQL = """
SELECT d.node_id,
       d.status::text AS status,
       im.node_type   AS implementer_type,
       im.external_id AS implementer_ref,
       im.url         AS implementer_url,
       pr.merged_at
FROM decision d
LEFT JOIN edge e  ON e.src_node_id = d.node_id
                 AND e.edge_type   = 'implemented_by'
                 AND e.valid_to IS NULL
LEFT JOIN node im ON im.id = e.dst_node_id
LEFT JOIN pull_request pr ON pr.node_id = im.id
WHERE d.node_id = ANY(%s)
ORDER BY d.node_id, im.external_id
"""


def format_implementer(row: dict[str, Any]) -> str:
    kind = row["implementer_type"]
    ref_ = row["implementer_ref"]
    if kind == "commit":
        return f"commit {ref_[:7]}"
    if kind != "pull_request":
        return f"{kind} {ref_}"
    # Merge state is spelled out rather than implied. An unmerged implementer should not be
    # able to sit quietly in a trace -- post-#17 it cannot exist, and this is how a
    # regression would announce itself instead of reading as an ordinary path.
    when = f"merged {row['merged_at']:%Y-%m-%d}" if row["merged_at"] else "NOT MERGED"
    return f"PR #{ref_}, {when}"


def decision_annotations(conn, node_ids: list[int]) -> dict[int, str]:
    """Map Decision node ids to a short statement of what the graph credits them to.

    A Why-walk reaching a Decision across `motivated_by` stops there and never traverses
    `implemented_by`, so without this the only pull request number a reader sees is the one
    inside the `thread_key` -- and that key names the cluster, not the work. 6 of 15
    Decisions currently sit under a key naming a pull request that never merged.
    """
    if not node_ids:
        return {}

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(DECISION_FACTS_SQL, (sorted(set(node_ids)),)).fetchall():
        grouped.setdefault(row["node_id"], []).append(row)

    notes: dict[int, str] = {}
    for node_id, rows in grouped.items():
        credited = [r for r in rows if r["implementer_ref"]]
        if not credited:
            # Unreachable while the rubric holds. Surfaced rather than skipped: a Decision
            # asserting nothing is the single most important thing to show an adjudicator.
            notes[node_id] = "no current implementer"
            continue
        # Every current implementer, not the first. A LIMIT 1 here would have concealed the
        # double-implementer bug that the #17 fix introduced and then repaired.
        notes[node_id] = "implemented by " + "; ".join(format_implementer(r) for r in credited)
    return notes


def decision_links(conn, node_ids: list[int]) -> dict[str, str]:
    """`{"PR #5898": "https://github.com/..."}` for the artifacts credited with the work.

    The implementer is named in the answer but is not on the path -- a Why-walk stops at
    the Decision -- so without this it is the one artifact a reader is told about and
    cannot open. It is also the artifact that did the thing, which makes it the one most
    worth opening.
    """
    if not node_ids:
        return {}
    links: dict[str, str] = {}
    for row in conn.execute(DECISION_FACTS_SQL, (sorted(set(node_ids)),)).fetchall():
        if row["implementer_ref"] and row["implementer_url"]:
            links[ref(row["implementer_type"], row["implementer_ref"])] = row["implementer_url"]
    return links


# GitHub's associations that mean the writer speaks for the project rather than to it.
# CONTRIBUTOR is deliberately absent: it means "has had a pull request merged here", which
# is not standing to close someone else's request -- and NONE is how "ok, my bad" was
# written, by the reporter withdrawing their own issue.
_STANDING = ("OWNER", "MEMBER", "COLLABORATOR")


@dataclass(frozen=True)
class Standing:
    """The last thing someone with standing said on an artifact.

    Not "the reason it closed". The graph holds no edge asserting that any comment caused
    any closure and this does not invent one -- it is the most recent comment from someone
    who speaks for the project, offered with its association and date so a reader can weigh
    it themselves.
    """

    body: str
    association: str
    at: datetime | None


@dataclass(frozen=True)
class Closure:
    """How an artifact ended, in the cases where the ending is itself the answer.

    `kind` is the graph's own word for it, not an interpretation: `not_planned` and
    `duplicate` are GitHub's `state_reason` values, and `unmerged` means `merged_at IS
    NULL` on a closed pull request. None of the three says *why* -- that distinction is
    the whole point, and `render` is careful to keep it.
    """

    ref: str
    kind: str
    closed_at: datetime | None
    last_word: Standing | None = None


# The endings that explain a refusal rather than merely accompanying one. An issue closed
# `completed` is absent here on purpose: it refuses for an ordinary reason (the thread
# carried no rubric-qualifying evidence), and naming its closure would dress up a plain
# miss as an explanation.
_EXPLAINS_A_REFUSAL = {"not_planned", "duplicate", "unmerged"}


def closure_fact(conn, node_id: int) -> Closure | None:
    """How this artifact closed, when that is itself why a Why-walk found nothing.

    §5.1 asserts a Decision only where work landed, so an issue closed `not_planned` and a
    pull request closed without merging both produce no Decision *by design*. The graph
    records both facts and the extractor is deliberate about them -- `state_reason`
    "separates an issue that was resolved from one the maintainers declined", `merged_at`
    "is what distinguishes work that landed from work that was refused".

    The refusal never read either, and so told 237 of the 263 artifacts that refuse that
    "a change made without an issue leaves nothing to reconstruct a decision from" -- which
    is false for every one of them (issue #86). Returns None when the graph records no
    ending that would explain the silence, which is when the existing sentence is right.
    """
    row = conn.execute(
        """
        SELECT n.node_type,
               n.external_id,
               i.state_reason::text AS state_reason,
               i.closed_at          AS issue_closed_at,
               p.closed_at          AS pr_closed_at,
               p.merged_at          AS merged_at,
               p.state              AS pr_state
          FROM node n
          LEFT JOIN issue i         ON i.node_id = n.id
          LEFT JOIN pull_request p  ON p.node_id = n.id
         WHERE n.id = %s
        """,
        (node_id,),
    ).fetchone()
    if row is None:
        return None

    if row["node_type"] == "issue":
        kind, closed_at = row["state_reason"], row["issue_closed_at"]
    elif row["node_type"] == "pull_request":
        # Only a *closed* pull request is an ending. An open one has not been refused;
        # it has not been decided at all, and saying otherwise would be the same
        # over-claim in the other direction.
        if row["pr_state"] != "closed" or row["merged_at"] is not None:
            return None
        kind, closed_at = "unmerged", row["pr_closed_at"]
    else:
        return None

    if kind not in _EXPLAINS_A_REFUSAL:
        return None
    return Closure(
        ref=ref(row["node_type"], row["external_id"]),
        kind=kind,
        closed_at=closed_at,
        last_word=_last_word(conn, node_id),
    )


def _last_word(conn, node_id: int) -> Standing | None:
    """The most recent comment on this artifact from someone with standing.

    Comments arrived as artifacts in #106 and are ingested for whatever the issues cursor
    surfaced, so this is often None -- which is not an error, and the caller falls back to
    saying the discussion is not ingested, which is then true.

    Ordered by `created_at DESC` and taking one. The last word is the one a reader wants
    next to a closure; showing several would read as a transcript and invite the inference
    that the whole exchange is the rationale.
    """
    row = conn.execute(
        """
        SELECT c.body, c.author_association, c.created_at
          FROM comment c
         WHERE c.parent_node_id = %s
           AND c.author_association = ANY(%s)
           AND coalesce(btrim(c.body), '') <> ''
         ORDER BY c.created_at DESC NULLS LAST
         LIMIT 1
        """,
        (node_id, list(_STANDING)),
    ).fetchone()
    if row is None:
        return None
    return Standing(
        body=row["body"],
        association=row["author_association"],
        at=row["created_at"],
    )


def decision_reviewers(conn, node_ids: list[int]) -> dict[int, list[str]]:
    """Who reviewed the work each Decision is credited to.

    Invisible in both surfaces until now, and for a structural reason rather than an
    oversight: a `reviewed` edge runs person -> pull request, so it never touches the
    Decision node that `dg report`'s evidence table selects by, and `reviewed` is not in
    WHY_EDGES, so a Why-walk never crosses one either. The person who wrote a change was
    shown twice and the people who scrutinised it not at all.

    That is worth fixing out of proportion to its size because `reviewed` is one of the four
    corroboration categories §5.4 counts -- evidence the rubric already weighs and the
    reader could not see. On this corpus it reaches 3 of 15 Decisions and 5 reviewers, which
    is a fact about flask rather than about the query: 14 `reviewed` edges across 226 pull
    requests, already a documented limitation. A repo with mandatory review populates it
    heavily.

    Names only, in the order they reviewed. No ranking and no score: authorship and review
    volume are not expertise, and #91's concentration caveat refuses that inference for the
    same reason this does.
    """
    if not node_ids:
        return {}

    rows = conn.execute(
        """
        SELECT impl.src_node_id      AS decision_node_id,
               reviewer.title        AS login,
               min(e.observed_at)    AS first_reviewed
          FROM edge impl
          JOIN edge e        ON e.dst_node_id = impl.dst_node_id
                            AND e.edge_type = 'reviewed'
                            AND e.valid_to IS NULL
          JOIN node reviewer ON reviewer.id = e.src_node_id
                            AND reviewer.node_type = 'person'
         WHERE impl.src_node_id = ANY(%s)
           AND impl.edge_type = 'implemented_by'
           AND impl.valid_to IS NULL
           AND reviewer.title IS NOT NULL
         GROUP BY 1, 2
         ORDER BY 1, 3 NULLS LAST, 2
        """,
        (sorted(set(node_ids)),),
    ).fetchall()

    found: dict[int, list[str]] = {}
    for row in rows:
        names = found.setdefault(row["decision_node_id"], [])
        # A reviewer who reviewed twice is one reviewer. Deduplicated here rather than in
        # SQL so the ordering above -- earliest review first -- decides which occurrence
        # survives.
        if row["login"] not in names:
            names.append(row["login"])
    return found


def decision_statuses(conn, node_ids: list[int]) -> dict[int, str]:
    """Map Decision node ids to `explicit` or `reconstructed`.

    Worth showing beside the annotation because they answer different questions. The status
    says how the Decision came to exist -- itemised in a release note, or rebuilt from the
    rubric -- and nothing else on screen distinguishes those two.
    """
    if not node_ids:
        return {}
    rows = conn.execute(
        "SELECT node_id, status::text AS status FROM decision WHERE node_id = ANY(%s)",
        (sorted(set(node_ids)),),
    ).fetchall()
    return {r["node_id"]: r["status"] for r in rows}


def artifact_bodies(conn, node_ids: list[int]) -> dict[int, str]:
    """Map node ids to their stored text body or commit message.

    This provides the actual problem statement or motivation written by the human author
    in the issue, pull request, or commit, enabling deep context instead of just titles.
    """
    if not node_ids:
        return {}
    sql = """
    SELECT n.id,
           COALESCE(i.body, pr.body, c.message) AS body
    FROM node n
    LEFT JOIN issue i ON i.node_id = n.id
    LEFT JOIN pull_request pr ON pr.node_id = n.id
    LEFT JOIN commit c ON c.node_id = n.id
    WHERE n.id = ANY(%s)
      AND COALESCE(i.body, pr.body, c.message) IS NOT NULL
      AND trim(COALESCE(i.body, pr.body, c.message)) != ''
    """
    rows = conn.execute(sql, (sorted(set(node_ids)),)).fetchall()
    return {r["id"]: r["body"] for r in rows}

