"""Score an evaluation from committed human labels (issue #128).

On flask the verdicts were filled in by hand and summarised in prose. That worked for 18 queries on
one repository. It doesn't for two repositories, a random recall sample and a second adjudicator:
each new figure would be hand arithmetic that nothing re-checks, the failure #67 fixed for the
README's figures. So every figure here is computed, by one command, from label files, with an
interval and the n it rests on.

    python -m decision_graph.evaluation score eval/flask
    python -m decision_graph.evaluation score eval/urllib3 --markdown

**This module never assigns a verdict.** It reads labels written by a person and counts them. The
runner grades nothing (`verdict` is null in every results.json), and the scorer is the same: it has
no opinion about whether an answer is right.

A label file, `<dir>/labels/<judge>.json`:

    {"format": 1, "repo": "pallets/flask", "judge": "handle", "judge_role": "who they are to the
     system", "labels": [{"item": "F1", "verdict": "correct", "tier": "explicit", "basis":
     "checked_directly", "date": "2026-02-01", "evidence_url": "https://...", "note": "..."}]}

Verdicts:
- `correct`: the system answered, and a human checked the answer is right;
- `incorrect`: it answered, and the answer is wrong. `truth` says `wrong_answer` (the default) or
  `should_have_refused` (nothing in the repository supports any answer);
- `correct-refusal`: it returned nothing, and nothing was the right outcome;
- `missed`: it returned nothing, but the repository does answer it.

What the figures do not establish is printed with them. In particular they never say how a system
performs on queries nobody labelled, and a judge who also built the system is a disclosed
limitation, not a fixed one.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = 1
VERDICTS = ("correct", "incorrect", "correct-refusal", "missed")
TRUTHS = ("wrong_answer", "should_have_refused")
BASES = ("checked_directly", "by_construction", "control", "unspecified")
Z95 = 1.959963984540054  # the 97.5th percentile of the standard normal


class LabelError(ValueError):
    pass


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for a proportion. (0, 1) when n is 0: nothing is known.

    Chosen over the normal approximation because it behaves at the edges: 18 of 18 is a range
    (about 82% to 100%), not "100% ± 0", and 0 of n has an upper bound above zero.
    """
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def fmt(successes: int, n: int) -> str:
    """`18/18 = 100.0% (95% CI 82.4% to 100.0%)`, or `0/0 (no items)`."""
    if n == 0:
        return "0/0 (no items)"
    lo, hi = wilson(successes, n)
    return f"{successes}/{n} = {100 * successes / n:.1f}% (95% CI {100 * lo:.1f}% to {100 * hi:.1f}%)"


@dataclass(frozen=True)
class Label:
    item: str
    verdict: str
    judge: str
    tier: str | None = None
    truth: str = "wrong_answer"
    basis: str = "unspecified"
    date: str | None = None
    evidence_url: str | None = None
    note: str = ""
    contested: bool = False  # the judge's own record that this verdict is arguable

    @property
    def answered(self) -> bool:
        return self.verdict in ("correct", "incorrect")

    @property
    def right_outcome_was_refusal(self) -> bool:
        """Whether nothing was the right thing to return for this item."""
        return self.verdict == "correct-refusal" or (
            self.verdict == "incorrect" and self.truth == "should_have_refused"
        )

    @property
    def is_correct_outcome(self) -> bool:
        return self.verdict in ("correct", "correct-refusal")


@dataclass
class LabelSet:
    repo: str
    judge: str
    judge_role: str
    labels: list[Label]
    source: str = ""
    path: str = ""
    disclosures: list[str] = field(default_factory=list)


def load_labels(path: Path) -> LabelSet:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("format") != FORMAT:
        raise LabelError(f"{path}: label format {raw.get('format')!r}, expected {FORMAT}")
    judge = raw.get("judge")
    if not judge:
        raise LabelError(f"{path}: no judge named. An unattributed verdict can't be weighed")
    labels: list[Label] = []
    seen: set[str] = set()
    for i, r in enumerate(raw.get("labels", [])):
        where = f"{path} label {i}"
        if "verdict" not in r or r["verdict"] is None:
            raise LabelError(f"{where}: no verdict. The scorer reads human verdicts and never assigns one")
        if r["verdict"] not in VERDICTS:
            raise LabelError(f"{where}: verdict {r['verdict']!r} is not one of {VERDICTS}")
        if r.get("truth", "wrong_answer") not in TRUTHS:
            raise LabelError(f"{where}: truth {r['truth']!r} is not one of {TRUTHS}")
        if r.get("basis", "unspecified") not in BASES:
            raise LabelError(f"{where}: basis {r['basis']!r} is not one of {BASES}")
        item = r.get("item")
        if not item:
            raise LabelError(f"{where}: no item id")
        if item in seen:
            raise LabelError(f"{path}: item {item!r} is labelled twice by {judge}")
        seen.add(item)
        labels.append(Label(
            item=item, verdict=r["verdict"], judge=judge, tier=r.get("tier"),
            truth=r.get("truth", "wrong_answer"), basis=r.get("basis", "unspecified"),
            date=r.get("date"), evidence_url=r.get("evidence_url"), note=r.get("note", ""),
            contested=bool(r.get("contested", False)),
        ))
    return LabelSet(
        repo=raw.get("repo", ""), judge=judge, judge_role=raw.get("judge_role", ""),
        labels=labels, source=raw.get("source", ""), path=str(path),
        disclosures=list(raw.get("disclosures", [])),
    )


def load_dir(directory: Path) -> list[LabelSet]:
    files = sorted((directory / "labels").glob("*.json"))
    if not files:
        raise LabelError(f"no label files in {directory / 'labels'}")
    return [load_labels(f) for f in files]


# ---------------------------------------------------------------------------
# The figures
# ---------------------------------------------------------------------------


def counts(labels: list[Label]) -> dict[str, int]:
    out = {v: 0 for v in VERDICTS}
    for label in labels:
        out[label.verdict] += 1
    return out


def figures(labels: list[Label]) -> dict:
    """Every number the report prints, as data, so a test can check each one."""
    n = len(labels)
    c = counts(labels)
    answered = c["correct"] + c["incorrect"]
    refusals = c["correct-refusal"] + c["missed"]
    refusal_items = sum(1 for lb in labels if lb.right_outcome_was_refusal)
    should_refuse_failed = sum(1 for lb in labels if lb.verdict == "incorrect" and lb.truth == "should_have_refused")
    out = {
        "n": n,
        "counts": c,
        "overall": (c["correct"] + c["correct-refusal"], n),
        "precision_answered": (c["correct"], answered),
        "abstention_precision": (c["correct-refusal"], refusals),
        "abstention_recall": (c["correct-refusal"], c["correct-refusal"] + should_refuse_failed),
        # An engine that returned nothing for every item scores exactly the items whose right
        # outcome was nothing. It is printed beside the overall figure so a high ratio is read
        # against it, not alone.
        "always_refuse_baseline": (refusal_items, n),
        "if_contested_were_wrong": (
            sum(1 for lb in labels if lb.is_correct_outcome and not lb.contested), n),
        "contested": [lb.item for lb in labels if lb.contested],
        "by_tier": {},
        "by_basis": {},
    }
    tiers: dict[str, list[Label]] = {}
    for lb in labels:
        if lb.answered:
            tiers.setdefault(lb.tier or "unlabelled", []).append(lb)
    for tier, group in sorted(tiers.items()):
        out["by_tier"][tier] = (sum(1 for g in group if g.verdict == "correct"), len(group))
    bases: dict[str, list[Label]] = {}
    for lb in labels:
        bases.setdefault(lb.basis, []).append(lb)
    for basis, group in sorted(bases.items()):
        out["by_basis"][basis] = (sum(1 for g in group if g.is_correct_outcome), len(group))
    return out


def agreement(a: LabelSet, b: LabelSet) -> dict:
    """Raw agreement and the disagreements between two judges, on the items both labelled."""
    la, lb = {x.item: x for x in a.labels}, {x.item: x for x in b.labels}
    both = sorted(set(la) & set(lb))
    same = [i for i in both if la[i].verdict == lb[i].verdict]
    differ = [(i, la[i].verdict, lb[i].verdict, la[i].note, lb[i].note) for i in both if i not in same]
    kappa = None
    if both:
        po = len(same) / len(both)
        pe = sum(
            (sum(1 for i in both if la[i].verdict == v) / len(both))
            * (sum(1 for i in both if lb[i].verdict == v) / len(both))
            for v in VERDICTS
        )
        kappa = (po - pe) / (1 - pe) if pe < 1 else None
    return {
        "judges": (a.judge, b.judge), "both": len(both), "same": len(same), "disagreements": differ,
        "only_first": sorted(set(la) - set(lb)), "only_second": sorted(set(lb) - set(la)),
        "kappa": kappa,
    }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def render_markdown(sets: list[LabelSet], *, query_ids: list[str] | None = None) -> str:
    repo = sets[0].repo or "(unnamed)"
    lines = [
        f"# Scored from labels: {repo}",
        "",
        "*Generated by `python -m decision_graph.evaluation score`. Every number below is computed from the "
        "committed label files; none is typed. The scorer never assigns a verdict.*",
        "",
    ]
    for ls in sets:
        f = figures(ls.labels)
        c = f["counts"]
        lines += [
            f"## Judge: `{ls.judge}`",
            "",
            f"- Role: {ls.judge_role or 'not stated'}",
            f"- Labels: {f['n']} (`{ls.path.replace(chr(92), '/')}`)" + (f"; {ls.source}" if ls.source else ""),
        ]
        for d in ls.disclosures:
            lines.append(f"- **Disclosure:** {d}")
        if query_ids is not None:
            missing = sorted(set(query_ids) - {lb.item for lb in ls.labels})
            extra = sorted({lb.item for lb in ls.labels} - set(query_ids))
            lines.append(f"- Coverage of the query set ({len(query_ids)} queries): "
                         f"{len(query_ids) - len(missing)} labelled"
                         + (f"; **unlabelled: {', '.join(missing)}**" if missing else "")
                         + (f"; **labels for unknown queries: {', '.join(extra)}**" if extra else ""))
        lines += [
            "",
            "| figure | value | what it is |",
            "|---|---|---|",
            f"| **correct outcome, overall** | {fmt(*f['overall'])} | answered correctly, or correctly returned nothing |",
            f"| *always-refuse baseline* | {fmt(*f['always_refuse_baseline'])} | what an engine returning nothing for every item would score. Read the row above against it |",
            f"| precision on answered | {fmt(*f['precision_answered'])} | of the items the system answered, how many a human found correct |",
            f"| abstention precision | {fmt(*f['abstention_precision'])} | of the items it refused, how many were right to (the rest are `missed`) |",
            f"| abstention recall | {fmt(*f['abstention_recall'])} | of the items that should have been refused, how many were (needs `should_have_refused` labels) |",
            "",
            f"Verdicts: {', '.join(f'{v} {c[v]}' for v in VERDICTS)}.",
            "",
        ]
        if f["contested"]:
            lines += [
                f"**Contested by the judge's own note:** {', '.join(f['contested'])}. "
                f"Counted wrong, the overall figure would be {fmt(*f['if_contested_were_wrong'])}.",
                "",
            ]
            lines += [f"- `{lb.item}`: {lb.note}" for lb in ls.labels if lb.contested]
            lines.append("")
        if f["by_tier"]:
            lines += ["**Precision by the tiers the answer carried** (ADR 0002's calibration table):", "",
                      "| tier | correct / answered |", "|---|---|"]
            lines += [f"| {t} | {fmt(*v)} |" for t, v in f["by_tier"].items()]
            lines.append("")
        lines += ["**By how the verdict was reached:**", "", "| basis | correct outcome |", "|---|---|"]
        lines += [f"| {b.replace('_', ' ')} | {fmt(*v)} |" for b, v in f["by_basis"].items()]
        lines.append("")
        small = [k for k, (s, n_) in {**{"overall": f["overall"]}, **f["by_tier"]}.items() if 0 < n_ < 30]
        if small:
            lines += ["*Intervals are wide where n is small. A figure with n under 30 is a range, not a rate.*", ""]
    if len(sets) > 1:
        lines += ["## Agreement between judges", ""]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                ag = agreement(sets[i], sets[j])
                lines.append(f"**`{ag['judges'][0]}` and `{ag['judges'][1]}`:** raw agreement "
                             f"{fmt(ag['same'], ag['both'])} on the {ag['both']} items both labelled.")
                if ag["kappa"] is not None:
                    warn = " (**unstable: n under 30**)" if ag["both"] < 30 else ""
                    lines.append(f"Cohen's kappa {ag['kappa']:.2f}{warn}.")
                if ag["only_first"] or ag["only_second"]:
                    lines.append(f"Labelled by only one: {', '.join(ag['only_first'] + ag['only_second'])}.")
                if ag["disagreements"]:
                    lines += ["", "| item | first | second | notes |", "|---|---|---|---|"]
                    lines += [f"| {it} | {v1} | {v2} | {(n1 or '-')} / {(n2 or '-')} |"
                              for it, v1, v2, n1, n2 in ag["disagreements"]]
                lines.append("")
    lines += [
        "## What this does not establish", "",
        "- Anything about queries nobody labelled. The labelled set is the evaluation, not a sample of use.",
        "- That the judge was independent. Where the judge also built the system, that is the "
        "disclosure above, and an independent second judge (#143) is what would change it.",
        "- Recall. These figures measure what the system said; whether it found everything is the "
        "sampled recall audit (#133), which has its own labels.",
        "",
    ]
    return "\n".join(lines)


def check_against_results(sets: list[LabelSet], results_path: Path) -> list[str]:
    """Contradictions between a label and what the runner recorded the engine returning.

    A label saying `correct` for a query the engine returned nothing for can't both be true. The
    scorer doesn't decide which is wrong; it refuses to print a figure built on the pair.
    """
    results = {r["id"]: r for r in json.loads(results_path.read_text(encoding="utf-8"))["results"]}
    problems = []
    for ls in sets:
        for lb in ls.labels:
            r = results.get(lb.item)
            if r is None:
                problems.append(f"{ls.judge}: label for {lb.item}, which is not in {results_path.name}")
            elif lb.answered != bool(r["answered"]):
                problems.append(
                    f"{ls.judge}: {lb.item} is labelled {lb.verdict}, but the engine "
                    f"{'answered' if r['answered'] else 'returned nothing'} in {results_path.name}")
    return problems


def query_ids_of(directory: Path) -> list[str] | None:
    """The ids in the directory's query set, when it has one, to report coverage."""
    for name in ("query_set.json",):
        p = directory / name
        if p.exists():
            return [q["id"] for q in json.loads(p.read_text(encoding="utf-8"))["queries"]]
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m decision_graph.evaluation score",
        description="Compute precision, abstention and per-tier figures from committed label files, "
        "with Wilson intervals. Reads labels only; never assigns a verdict.",
    )
    p.add_argument("directory", help="a directory holding labels/*.json (and optionally query_set.json)")
    p.add_argument("--query-set", help="query_set.json to check label coverage against")
    p.add_argument("--results", help="results.json to check the labels against (default: the one "
                   "beside the labels directory, if any)")
    p.add_argument("--output", help="write the Markdown here instead of stdout")
    args = p.parse_args(argv)
    directory = Path(args.directory)
    try:
        sets = load_dir(directory)
        qs = Path(args.query_set) if args.query_set else None
        ids = ([q["id"] for q in json.loads(qs.read_text(encoding="utf-8"))["queries"]]
               if qs else query_ids_of(directory))
        res = Path(args.results) if args.results else directory / "results.json"
        if res.exists():
            problems = check_against_results(sets, res)
            if problems:
                raise LabelError("labels contradict the recorded results: " + "; ".join(problems))
        text = render_markdown(sets, query_ids=ids)
    except (LabelError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
        print(text)
    return 0
