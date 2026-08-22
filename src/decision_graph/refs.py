"""Cross-reference parsing — the core of extraction-first edge construction (§5.1).

Every edge produced here comes from a signal literally present in the text. Nothing
in this module guesses; guessing is inference.py's job and is gated separately.

Deliberately conservative: a missed edge leaves an artifact queryable on its own,
whereas a false edge corrupts a traversal and, worse, can push a Decision over the
reconstructed rubric bar on evidence that was never really there.
"""

from __future__ import annotations

import re

# GitHub's own closing keywords. Anything else that mentions an issue is a plain
# reference — the distinction matters because `closes` counts as Validation in the
# §5.1 rubric while `references` does not.
CLOSING_KEYWORDS = r"clos(?:e|es|ed)|fix(?:|es|ed)|resolv(?:e|es|ed)"

# The two patterns resist URL fragments (`.../changes/#5448`) by DIFFERENT mechanisms,
# which is worth stating because it is not obvious and one of them is implicit:
#
#   ISSUE_REF_RE  — explicit (?<![\w/]) lookbehind.
#   CLOSES_RE     — no lookbehind, but `\s*:?\s+` makes whitespace before '#' MANDATORY,
#                   so a '#' glued to '/' or a word character cannot match.
#
# Audited over 447 stored bodies: 39 CLOSES_RE matches, the preceding character was
# whitespace in every one, and there were zero refs it accepted that the lookbehind-
# guarded pattern rejected. Relaxing that `\s+` to `\s*` would silently reintroduce the
# false-positive class, so test_closing_keyword_rejects_url_fragments pins it.
CLOSES_RE = re.compile(rf"\b(?:{CLOSING_KEYWORDS})\b\s*:?\s+#(\d+)", re.IGNORECASE)
ISSUE_REF_RE = re.compile(r"(?<![\w/])#(\d+)\b")

# GitHub honours `fixes https://github.com/owner/repo/issues/N` exactly as it honours
# `fixes #N` -- it closes the issue on merge either way. The `\s+#` mechanism above
# rejects a docs anchor and this alike, because neither has whitespace before the
# digits: right for the anchor, wrong for a real reference. Two Decisions were lost to
# that (issue #50).
#
# The owner/repo is captured rather than skipped, because the URL carries its own and it
# is not always the repo being ingested -- a body reading `.../pallets/werkzeug/pull/3219`
# would otherwise resolve 3219 against THIS repo's numbers and invent an edge to an
# unrelated issue. The caller supplies the repo to compare against; a caller that does
# not know it gets the old behaviour, which is why `repo` defaults to None and not to a
# guess.
#
# `/issues/` only. A `/pull/` URL is a PR closing a PR, which is not the
# Motivation-Implementation shape clause 3 is about.
CLOSES_URL_RE = re.compile(
    rf"\b(?:{CLOSING_KEYWORDS})\b\s*:?\s+https?://(?:www\.)?github\.com/"
    r"([\w.-]+/[\w.-]+)/issues/(\d+)\b",
    re.IGNORECASE,
)

# Only full 40-character SHAs. Abbreviated SHAs are indistinguishable from ordinary
# hex strings in prose and produced false edges in early testing.
SHA_RE = re.compile(r"\b([0-9a-f]{40})\b")

# "Co-authored-by: Name <email>" — an explicit authorship signal git itself defines.
COAUTHOR_RE = re.compile(r"^Co-authored-by:\s*(.+?)\s*<(.+?)>\s*$", re.IGNORECASE | re.MULTILINE)


def closing_refs(text: str | None, repo: str | None = None) -> set[int]:
    """Issue/PR numbers this text explicitly closes.

    `repo` is the "owner/name" being ingested. Pass it to also honour the full-URL form,
    `fixes https://github.com/owner/name/issues/N`, which GitHub treats as closing. Slugs
    are compared case-insensitively because GitHub's are, and a URL naming a DIFFERENT
    repository is ignored: its numbers are not this repository's numbers.
    """
    if not text:
        return set()
    found = {int(m) for m in CLOSES_RE.findall(text)}
    if repo:
        target = repo.casefold()
        found |= {
            int(number)
            for slug, number in CLOSES_URL_RE.findall(text)
            if slug.casefold() == target
        }
    return found


def mentioned_refs(text: str | None, repo: str | None = None) -> set[int]:
    """Issue/PR numbers mentioned but not closed.

    Takes `repo` for the same reason: a number that is a *closing* reference in URL form
    must not also be reported as a bare mention, or one artifact would earn both a `closes`
    and a `references` edge and the weaker one would muddy its provenance.
    """
    if not text:
        return set()
    return {int(m) for m in ISSUE_REF_RE.findall(text)} - closing_refs(text, repo)


def commit_refs(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(SHA_RE.findall(text.lower()))


def coauthors(message: str | None) -> list[tuple[str, str]]:
    if not message:
        return []
    return [(name, email) for name, email in COAUTHOR_RE.findall(message)]


def parse_codeowners(text: str) -> list[tuple[int, str, list[str]]]:
    """Parse CODEOWNERS into (line_number, path_pattern, owners).

    Built and tested even though pallets/flask ships no CODEOWNERS file (issue #1),
    so the `owns` extractor no-ops there. Kept because the parser is repo-independent
    and the gap is a property of the target repo, not of the system.
    """
    rules: list[tuple[int, str, list[str]]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, owners = parts[0], [o.lstrip("@") for o in parts[1:]]
        rules.append((lineno, pattern, owners))
    return rules
