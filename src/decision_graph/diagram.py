"""An answer as a picture: Mermaid and Graphviz DOT (issue #71).

A traversal is a graph, and past a couple of hops an indented list is the wrong shape for
one. An impact walk from `#5898` returns four paths that all leave the same node, and
nothing in the text output shows that they fan out from one place.

Two rules, and both are about not letting a picture claim more than the walk did:

1. **Only edges the engine actually traversed are drawn.** The credited implementer is
   named in the text output and is deliberately absent here, because a Why-walk stops at
   the Decision and never crosses `implemented_by` — drawing it would put a hop in the
   picture that the engine did not make. A diagram is read as the whole story, so it must
   contain nothing but the story.
2. **The evidence tier survives the translation.** Solid for explicit, thick for
   corroborated, dashed for inferred, and the tier is written on the edge label as well as
   drawn, because line weight is a hint and the word is the claim. A picture that renders a
   guess and a record identically undoes §5.3 and §5.4 at the last step.

Output is raw, with no code fence, so it pipes straight into a `.mmd` or `.dot` file.
"""

from __future__ import annotations

from . import render, trace
from .reasoning import Answer

# Mermaid node shapes, chosen so the artifact kinds are distinguishable at a glance rather
# than decoratively: a Decision is the thing this system asserts and gets the shape nothing
# else uses.
_MERMAID_SHAPE = {
    "decision": ("{{", "}}"),  # hexagon
    "issue": ("([", "])"),  # stadium
    "pull_request": ("[", "]"),  # rectangle
    "commit": ("((", "))"),  # circle
    "release": ("[/", "/]"),  # parallelogram
    "person": ("(", ")"),
}

_MERMAID_LINK = {
    "explicit": "-->",
    "corroborated": "==>",  # thick
    "inferred": "-.->",  # dashed
}

_DOT_STYLE = {
    "explicit": 'style=solid, penwidth=1.2',
    "corroborated": 'style=solid, penwidth=2.6',
    "inferred": 'style=dashed, penwidth=1.0',
}


def _escape_mermaid(text: str) -> str:
    """Make a real GitHub title safe inside a Mermaid label.

    Every label this module emits is quoted, and Mermaid's quoted form exists precisely so
    that brackets, parentheses and `#` can appear literally -- escaping them anyway turns
    `redirect defaults to 303 (#5898)` into a wall of `&#40;&#35;` that renders correctly
    and reads terribly. The one character that cannot survive is the quote itself, which
    ends the label early; Mermaid's own escape for it is `#quot;`.

    A newline would end the statement, so it becomes a space. Mermaid signals a broken
    label by rendering nothing rather than by reporting an error, which is why this is
    conservative about the two characters that matter and relaxed about the rest.
    """
    return text.replace('"', "#quot;").replace("\n", " ").replace("\r", " ").strip()


def _escape_dot(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _walked(answer: Answer):
    """Every (src, dst, step) the engine traversed, deduplicated, in a stable order.

    Paths overlap -- four impact paths from one node share that node and often a prefix --
    so drawing per path would emit the same edge repeatedly and, in Mermaid, draw it
    repeatedly too.
    """
    nodes: dict[int, tuple] = {}
    edges: dict[int, tuple] = {}
    for path in sorted(answer.paths, key=lambda p: (p.depth, p.target_id)):
        for node_id in path.node_ids:
            ref = path.ref(node_id)
            nodes.setdefault(node_id, (ref.node_type, ref.title, ref.external_id, ref.url))
        for step in path.steps:
            edges.setdefault(step.edge_id, (step.from_node_id, step.to_node_id, step))
    return nodes, edges


def _title(node_type: str | None, external_id: str | None, title: str | None) -> tuple[str, str]:
    head = trace.ref(node_type, external_id)
    return head, render._clip(title, 48)


def to_mermaid(answer: Answer) -> str:
    nodes, edges = _walked(answer)
    if not nodes:
        return ""

    # Force the base (light) theme so the diagram is readable regardless of which theme
    # mermaid.live or GitHub happens to default to. The dark theme turns every non-decision
    # node into a dark gray box with white text on a dark background -- invisible on a dark
    # page and low-contrast on a light one. `base` gives a white canvas and lets classDef
    # control every fill, so the colors here are exactly what the reader sees.
    lines = [
        "%%{init: {"
        "'theme': 'base',"
        " 'themeVariables': {"
        "'primaryColor': '#ffffff',"
        " 'primaryTextColor': '#1a1a1a',"
        " 'primaryBorderColor': '#888888',"
        " 'lineColor': '#555555',"
        " 'edgeLabelBackground': '#f0f0f0',"
        " 'tertiaryColor': '#f9f9f9'"
        "}}}%%",
        "graph LR",
    ]

    for node_id, (node_type, title, external_id, _url) in nodes.items():
        open_, close = _MERMAID_SHAPE.get(node_type or "", ("[", "]"))
        head, short = _title(node_type, external_id, title)
        text = _escape_mermaid(head)
        if short:
            text += "<br/>" + _escape_mermaid(short)
        lines.append(f'  n{node_id}{open_}"{text}"{close}')

    for _edge_id, (src, dst, step) in edges.items():
        arrow = _MERMAID_LINK.get(step.evidence_tier, "-->")
        verb = render.phrase(step.edge_type, True)
        label = _escape_mermaid(f"{verb} · {step.evidence_tier}")
        lines.append(f'  n{src} {arrow}|"{label}"| n{dst}')

    # Per-type class definitions. Colors are chosen for contrast on white and for
    # distinctiveness from each other: decision=amber, issue=blue, PR=green,
    # commit=indigo, release=teal. All have dark text (#1a1a1a) so readability does not
    # depend on the reader knowing which tier maps to which shade.
    lines.append("  classDef decision  fill:#fde68a,stroke:#b45309,stroke-width:2px,color:#1a1a1a;")
    lines.append("  classDef issue      fill:#dbeafe,stroke:#1d4ed8,stroke-width:1.5px,color:#1e3a5f;")
    lines.append("  classDef pr         fill:#dcfce7,stroke:#15803d,stroke-width:1.5px,color:#14532d;")
    lines.append("  classDef commit     fill:#ede9fe,stroke:#6d28d9,stroke-width:1.5px,color:#3b0764;")
    lines.append("  classDef release    fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a;")
    lines.append("  classDef person     fill:#fef9c3,stroke:#a16207,stroke-width:1px,color:#713f12;")
    lines.append("  classDef other      fill:#f3f4f6,stroke:#6b7280,stroke-width:1px,color:#1a1a1a;")

    _TYPE_CLASS = {
        "decision": "decision",
        "issue": "issue",
        "pull_request": "pr",
        "commit": "commit",
        "release": "release",
        "person": "person",
    }
    for node_id, (node_type, _, _, _) in nodes.items():
        cls = _TYPE_CLASS.get(node_type or "", "other")
        lines.append(f"  class n{node_id} {cls};")

    return "\n".join(lines)



def to_dot(answer: Answer) -> str:
    nodes, edges = _walked(answer)
    if not nodes:
        return ""

    lines = ["digraph answer {", "  rankdir=LR;", '  node [shape=box, fontname="Helvetica"];']
    for node_id, (node_type, title, external_id, url) in nodes.items():
        head, short = _title(node_type, external_id, title)
        # `\\n` is DOT's line break inside a quoted label, and it is appended AFTER
        # escaping: _escape_dot doubles backslashes, so building the label first would emit
        # a literal backslash-n and print it as text.
        label = _escape_dot(head)
        if short:
            label += "\\n" + _escape_dot(short)
        shape = "hexagon" if node_type == "decision" else "box"
        # DOT carries the URL as an attribute, so an SVG rendered from this is clickable --
        # the same "check it on GitHub" affordance the text output has.
        href = f', URL="{_escape_dot(url)}"' if url else ""
        lines.append(f'  n{node_id} [label="{label}", shape={shape}{href}];')

    for _edge_id, (src, dst, step) in edges.items():
        verb = render.phrase(step.edge_type, True)  # graph direction; see to_mermaid
        label = _escape_dot(f"{verb} ({step.evidence_tier})")
        style = _DOT_STYLE.get(step.evidence_tier, "style=solid")
        lines.append(f'  n{src} -> n{dst} [label="{label}", {style}];')

    lines.append("}")
    return "\n".join(lines)


FORMATS = {"mermaid": to_mermaid, "dot": to_dot}


def emit(answer: Answer, fmt: str) -> str:
    return FORMATS[fmt](answer)
