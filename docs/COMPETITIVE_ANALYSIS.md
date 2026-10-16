# Competitive and open-source landscape

*Reviewed 2026-10-17. Two independent research passes read each project's own repository,
documentation, licence file or paper. I then re-checked the claims this document leans on
most against the primary source: Potpie's querying doc, Wisegit's README, the
CVE-2026-48529 listings, and the LaToza & Myers PDF. Nothing was installed or run.
Vendor performance figures are the vendors' own and were not reproduced. Every statement
below is either **[verified]**, with its source, or **[conclusion]**, which is our own
reading. Where a closed product's licence couldn't be read, it says so.*

## The short version

**[conclusion]** Almost everything in this space indexes one of three things:
- **code**: Sourcegraph, OpenGrok, CodeQL, Semgrep, Serena, Aider, GitNexus,
  code-graph-rag, Cursor;
- **raw history**: GitHub's MCP server, the reference `git` MCP server, Augment Context
  Lineage;
- **hand-written or agent-written notes**: CLAUDE.md, Copilot Memory, AGENTS.md, Letta,
  Mem0.

Very little reconstructs *why* from the repository's own record and grades how strongly
the record says it. Only two projects overlap meaningfully with TraceMind's core:

| | Potpie | Wisegit | TraceMind |
|---|---|---|---|
| Where decisions come from | **Recorded** going forward by agents or users (`potpie record --type decision`) | Inferred per function from commits and enrichment | **Reconstructed mechanically** from the last 12 months: issue + closing PR + reviews, by database-enforced rules |
| Evidence strength | 7 truth classes mapped to a ranking weight | One blended "freeze score" | Three tiers as a **structural constraint**: inferred can never pass as explicit |
| Refusal | Coverage statuses, plus "an empty or sparse result does not prove the source has no relevant fact" | "Theory gaps" (planned) | Refusal is an answer kind, naming how something ended and never why |
| Time | `as_of` hard filter; `occurred_at` / `observed_at` | Timeline | `--as-of`, though its semantics are still being fixed (#124, #181) |
| Licence | Apache-2.0 | MIT | MIT |
| Sources | [querying](https://github.com/potpie-ai/potpie/blob/main/docs/context-graph/querying.md), [ontology](https://github.com/potpie-ai/potpie/blob/main/docs/context-graph/ontology.md), [architecture](https://github.com/potpie-ai/potpie/blob/main/docs/context-graph/architecture.md) **[verified]** | [README](https://github.com/Sandip124/wisegit) **[verified]**; 1 star | this repo |

**[conclusion]** TraceMind's defensible difference has two parts:
1. **No cold start.** Potpie's decision store starts empty and fills only as agents record
   decisions. TraceMind rebuilds a year of history with no human effort.
2. **Rigour that can be checked.** Tiers are rules, not weights. Refusals are a measured
   output. The planned cross-repository evaluation (#131–#134) is the evidence.

Features alone aren't a moat. If Potpie adds mechanical issue-to-PR linking, the
remaining gap is rigour and published measurement.

Potpie's own docs say "Potpie returns ranked evidence, never a synthesized answer"
**[verified]**. TraceMind shares that stance: a model may paraphrase, labelled, and never
adds a fact.

## Where the evidence says users are

**[verified]** LaToza & Myers, *Hard-to-Answer Questions about Code* (PLATEAU 2010),
surveyed 179 Microsoft developers, who reported 371 hard questions. **Rationale** was the
most-reported category (42). Within it, "Why wasn't it done this other way?" (15) slightly
outnumbered "Why was it done this way?" (14). History (23) included "What else changed
when this code was changed?". These count *hard* questions, not frequency, at one company
in 2010.

**[conclusion]** The data supports three things:
- "why", and especially "why not the other way", as the core question;
- prior attempts (#137, shipped) and declined decisions (#157) as near-core;
- co-change (#151) as a History answer.

**[verified]** Research on agents and history:
- **Blame-derived history helps repair, by little for strong models.** HAFixAgent v4
  reports +4.4% on Defects4J and +1.1% on BugsInPy with its strongest model
  ([arXiv 2511.01047](https://arxiv.org/abs/2511.01047)).
- **Rationale is spread across artifacts.** Argus found "no single artifact type
  consistently captures all components", and LLM rationale identification reached 51.4%
  precision and 93.2% recall ([arXiv 2604.10345](https://arxiv.org/abs/2604.10345)).
- **Passing tests isn't the same as respecting design.** "Fewer than half of resolved
  issues are fully design-satisfying" ([arXiv 2604.05955](https://arxiv.org/abs/2604.05955)).
- **Temporal leakage is real.** SWE-bench issue #465 found agents running `git log --all`
  to find future fixes ([issue](https://github.com/SWE-bench/SWE-bench/issues/465)).

**[conclusion]** "History makes agents better" is a weak claim for frontier models.
"History stops agents repeating declined work and violating earlier design decisions" is
the stronger, testable one, and the agent study (#140–#142) should be designed around it.
Argus's 51.4% is direct evidence for keeping model-proposed links in a separate inferred
tier.

## By category

### Code intelligence and search

| Project | What it does, verified | TraceMind equivalent | Take / avoid | Licence |
|---|---|---|---|---|
| [Sourcegraph](https://sourcegraph.com) (+ MCP) | Code, commit and diff search; SCIP code graph; revision-pinned reads | None for why | Revision pinning matches `--as-of` thinking | Proprietary; the public snapshot is archived |
| [OpenGrok](https://github.com/oracle/opengrok) | Code + SCM history browsing, annotate | Partial (history) | Nothing new | CDDL-1.0 |
| [CodeQL](https://github.com/github/codeql) | Code as a queryable database | None | **Avoid** depending on the CLI: its [licence](https://github.com/github/codeql-cli-binaries/blob/main/LICENSE.md) restricts use on private code | Queries MIT; CLI restricted |
| [Semgrep](https://github.com/semgrep/semgrep) | Pattern matching on code | None | Its MCP server was folded into the main package, which is a lesson for keeping MCP inside `dg` (#139) | LGPL-2.1 engine |
| [GitNexus](https://github.com/abhigyanpatwari/GitNexus) | Code graph + diff → affected areas, with confidence scores | Partial (impact) | **Avoid reusing code** | PolyForm Noncommercial 1.0.0 |
| [Serena](https://github.com/oraios/serena) | LSP-backed symbol tools for agents | None | Don't vendor | GPL-3.0-or-later |
| [Aider repo map](https://aider.chat/docs/repomap.html) | tree-sitter symbol map within a token budget (`--map-tokens`) | None | Token-budgeted context packs (#138, #178) | Apache-2.0 |
| [Continue](https://github.com/continuedev/continue) | IDE assistant | n/a | No longer maintained, so not an integration target | Apache-2.0 |
| SCIP / LSIF / tree-sitter / stack-graphs | Code-intelligence standards | None | tree-sitter only if symbols are ever needed (ADR 0005 defers them); stack-graphs is archived | various |

### Agent context, MCP and memory

| Project | What it does, verified | Lesson for TraceMind |
|---|---|---|
| [GitHub MCP server](https://github.com/github/github-mcp-server) (MIT) | Raw issue, PR and commit tools; `--read-only`; "lockdown mode" filters public content to push-access authors and is "best-effort", "not an authorization boundary" | Quoted text needs author standing (#177); read-only must win (#139) |
| CVE-2026-48529 / GHSA-pjp5-fpmr-3349 | GitHub MCP server in HTTP mode reused the first user's GraphQL client for lockdown checks; fixed in 1.1.2 ([OSV](https://osv.dev/vulnerability/CVE-2026-48529)) | No process-wide caches keyed on one credential (#139) |
| [Reference `git` MCP server](https://github.com/modelcontextprotocol/servers/tree/main/src/git) (MIT) | 12 tools (log, show, diff…); **no blame** | Don't duplicate raw git; resolve blame to a *Decision* (#179) |
| [CodeGraph](https://github.com/colbymchenry/codegraph) (MIT) | One default MCP tool; others opt-in | One default tool (#139) |
| [MCP spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | `outputSchema` + `structuredContent`; servers MUST "Sanitize tool outputs" | A refusal is a typed successful result, not `isError` (#139) |
| [AGENTS.md](https://agents.md/) | Instruction-file format adopted by Codex, Cursor, Copilot agent, Aider, Devin and others | Print a snippet telling agents when to call `dg` (#185) |
| [Copilot Memory](https://docs.github.com/en/copilot/concepts/agents/copilot-memory) | Agent-written facts, citations validated against the current branch before use | Flag evidence that changed since ingestion (#180) |
| Augment Context Lineage, Cursor PR indexing | LLM summaries of commits and PRs fed to search | **[conclusion]** a summary is not a record; no refusal path documented |
| [Graphiti](https://github.com/getzep/graphiti) (Apache-2.0) | Temporal KG: facts invalidated, not deleted; bi-temporal | Reference design for #181 (ADR 0009); not a dependency |
| Mem0, Letta, Unblocked | Resolve contradictions (delete / last-write-wins / "recency, authority, proximity") | **Do the opposite**: return conflicts, never a winner (#175) |

### Decisions and engineering knowledge

| Project | What it does, verified | Take / avoid |
|---|---|---|
| [MADR](https://adr.github.io/madr/) | ADR template; status includes `rejected` and `superseded by` | Export Decisions as MADR with "not evidenced" fields (#188) |
| [adr-tools](https://github.com/npryce/adr-tools) | Shell ADR tooling with supersession links | Parse the format; **don't vendor** (GPL-3.0) |
| [log4brains](https://github.com/thomvaill/log4brains), [Structurizr](https://docs.structurizr.com/dsl/adrs) | Publish or import ADR collections | Ingest declared ADRs only if prevalence justifies it (#187) |
| Backstage, Swimm, Glean, DeepWiki / Code Wiki | Ownership catalogues, doc-to-code coupling, enterprise search, generated wikis | **[conclusion]** describe *what*, cite sources, and document no refusal or temporal behaviour; measure that claim head-to-head (#184) |

### Change impact, risk and history

| Method or tool | What it does, verified | Use |
|---|---|---|
| ROSE ([Zimmermann et al., TSE 2005](https://thomas-zimmermann.com/publications/files/zimmermann-tse-2005.pdf)) | Mines co-change rules from prior history only; top-10 suggestions; ignores >30-entity transactions | Evaluation protocol for #151 |
| [code-maat](https://github.com/adamtornhill/code-maat) | Coupling defaults (min-revs 5, coupling 30%, max changeset 30) | Baseline for #151; GPL-3.0, reimplement |
| [CodeScene change coupling](https://codescene.io/docs/guides/technical/change-coupling.html) | Couples modules sharing a ticket ID | Decision-scoped co-change (#151 comment) |
| SZZ (Śliwerski et al., MSR 2005) + [Rosa et al., ICSE 2021](https://arxiv.org/abs/2102.03300) | Bug-inducing commits; an oracle from fixes that cite the culprit | Explicit regression links (#182), then SZZ as inferred, measured against them (#183) |
| [PyDriller](https://pydriller.readthedocs.io/) | Git mining, `get_commits_last_modified_lines` | Usable (Apache-2.0); pyszz is GPL-3.0 |
| [gitStream](https://docs.gitstream.cm/filter-functions/) `codeExperts` | Blame + recency expertise | A separate "authored surviving lines" signal, never blended (#189) |
| [git-of-theseus](https://github.com/erikbern/git-of-theseus) | Line survival curves | A drift measure (#158 comment) |
| GitHub Events API ([changelog](https://github.blog/changelog/2025-08-08-upcoming-changes-to-github-events-api-payloads/)) | Since 2025-10-07, event payloads drop push commit lists and `author_association` | Keep STRUCTURAL on REST (#190); GH Archive can't replace REST ingestion |

### Evaluation, provenance and abstention

| Source | Use |
|---|---|
| [Wen et al.](https://arxiv.org/pdf/2407.18418), abstention survey; [Kamath et al., ACL 2020](https://arxiv.org/abs/2006.09462) | Abstention recall, Effective Reliability, a tier-induced risk-coverage curve, and shift across repositories (#128 comment) |
| [ALCE](https://arxiv.org/pdf/2305.14627), [RAGAS](https://arxiv.org/pdf/2309.15217) | Offline citation and faithfulness metrics for `dg ask`, after the deterministic check (#125 comment) |
| [W3C PROV-O](https://www.w3.org/TR/prov-o/), [SLSA provenance](https://slsa.dev/spec/v1.0/provenance) | A provenance block on every JSON answer (#176) |
| [Graphiti paper](https://arxiv.org/pdf/2501.13956), [Fowler, bitemporal history](https://martinfowler.com/articles/bitemporal-history.html) | Separate validity from retraction (#181, ADR 0009) |

## What TraceMind does that we found nowhere else

**[conclusion]**, each a claim the backlog must keep true:

1. **Tiers as a database constraint**, not a ranking weight (migration 0001's tag/tier
   invariant, `edge_inferred_gate`).
2. **Refusal that names the ending and declines the reason** (#86, #108), now also as typed
   JSON (#135) and as an attempts list that says what an empty result does *not* mean
   (#137).
3. **"Tried in #A, landed in #B"** read mechanically from thread membership (#137).
4. **Repository text confined to named fields** in the agent contract, tested with a planted
   injection sentence (#135).

## What we decided not to copy

- **A blended score** (Wisegit's freeze score, numeric confidence). It hides which signal
  carried the verdict, which is what tiers exist to expose (ADR 0002).
- **Automatic contradiction resolution** (Mem0, Letta, Unblocked, Graphiti). It is the
  silent overwrite #120 is about.
- **Multi-source ingestion** (Slack, Jira, Confluence; as in Potpie, Glean, Unblocked).
  Off-repository chat has no closing-keyword equivalent, so nearly all of it could only be
  inferred. It stays out of scope (PRD §4).
- **LLM-written summaries as context** (Augment, generated wikis). The cardinal rule: a
  model never adds a decision, link or reason.
- **FROZEN/SAFE labels** on files. Absence of a record isn't safety (#178 says "absent").

## Licence watch-list (TraceMind is MIT)

| Don't vendor | Usable as a dependency or reference |
|---|---|
| GPL-3.0: adr-tools, code-maat, pyszz, Sourcetrail, Serena | Apache-2.0 / MIT / BSD: PyDriller, git-of-theseus, Graphiti, Potpie (reference only), Wisegit, gitStream, import-linter, py-tree-sitter |
| PolyForm Noncommercial: GitNexus | MPL-2.0, unmodified: Kaiaulu, XTDB |
| Restricted: CodeQL CLI on private code | |

The licence readings are from each project's LICENSE file. Their legal reading here is
ours, not legal advice.

## Issues this review produced

New:
- #174 coverage on every answer
- #175 conflicts, never resolved
- #176 provenance block
- #177 author standing on quoted text
- #178 pre-edit manifest
- #179 line-level why
- #180 changed-evidence flags
- #181 ADR 0009: retraction against validity, a decision for the owner
- #182 explicit regression links
- #183 SZZ (research)
- #184 head-to-head against a generated wiki
- #185 agent instruction snippet
- #186 unexplained-change report
- #187 declared architecture files
- #188 MADR export
- #189 authored-lines expertise
- #190 Events API hygiene

Existing issues updated with findings: #124, #125, #128, #133, #139, #151, #152, #157,
#158.

## Not verified in this review

- Closed products' licences and any feature not on a public page: Glean, Cursor,
  Augment, Unblocked, Greptile.
- Bhat et al. (ECSA 2017), behind a login, so bibliographic data only.
- Whether GitHub *webhook* payloads, as opposed to Events API payloads, were trimmed.
  #190 makes it irrelevant.
- Whether every artifact type TraceMind stores carries `author_association`. #177 checks
  it first.
