"""A mechanical citation check on `dg ask`'s prose (issue #125).

`dg ask` prints the deterministic trace, then a model's paragraph. The trace is authoritative,
but a reader trusts the paragraph, and nothing checked it. This does, with regular
expressions and set comparison only: no extra model call, and no judgement about whether the
reasoning is right.

**What it verifies**
- Every artifact the paragraph *names* is on the trail printed above it: issue and pull
  request numbers (`#5895`, `issue #5895`, `PR 5898`), commit shas (`commit eca5fd1`, any
  prefix of 7 or more that matches a trail commit), release tags (`release 3.1.2`), and
  GitHub URLs to any of those.
- A number the paragraph calls an issue is an issue on the trail, and likewise a pull request.
- A GitHub URL points at the repository the trail is about.
- Any other link is one the trail printed. A link the model invented is how an injected
  instruction would try to send a reader somewhere.
- The paragraph doesn't claim a stronger evidence tier than the trail grants: "corroborated"
  with no corroborated path, or "explicit"/"corroborated" when every path is inferred. A
  negated mention ("not corroborated") is skipped.
- When the answer used the inferred fallback, the paragraph says "inferred" somewhere.

**What it does not verify**
- Whether the reasoning, or any sentence that names no artifact, is correct.
- Other node types the trail can print (person, team, workflow, repository, branch, wiki page,
  CODEOWNERS scope). Their forms collide with ordinary English: "release notes", "team
  members", "branch protection". Checking them would warn about prose that is fine, and a
  warning that cries wolf gets ignored.
- Anything the model wrote that the extractor's patterns don't recognise. A clean result
  means nothing *recognisable* is unsupported. It is not a proof the paragraph is faithful.

What counts as "on the trail": the nodes on the answer's paths; the pull requests credited
with a Decision (a Why-walk stops at the Decision, so they aren't on a path but are named in
the answer); and anything that appears in a title or credit line the trail itself prints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .reasoning import Answer

# `issue #5895`, `pull request #5898`, `PR 5898`, `#5895`. The word is optional and `#` is
# optional only when a word is present, which is `retrieval._REFERENCE`'s rule for the same
# reason: a bare number is never a reference.
_NUMBER = re.compile(
    r"(?:\b(?P<word>issues?|pull\s+requests?|prs?|pull|comments?)\s*#?(?P<n1>\d+)"
    r"|(?<![\w/&])#(?P<n2>\d+))\b",
    re.IGNORECASE,
)
# `commit eca5fd1`, `sha eca5fd1`, and the bare form only inside backticks or a URL, where a
# hex-looking word is far more likely to be a sha than the word "decade".
_SHA = re.compile(r"\b(?:commit|sha)\s+`?(?P<s1>[0-9a-f]{7,40})\b|`(?P<s2>[0-9a-f]{7,40})`",
                  re.IGNORECASE)
_RELEASE = re.compile(r"\brelease\s+`?v?(?P<tag>\d+(?:\.\d+)+\w*)\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_GH_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<repo>[\w.-]+/[\w.-]+)/"
    r"(?:(?P<kind>issues|pull|commit|releases/tag)/(?P<id>[\w.-]+))?",
    re.IGNORECASE,
)
_TIER = re.compile(r"\b(explicit|corroborated|inferred)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|without|lacks?|isn't|aren't|wasn't|"
                       r"weren't|doesn't|don't|cannot|can't)\b", re.IGNORECASE)

TIER_RANK = {"inferred": 0, "explicit": 1, "corroborated": 2}

WARNING_INTRO = ("Citation check: the paragraph above says things the trail does not support. "
                 "Trust the trail, not the paragraph, for these:")
CLEAN = "Citation check: every artifact, link and tier the paragraph names is on the trail above."
NOTHING = ("Citation check: the paragraph names no artifact the check recognises, so there was "
           "nothing to compare with the trail.")
LIMITS = ("This checks names, links and tiers only, not whether the reasoning is right, and not "
          "people, teams or files.")


@dataclass(frozen=True)
class Finding:
    kind: str  # unsupported_reference | wrong_type | other_repository | foreign_link |
    #            overstated_tier | inferred_not_stated
    text: str  # what the paragraph said
    detail: str  # fixed sentence from this module

    def line(self) -> str:
        return f"  - {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    references_checked: int = 0
    tier_words_checked: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings

    def lines(self) -> list[str]:
        if self.findings:
            return [WARNING_INTRO, *[f.line() for f in self.findings], LIMITS]
        if self.references_checked == 0 and self.tier_words_checked == 0:
            return [NOTHING, LIMITS]
        return [CLEAN, LIMITS]

    def to_dict(self) -> dict:
        """Machine-readable form, for the JSON contract and the MCP server."""
        return {
            "clean": self.clean,
            "references_checked": self.references_checked,
            "tier_words_checked": self.tier_words_checked,
            "findings": [{"kind": f.kind, "text": f.text, "detail": f.detail}
                         for f in self.findings],
            "verifies": ["artifact references", "link targets", "claimed evidence tiers"],
            "does_not_verify": ["reasoning", "sentences naming no artifact",
                                "people, teams, workflows or files"],
        }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    kind: str  # number | sha | release | url
    value: str
    hint: str | None = None  # "issue" | "pull_request" | "comment" for a typed number
    text: str = ""


def _normalise_url(url: str) -> str:
    return url.rstrip(".,;:!?)").rstrip("/").lower()


def extract(text: str) -> list[Ref]:
    """Every reference the extractor recognises in `text`, in order of appearance."""
    found: list[tuple[int, Ref]] = []
    for m in _NUMBER.finditer(text):
        word = (m.group("word") or "").lower().replace("\n", " ")
        hint = None
        if word.startswith("issue"):
            hint = "issue"
        elif word.startswith(("pull", "pr")):
            hint = "pull_request"
        elif word.startswith("comment"):
            hint = "comment"
        found.append((m.start(), Ref("number", m.group("n1") or m.group("n2"), hint, m.group(0))))
    for m in _SHA.finditer(text):
        found.append((m.start(), Ref("sha", (m.group("s1") or m.group("s2")).lower(), None, m.group(0))))
    for m in _RELEASE.finditer(text):
        found.append((m.start(), Ref("release", m.group("tag").lower(), None, m.group(0))))
    for m in _URL.finditer(text):
        found.append((m.start(), Ref("url", _normalise_url(m.group(0)), None, m.group(0).rstrip(".,;:!?)"))))
    return [r for _, r in sorted(found, key=lambda x: x[0])]


# ---------------------------------------------------------------------------
# What the trail holds
# ---------------------------------------------------------------------------


@dataclass
class Trail:
    numbers: dict[str, set[str]] = field(default_factory=dict)  # number -> node types
    shas: set[str] = field(default_factory=set)
    releases: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    repos: set[str] = field(default_factory=set)
    tiers: set[str] = field(default_factory=set)
    inferred_fallback: bool = False

    def add_text(self, text: str | None) -> None:
        """Whatever the trail prints in a title or credit line counts as on the trail."""
        for ref in extract(text or ""):
            self._add_ref(ref)

    def _add_ref(self, ref: Ref) -> None:
        if ref.kind == "number":
            self.numbers.setdefault(ref.value, set()).add(ref.hint or "*")
        elif ref.kind == "sha":
            self.shas.add(ref.value)
        elif ref.kind == "release":
            self.releases.add(ref.value)
        elif ref.kind == "url":
            self._add_url(ref.value)

    def _add_url(self, url: str) -> None:
        url = _normalise_url(url)
        self.urls.add(url)
        m = _GH_URL.match(url)
        if m:
            self.repos.add(m.group("repo").lower())


def trail_of(answer: Answer, *, annotations: dict[int, str] | None = None,
             links: dict[str, str] | None = None) -> Trail:
    t = Trail(inferred_fallback=answer.used_inferred_fallback)
    for path in answer.paths:
        t.tiers.add(path.tier)
        for step in path.steps:
            t.tiers.add(step.evidence_tier)
        for nid in path.node_ids:
            ref = path.ref(nid)
            if ref.url:
                t._add_url(ref.url)
            ext = ref.external_id or ""
            if ref.node_type in ("issue", "pull_request", "comment") and ext.isdigit():
                t.numbers.setdefault(ext, set()).add(ref.node_type)
            elif ref.node_type == "commit" and ext:
                t.shas.add(ext.lower())
            elif ref.node_type == "release" and ext:
                t.releases.add(ext.lower().lstrip("v"))
            t.add_text(ref.title)
    for note in (annotations or {}).values():
        t.add_text(note)
    for label, url in (links or {}).items():
        t.add_text(label)
        t._add_url(url)
    return t


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def check(prose: str, trail: Trail) -> Report:
    report = Report()
    seen: set[tuple[str, str]] = set()

    for ref in extract(prose):
        key = (ref.kind, ref.value)
        if key in seen:
            continue
        seen.add(key)
        if ref.kind == "number":
            report.references_checked += 1
            types = trail.numbers.get(ref.value)
            if types is None:
                report.findings.append(Finding(
                    "unsupported_reference", ref.text,
                    f"It names {ref.text.strip()!r}, which is not on the trail."))
            elif ref.hint and "*" not in types and ref.hint not in types:
                actual = "/".join(sorted(t.replace("_", " ") for t in types))
                report.findings.append(Finding(
                    "wrong_type", ref.text,
                    f"It calls #{ref.value} a {ref.hint.replace('_', ' ')}; the trail shows "
                    f"{actual} #{ref.value}."))
        elif ref.kind == "sha":
            report.references_checked += 1
            if not any(s.startswith(ref.value) or ref.value.startswith(s) for s in trail.shas):
                report.findings.append(Finding(
                    "unsupported_reference", ref.text,
                    f"It names commit {ref.value[:7]}, which is not on the trail."))
        elif ref.kind == "release":
            report.references_checked += 1
            if ref.value not in trail.releases:
                report.findings.append(Finding(
                    "unsupported_reference", ref.text,
                    f"It names release {ref.value}, which is not on the trail."))
        elif ref.kind == "url":
            report.references_checked += 1
            _check_url(ref, trail, report)

    _check_tiers(prose, trail, report)
    return report


def _check_url(ref: Ref, trail: Trail, report: Report) -> None:
    if ref.value in trail.urls:
        return
    m = _GH_URL.match(ref.value)
    if not m:
        report.findings.append(Finding(
            "foreign_link", ref.text,
            f"It links to {ref.text}, which the trail never printed."))
        return
    if trail.repos and m.group("repo").lower() not in trail.repos:
        report.findings.append(Finding(
            "other_repository", ref.text,
            f"It links to {m.group('repo')}, not the repository the trail is about "
            f"({', '.join(sorted(trail.repos))})."))
        return
    kind, ident = m.group("kind"), (m.group("id") or "").lower()
    if not kind:
        return  # the repository home page: not an artifact claim
    if kind in ("issues", "pull"):
        ok = ident in trail.numbers
    elif kind == "commit":
        ok = any(s.startswith(ident) or ident.startswith(s) for s in trail.shas)
    else:
        ok = ident.lstrip("v") in trail.releases
    if not ok:
        report.findings.append(Finding(
            "unsupported_reference", ref.text,
            f"It links to {ref.text}, which is not on the trail."))


def _check_tiers(prose: str, trail: Trail, report: Report) -> None:
    if not trail.tiers:
        return
    strongest = max(TIER_RANK[t] for t in trail.tiers)
    claimed_inferred = False
    flagged: set[str] = set()
    for m in _TIER.finditer(prose):
        word = m.group(1).lower()
        report.tier_words_checked += 1
        if word == "inferred":
            claimed_inferred = True
            continue
        if _NEGATION.search(prose[max(0, m.start() - 24):m.start()]):
            continue
        if TIER_RANK[word] > strongest and word not in flagged:
            flagged.add(word)
            granted = ", ".join(sorted(trail.tiers, key=TIER_RANK.get))
            report.findings.append(Finding(
                "overstated_tier", m.group(0),
                f"It calls evidence {word!r}; the trail grades it {granted}."))
    if trail.inferred_fallback and not claimed_inferred:
        report.findings.append(Finding(
            "inferred_not_stated", "",
            "The answer used the inferred fallback, and the paragraph never says 'inferred'."))


def check_answer(prose: str, answer: Answer, *, annotations: dict[int, str] | None = None,
                 links: dict[str, str] | None = None) -> Report:
    return check(prose, trail_of(answer, annotations=annotations, links=links))


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.kind] = out.get(f.kind, 0) + 1
    return out
