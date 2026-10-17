# Security

TraceMind reads a GitHub repository into a local database and answers questions about it.
This page states what it trusts, what leaves your machine, what is exposed on the network,
and how to report a problem. Each statement names the code or configuration it describes.
They were checked against `main` on 2026-10-17. If one is wrong, that is a bug: report it.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** on this repository (Security → Report a
vulnerability). Please don't open a public issue for anything exploitable. Private
reporting has to be enabled by the repository owner in Settings → Code security; until it
is, contact the owner through their GitHub profile and ask for a private channel.

## Trust boundaries

**Everything written in the ingested repository is untrusted**, including titles, bodies,
comments, commit messages, release notes and branch names. Anyone who can open an issue
can write it. It is treated as data wherever it goes.

| Where repository text goes | What protects it | Code |
|---|---|---|
| Your terminal (`dg query`, `dg ask`, `--mode attempts`) | Escape sequences (ANSI/OSC/CSI), other control characters and bidirectional overrides are removed before printing, so text can't create hidden hyperlinks, clear the screen or retitle the window | `render.safe`, `tests/test_terminal_safety.py` |
| The HTML report (`dg report`) | Every value is HTML-escaped. Diagrams render with Mermaid `securityLevel: "strict"`. The Mermaid script is pinned to one version **and** to its SHA-384 (Subresource Integrity), so a compromised CDN can't substitute it | `report._esc`, `report.MERMAID_SRI` |
| The JSON contract (`--format json`, `dg context`) | Repository text appears only in the fields named by `repository_text_fields` (`title`, `excerpt`, `body`), never inside a system-written sentence. Each object carrying such a field also carries `author_association` and `untrusted` (true unless OWNER, MEMBER or COLLABORATOR, and true when unknown). `--quotes maintainers` removes the text of every untrusted object and counts what it removed. **The labels are advisory**: a client model can still follow injected text, including a maintainer's, and GitHub records no association for commits or releases, so those are always untrusted | `contract.py` (`label_authors`, `suppress_untrusted`), `tests/test_contract.py`, `tests/test_author_standing.py` |
| The model behind `dg ask` | Instructions sit alone in a system message. Repository text arrives as JSON values in fields such as `untrusted_artifact_text`, with control and bidi characters stripped. **This reduces prompt injection; it does not prevent it.** The model's output is printed after the deterministic evidence trail and is itself terminal-sanitised. Checking the prose against the trail is #125 | `explain.py`, `tests/test_ask_boundary.py` |
| The graph itself | Extraction is rule-based, and no model writes to the graph. Inferred edges pass a database gate (migration 0001). A stale or replayed payload can't overwrite newer state (#120) | `db.py`, `extractors.py` |

The adversarial bodies used by these tests are in `tests/fixtures/adversarial_bodies.json`.

## What leaves your machine

| Destination | When | What is sent | Code |
|---|---|---|---|
| `api.github.com` | `dg init`, `dg doctor`, `dg ingest`, `dg-webhook` | Authenticated read requests for the target repository | `github.py` (`API_ROOT`), `cli._github` |
| Nvidia NIM (`integrate.api.nvidia.com`) | **Only** `dg ask` | The fields in README "Asking in English": your question, the traversal (references, titles, edge types, tiers), Decision facts, and up to 1,500 characters of each artifact body on the paths. Set `DG_ASK_SEND_BODIES=0` to leave out bodies | `explain.SENT_FIELDS`, `explain.get_client` |
| `cdn.jsdelivr.net` | When **a browser opens** a saved report | A request for the Mermaid script; nothing from the graph | `report.MERMAID_CDN` |

Nothing else makes a network call. `dg query`, `dg context`, `dg report`, `dg status` and
`dg snapshot` read only your local database.

## Secrets

- **The GitHub token** lives in `.env` (git-ignored, `.gitignore` line 1), written by
  `dg init`. A public repository needs **no token scopes at all**: a classic token with
  nothing ticked, or a fine-grained read-only token (`.env.example`). `write_env` tries
  `chmod 600`. On a Windows bind mount that has no effect, so protect the project folder
  yourself.
- **The token is never logged.** It is sent only as an `Authorization` header set once on
  the HTTP client. `tests/test_secrets_not_logged.py` runs the success, retry and
  rate-limit paths with every logger at DEBUG and checks that no record contains the
  token, `Authorization` or `Bearer`.
- **Snapshots never contain secrets.** The database holds none, and `dg snapshot export`
  refuses if the configured token appears anywhere in the data (`snapshot.export`). A
  snapshot is still a full copy of the ingested repository's content, so treat one from a
  private repository as you would the repository.
- **The database password** is random, generated by `dg init` and stored in `.env` as
  `DG_DB_PASSWORD`. `dg doctor` fails while the public fallback `dg` is in use (#118).

## Network exposure

| Listener | Bound to | Notes | Code |
|---|---|---|---|
| PostgreSQL (published port) | `127.0.0.1:55434` by default | `DG_DB_BIND=0.0.0.0` exposes it deliberately. `dg` itself never uses this port; it talks to `db:5432` on the compose network. **The app still connects as the `postgres` superuser**: least-privilege roles are #172 | `docker-compose.yml` |
| `dg-webhook` | **`0.0.0.0:8000` by default** | Refuses to start without `GITHUB_WEBHOOK_SECRET`. Verifies the HMAC-SHA256 signature with `hmac.compare_digest` before parsing anything. Every bad signature gets the same 401. It is `http.server`: single-threaded, **no TLS**, and not a production server. Run it behind a TLS-terminating reverse proxy. Duplicate deliveries aren't yet de-duplicated (#164). Its all-interfaces default is tracked in #191 | `webhook.py` |

## Out of scope today

- **Per-user access control.** Anyone who can run `dg` or reach the database can read
  everything in it (ADR 0007, PRD §4).
- **Private repositories.** Ingesting one copies its content into the database and any
  snapshot. Treat them with the repository's own confidentiality.
- **Model behaviour.** `dg ask` output is a labelled paraphrase, not evidence. The evidence
  is the trail printed above it.
