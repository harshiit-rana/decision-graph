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

CLOSES_RE = re.compile(rf"\b(?:{CLOSING_KEYWORDS})\b\s*:?\s+#(\d+)", re.IGNORECASE)
ISSUE_REF_RE = re.compile(r"(?<![\w/])#(\d+)\b")

# Only full 40-character SHAs. Abbreviated SHAs are indistinguishable from ordinary
# hex strings in prose and produced false edges in early testing.
SHA_RE = re.compile(r"\b([0-9a-f]{40})\b")

# "Co-authored-by: Name <email>" — an explicit authorship signal git itself defines.
COAUTHOR_RE = re.compile(r"^Co-authored-by:\s*(.+?)\s*<(.+?)>\s*$", re.IGNORECASE | re.MULTILINE)


def closing_refs(text: str | None) -> set[int]:
    """Issue/PR numbers this text explicitly closes."""
    if not text:
        return set()
    return {int(m) for m in CLOSES_RE.findall(text)}


def mentioned_refs(text: str | None) -> set[int]:
    """Issue/PR numbers mentioned but not closed."""
    if not text:
        return set()
    return {int(m) for m in ISSUE_REF_RE.findall(text)} - closing_refs(text)


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
