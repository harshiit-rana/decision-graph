# Agent study: protocol, tasks and metrics, registered before any run (#140)

**Registered 2026-10-20.** No agent has run on these tasks, and no agent has been given the
TraceMind tools (they do not exist yet: #139). The commit that adds this file is the
registration. **Everything above the Annotations heading at the end is never edited.** A correction is an
annotation in a later commit, so a decision can't be quietly changed after the results are known.
`tests/test_agent_study_registration.py` pins the hash of this text, of `tasks.json`, of
`draw.json` and of `pool.json`.

## The question, and what would answer it

ADR 0006's hypothesis H1: a coding agent about to act on a change makes fewer wrong
assumptions if it is given the recorded history of the project (decisions, prior attempts,
competing changes) with the evidence tier and the refusals shown.

The study compares one agent under two conditions on the same tasks. It can support one of
three conclusions, fixed here:

1. **History helps.** Condition B has a lower rate of wrong assumptions than A, by the rule in
   "Analysis" below. ADR 0006 keeps the MCP surface.
2. **No detectable effect.** The rule is not met. ADR 0006 says to stop at the JSON contract and
   not maintain an MCP surface for no measured benefit. **This result will be published.**
3. **Harm.** B is worse than A by the same rule. Published, and treated as a bug report on the
   context packs before anything else.

It cannot show that TraceMind is better than any other way of giving an agent history, because
there is no condition C (see "Conditions").

## Tasks

### What a task is

A *precedent task*: a pull request, as it was filed, that a maintainer then closed by pointing
at an earlier artifact in the same repository. The history that decides how the proposal should
be handled is in that earlier artifact and not in the proposal's text. The agent is asked to
plan the handling; the ground truth is what the recorded history says.

### How they were selected

By `select_tasks.py`, from `pool.json`, a committed dump of what GitHub held on 2026-10-20 for
`pallets/flask` and `urllib3/urllib3`. The rule is in that file's docstring and is applied by
code. In short, an issue or pull request is eligible when it was created in a fixed window,
GitHub reports it closed, a maintainer comment (OWNER, MEMBER or COLLABORATOR) links an earlier
artifact of the same repository, that artifact was created inside the window and at least three
days before the first comment that links it, and not every such artifact had already been merged
before that comment (a merged change is in the checkout, so an agent with repository access alone
can read it and the task would not test recorded history). Items a maintainer marks as spam or
machine-generated are removed by a fixed list of marker phrases.

Items that share a precedent are one cluster, and the task is the cluster's earliest item. The
unit is the history, not the later pull request: four pull requests re-proposing the same
`allowed_methods` change are one task.

**The census is taken whole. There is no random draw.** The rule found few enough items that
sampling would only add variation.

### What happened to the rule, disclosed

- I read a list of 42 flask issues closed as "not planned" (titles only) before writing the
  rule. Most were spam or machine-generated reports with no maintainer reply linking history.
- The first version of the rule took only issues closed as not planned or duplicate. It found
  **2 eligible items across both repositories**. I widened it to pull requests and to any
  closure reason **before writing any ground truth**, and then added the decision-point and
  merged-before rules, again before ground truth. The numbers at each step are not recorded
  beyond this paragraph; the final rule and its output are what is registered.
- The final census has **11 clusters** (2 flask, 9 urllib3). A human check then removed two:
  - `urllib3#5078`: its only "precedent" is `#5077`, the issue it refers to; the real
    precedent (`#5102`) has a higher number than `#5078` and the rule only looks at lower ones;
  - `urllib3#5109`: its only "precedent" is `#5107`, the issue it claims to fix, not a prior
    decision.
  The check applied one criterion, fixed before reading either: *the precedent's content, not the
  proposal's text, determines the correct handling.* It was applied by the author of this protocol, who also wrote
  the ground truth, and was not reviewed by a second person (#143).
- **That leaves 9 tasks. The issue asked for at least 12.** I did not make up the difference
  with hand-picked tasks, because that would be the one place selection could favour the
  tool. See "Limitations" for what 9 allows.

### The nine

| id | repo | item | kind | the precedent it depends on |
|---|---|---|---|---|
| A1 | flask | #5923 | declined before | #5912, #5914 (same change, closed not planned nine days earlier) |
| A2 | flask | #6101 | already in progress | #6095 (open at the time) |
| A3 | urllib3 | #3759 | already in progress | #3720 (open) |
| A4 | urllib3 | #3771 | prior attempt | #3726 (open), #3636 |
| A5 | urllib3 | #4992 | prior attempt | #3729 (open) |
| A6 | urllib3 | #5049 | compatibility constraint | #5044 (maintainer comment on `False`) |
| A7 | urllib3 | #5088 | already in progress | #5071 (open) |
| A8 | urllib3 | #5108 | already in progress | #5102 (open) |
| A9 | urllib3 | #5121 | compatibility constraint | #5103 (open), #3777 |

**This is narrower than the two examples in the issue.** Five of nine are "someone is already
doing this". One (A1) is a re-proposal of something closed; two (A6, A9) record a compatibility
constraint in a discussion. None records a design rationale. **A result here says nothing about
whether history helps an agent understand why code is the way it is.** That question needs
tasks that need file-level history (ADR 0005, #148) and is not tested.

### Where tasks come from, and the graph

Condition B queries a graph that must hold only what existed at the task's `as_of` time (the
second before the maintainer comment that links the precedent). Two consequences, registered now:

- The flask graph exists (a 12-month window ending 2026-08-14, so its floor is about 2025-08-19).
  The urllib3 graph does not yet (#132). **No run happens until it does.**
- `--as-of` means "first observed by T" (#124) and the error runs toward refusal. A task whose
  precedent the graph holds but dates after `as_of` will look like a refusal. That counts as the
  tool's result and is not corrected.

## What the agent sees

The **prompt** is fixed. `{...}` is filled from `tasks.json`; nothing else is added:

> You are helping maintain the repository `{repo}`. This change was proposed on `{filed_at}`,
> exactly as filed:
>
> Title: `{item.title}`
>
> `{item.body}`
>
> Write a short plan for how the project should handle this proposal: what you would check
> first, what you would do (merge it, ask for changes, close it, or something else), and the
> claims about the project's history that your plan relies on. Say what you could not determine.

**The same model, the same prompt, the same tool-call budget (40) and the same wall-clock limit
(15 minutes) in every run.** The model identifier is recorded in the harness (#141) before the
first run and not changed. Sampling settings are the model's defaults and recorded.

### Conditions

- **A.** The repository checked out at the last commit of the default branch before `as_of`,
  with git history up to that commit, read and search tools, and no network.
- **B.** A, plus the TraceMind tools (#139) over a graph queried as of `as_of`.
- **No condition C.** The issue floats "plus raw GitHub search" to separate "history helps" from
  "TraceMind helps". I leave it out because a search that cannot be bounded at `as_of` hands the
  agent the answer (the maintainer's closing comment is on GitHub). It can be added as a
  separate registered batch if someone supplies a time-bounded GitHub snapshot.

### Leakage, and what voids a run

The proposal's closing comment, and anything after `as_of`, must not reach the agent. Checked
mechanically after each run, by scanning the transcript for GitHub URLs and `#N` references and
resolving each to its creation time with the committed `pool.json` (where present) or the API:

- any artifact **created after `as_of`**, or any quotation from a comment posted after it,
  appearing in a tool result makes the run **leaked**. Leaked runs are scored separately and
  reported by condition, with the count. They are not silently dropped and not silently kept.
- Task `ground_truth.history[].state_at_as_of` lists what each artifact looked like then. A tool
  result that shows a later state (for example "merged" for #6095 in A2) is a leak of that kind.

## Runs

**3 repeats per task per condition: 9 x 2 x 3 = 54 runs.** Why 3: model output varies, and a
task is then scored as a rate (0, 1/3, 2/3, 1) instead of one draw. Three is the least that
distinguishes "sometimes" from "always"; more is better and is limited by the cost below.

Run order is a seeded shuffle of all 54 (`random.Random(20261020)`), so condition and task are
not confounded with time of day or model-version drift. A run that fails for an infrastructure
reason (timeout of the harness, crash) is re-run with the identical inputs and the failure is
logged. **A run is never re-run because of its answer.** No prompt, condition or budget is
changed after the first run. A pilot of the harness may use a task that is **not** among these
nine, and its results are discarded.

### Cost

I do not know the cost, and will not guess one. It is runs (54) times tokens per run times the
model's price on the day of the run. Tokens per run come from the pilot above. **No run starts
until the project owner has approved a written estimate computed from the then-current price
list**, because this is a recurring cost.

## Metrics, fixed now

Scored per run, by a person, against `tasks.json` ground truth.

| id | metric | scored |
|---|---|---|
| M1 | **Wrong assumption.** The plan asserts a claim in the task's `contradicted_claims`, or any other claim about the project's history that GitHub text refutes. The scorer opens the cited artifact to check and records the claim and the URL. | every run |
| M2 | **Cites the prior artifact.** The plan names every `must_cite` artifact, with its role (not merely listed). | every run |
| M3 | **States uncertainty where history is silent.** The plan does not assert any fact in `history_silent_on`, and says it could not determine it. | only A1, A5, A6 (the tasks with a non-empty list) |
| M4 | **Fabricated history.** The plan cites an artifact that does not exist, or misdescribes one that does. Informational. | every run |

**M1 is the primary metric.** M2 to M4 are reported and never combined into M1 or into a score.

**Scoring rubric for M1.** A claim is "contradicted" only if the recorded history up to `as_of`
contradicts it. A claim that is merely unverified is not M1; it is a missing citation (M2). A plan
that says "I could not determine whether anyone is already working on this" is not wrong.
A plan that closes or merges is not scored on that choice, only on its claims. **The scorer does
not score on whether the plan matched what the maintainers did.** The maintainers may have been
wrong.

### Blinding

The scorer receives, for each run, **only the final plan text**, in a shuffled order, with the
condition, run number and tool-call log removed. The scorer is not told which tasks are
expected to differ. After scoring, the scorer guesses the condition of each plan from the text
and the guesses are recorded; if they are right far more often than half, the blinding is
reported as having failed. **Plans in B tend to cite artifacts by number, which can reveal the
condition**; the study treats that as a threat, not a solved problem.

The ground truth was written by the same side that built TraceMind. A second scorer who did
not is preferred (#143). If none is found, the scorer is the project author and this is stated
on every number in the result.

## Analysis, and what counts as no effect

For each task: the number of M1 failures among its 3 runs, per condition. A task is **won by B** if
B has fewer M1 failures than A, **won by A** if more, tied otherwise.

- **Test.** Exact two-sided sign test on the won tasks, ties dropped, at alpha = 0.05.
- **Size.** The difference in mean per-run M1 rate (A minus B) must also be at least **0.15**
  (absolute). A significant difference smaller than that is reported as no practical effect.
- **Significant means one of these won-task counts (B to A):** 6 to 0, 7 to 0, 8 to 0, 9 to 0 or 8 to 1. **With
  nine tasks, 7 to 1, 6 to 1, 5 to 0 and anything closer are not significant.** That is how
  little this study can detect.

**The null result is registered as: the sign test is not significant, or the difference in mean
per-run M1 rate is under 0.15.** It is
published in `eval/agent_study/RESULTS.md` with the full per-task table, the leaked-run counts,
M2 to M4, and the scorer's identity, whatever the numbers are.

## Limitations, stated so they can't be discovered later

- **Nine tasks, two repositories, one model, one kind of history.** A large effect is the only
  thing the sign test can find. "No detectable effect" is not "no effect".
- **The ground truth is mine.** It rests on GitHub text I read. A reader can open every URL;
  a second reader has not.
- **The tasks come from what maintainers closed.** They are cases where a maintainer *did*
  point at history. Proposals where history mattered and nobody said so are not in the set.
- **The graph is built after the fact** and dated by first observation (#124). Condition B's
  result depends on that, in the direction of fewer answers.
- **Tasks are public repositories only.** Text from them goes to whichever model service the
  harness uses. That is the data disclosure of `dg ask`, and the owner approves it with the cost.
- Several maintainer comments cite "AI" contributions. The tasks include such proposals
  because the rule did not filter on it; agents may behave differently on them.

## Dependencies

- #139 (the tools in condition B), #141 (the harness), #132 (the urllib3 graph).
- A later batch of tasks, from a later pool, is a new registration and is never merged into
  this set.

## Annotations

*(Anything learned after this commit goes below in later commits. Nothing above this line is
edited.)*
