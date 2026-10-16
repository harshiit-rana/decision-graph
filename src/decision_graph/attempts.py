"""Prior attempts: "has someone already tried this, and what happened?" (issue #137).

This is often the question an engineer or a coding agent most needs answered before
starting work. The graph already holds the answer for most declined work: pull requests
closed unmerged, and issues closed as not planned or as duplicates. Until now it only
surfaced them one at a time, as refusals to a Why question about an artifact you had to
know already.

This is a query-time view (ADR 0003, option A). It adds no schema and asserts nothing new.
Everything it says is a closure fact the graph already records, read by the same
`trace.closure_fact` the refusal renderer uses:

- **how** an attempt ended (GitHub's `state_reason`, or `merged_at IS NULL` on a closed
  pull request), and when;
- whether the **same thread later landed**, meaning a merged pull request shares its
  `thread_key`. "Tried in #A, landed in #B" is explicit in the graph and is the most useful
  case;
- the last word from someone with standing, quoted under the #109 rule: what was said,
  never why it closed.

It never states a reason for a closure. Matching is lexical (exact title, identifier,
prefix, trigram), so a differently-worded attempt is missed. Paraphrase retrieval is
unmeasured (#144), and the output says so every time rather than letting an empty list
read as "nobody tried".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import retrieval, trace
from .trace import Standing

# How many retrieval candidates to scan for attempts. Retrieval ranks every artifact, not
# only ended ones, so it is asked for more than will be shown and the list is filtered
# afterwards.
SCAN_LIMIT = 200
DEFAULT_LIMIT = 10

ENDINGS = {
    "unmerged": "closed without merging",
    "not_planned": 'closed as "not planned"',
    "duplicate": "closed as a duplicate",
}

NO_REASON_CAVEAT = (
    "These are how each attempt ended, not why. The graph records no link between a "
    "comment and a closure, and closed without landing covers rejection, supersession and "
    "abandonment alike."
)

LEXICAL_CAVEAT = (
    "Matching is lexical (titles and identifiers), so a differently-worded attempt is "
    "missed, and only the ingested window is searched."
)


@dataclass(frozen=True)
class Landed:
    node_id: int
    external_id: str
    title: str | None
    url: str | None
    merged_at: datetime | None

    @property
    def ref(self) -> str:
        return trace.ref("pull_request", self.external_id)


@dataclass
class Attempt:
    candidate: retrieval.Candidate
    url: str | None
    ending: str  # a key of ENDINGS
    closed_at: datetime | None
    last_word: Standing | None
    landed_in: list[Landed] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return trace.ref(self.candidate.node_type, self.candidate.external_id)


_LANDED_SQL = """
SELECT n.id, n.external_id, n.title, n.url, p.merged_at
  FROM node attempt
  JOIN node n         ON n.thread_key = attempt.thread_key AND n.id <> attempt.id
  JOIN pull_request p ON p.node_id = n.id
 WHERE attempt.id = %s
   AND attempt.thread_key IS NOT NULL
   AND p.merged_at IS NOT NULL
 ORDER BY p.merged_at, n.id
"""


def find_attempts(conn, query: str, *, repo_node_id: int | None = None,
                  limit: int = DEFAULT_LIMIT) -> list[Attempt]:
    """Ended issues and pull requests that match `query`, best match first."""
    candidates = retrieval.find_candidates(
        conn, query, node_types=("issue", "pull_request"), repo_node_id=repo_node_id,
        limit=SCAN_LIMIT,
    )
    found: list[Attempt] = []
    for c in candidates:
        closure = trace.closure_fact(conn, c.node_id)
        if closure is None:
            continue
        url_row = conn.execute("SELECT url FROM node WHERE id = %s", (c.node_id,)).fetchone()
        landed = [
            Landed(r["id"], r["external_id"], r["title"], r["url"], r["merged_at"])
            for r in conn.execute(_LANDED_SQL, (c.node_id,)).fetchall()
        ]
        found.append(Attempt(
            candidate=c, url=url_row["url"] if url_row else None, ending=closure.kind,
            closed_at=closure.closed_at, last_word=closure.last_word, landed_in=landed,
        ))
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_MATCH_MEANING = {
    "exact": "exact title match",
    "identifier": "matched by identifier",
    "prefix": "title starts with your query",
    "fuzzy": "closest text match",
}


def render(query: str, attempts: list[Attempt], *, out) -> None:
    import textwrap

    from . import render as r

    p = lambda s="": print(s, file=out)  # noqa: E731
    p(r.bold(f"Has this been tried before?   {r.dim(repr(query))}"))
    p()
    if not attempts:
        p("  No earlier attempt in the ingested window matches this.")
        p()
        p("    That is not evidence that nobody tried. " + LEXICAL_CAVEAT)
        p()
        return

    p(r.bold(f"  {r.plural(len(attempts), 'earlier attempt')} in the ingested window"))
    p()
    for i, a in enumerate(attempts, 1):
        when = f" on {a.closed_at:%Y-%m-%d}" if a.closed_at else ""
        how = _MATCH_MEANING.get(a.candidate.match, a.candidate.match)
        p(f"  [{i}] {r.bold(a.ref)}  {r._clip(a.candidate.title, 56)}")
        p(r.dim(f"      {how}"))
        p(f"      {ENDINGS.get(a.ending, a.ending)}{when}")
        for landed in a.landed_in:
            merged = f", merged {landed.merged_at:%Y-%m-%d}" if landed.merged_at else ""
            p(f"      the same thread later landed in {landed.ref}{merged}")
        if a.last_word is not None:
            said = a.last_word
            at = f", {said.at:%Y-%m-%d}" if said.at else ""
            p(f"      the last word on it, from a {said.association}{at}:")
            flat = " ".join(r.safe(said.body).split())
            for line in textwrap.wrap(flat, width=64, max_lines=3, placeholder=" ..."):
                p(f"        {r.dim(line)}")
        if a.url:
            p(r.dim(f"      {a.url}"))
        for landed in a.landed_in:
            if landed.url:
                p(r.dim(f"      {landed.url}"))
        p()
    p("  " + NO_REASON_CAVEAT)
    p("  " + LEXICAL_CAVEAT)
    p()


# ---------------------------------------------------------------------------
# JSON (the #135 contract, kind "attempts")
# ---------------------------------------------------------------------------


def document(query: str, attempts: list[Attempt]) -> dict:
    from . import contract

    def attempt_doc(a: Attempt) -> dict:
        c = a.candidate
        said = a.last_word
        return {
            "start": {**contract.node_doc(c.node_id, c.node_type, c.external_id, c.title, a.url),
                      "match": c.match},
            "ending": a.ending,
            "closed_at": a.closed_at,
            "last_word": None if said is None else {
                "body": said.body, "author_association": said.association, "at": said.at,
                "caveat": contract.LAST_WORD_CAVEAT,
            },
            "landed_in": [
                {**contract.node_doc(l.node_id, "pull_request", l.external_id, l.title, l.url),
                 "merged_at": l.merged_at}
                for l in a.landed_in
            ],
        }

    return {
        "schema_version": contract.SCHEMA_VERSION,
        "kind": "attempts",
        "query": query,
        "repository_text_fields": list(contract.REPOSITORY_TEXT_FIELDS),
        "attempts": [attempt_doc(a) for a in attempts],
        "caveats": [NO_REASON_CAVEAT, LEXICAL_CAVEAT],
    }
