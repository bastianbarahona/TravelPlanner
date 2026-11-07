# CLAUDE.md — Travel Planning Platform

> Project-specific development harness. Committed as part of the deliverable.
> Scope: this file governs work in this repository only.

## Ground rules

1. **The contract is the truth.** `contracts/openAPI.json` is immutable. Before accepting any schema, model, client or test touching the provider, compare it **field by field** against the contract. If they disagree, the contract wins.
2. **Money is `Decimal`, never `float`.** Database `NUMERIC(12,2)`, Python `Decimal`, JSON string with exactly two decimals matching `^(0|[1-9][0-9]*)\.[0-9]{2}$`. Rounding is `ROUND_HALF_UP`, applied in one place: `app/money.py`.
3. **`revision` is a string**, not an integer. So is every provider identifier.
4. **`unknown` is our state, not the provider's.** Provider outcomes are only `pending | confirmed | failed`. A timeout or 5xx is never proof of rejection.
5. **No feature ships without a test that can fail.** A test that only asserts a canned mock response is not evidence.

## Architecture decisions (already made — do not relitigate)

| Decision | Rejected alternative | Why |
|---|---|---|
| Modular monolith | Microservices | The PRD's guarantees (concurrent edits, booking idempotency, document/reference consistency) are transactional. One Postgres + optimistic locking solves them directly; sagas and outbox would add failure surface for no scale benefit. |
| Optimistic locking (`version` column) | Pessimistic locks / CRDT | Low contention, simple to explain, returns a usable 409 with current state. |
| Offer stored as JSONB snapshot | Local catalog tables | The app must consume the provider through its public contract, not mirror its database. |
| Polling + state endpoint | WebSocket-only push | The PRD requires recovery without assuming every notification arrived. A readable state endpoint is the source of truth. |
| `Decimal` | float | Reproducible totals (US-05). |

## Module layout

```
app/
  api/          FastAPI routers — thin, no business logic
  domain/       entities, rules, invariants
  services/     use cases (trips, search, booking, documents)
  providers/    HTTP client for the external provider (contract-bound)
  mcp/          read-only MCP server and tools
  assistant/    LLM orchestration (swappable, deterministic mode for tests)
  db/           models, migrations, repositories
  money.py      the only place rounding happens
  config.py     environment only; no secrets in code
provider-mock/  separate service implementing openAPI.json
web/            React + TS, minimal
tests/
contracts/openAPI.json   ← never modified
```

## Definition of done (per change)

1. Code written and **read line by line** before committing.
2. At least one test covering a **failure path**, not just the happy path.
3. `docker compose up` still works from clean.
4. If it changes a guarantee, `ARCHITECTURE.md` is updated in the same commit.
5. Commit message states what changed and why in one line.

## CI and pull requests

1. **Every pull request is born with Auto-fix on.** The session that opens it turns it on right away, as part of opening it.
2. **Every CI failure is alerted to the owner and logged** in `docs/STATUS.md`, Section 2.7, with no exceptions.
3. **Every fix to a CI failure needs the owner's review and validation,** even when it is automatic, trivial or for a flaky failure. A fix may be pushed to the pull request so CI can check it, but it stays a proposal until the owner validates it. Auto-merge stays off.

## Ponytail (YAGNI)

Ponytail decides how little to build: stop at the first rung that holds, from "does this need to exist?" to the minimum code. Claude loads it as a plugin through `.claude/settings.json`, and Codex reads it from `AGENTS.md`, which holds the full ladder if the plugin is not installed.

1. **It shortens solutions, never guarantees.** The contract, the ground rules and the definition of done win.
2. **Tests:** its "one check" yields to rule 5 and to the tests each iteration requires, because those are explicit requests.
3. **Layout:** its "fewest files" applies inside the module layout above, not instead of it.
4. **Shortcuts:** a deliberate simplification carries a `ponytail:` comment naming its ceiling. Before closing an iteration, list them with `/ponytail-debt`, and record the ones that stay under "Limits I accept" in `ARCHITECTURE.md`.

## Scope guardrails — do NOT build

- Currency conversion, payments, cancellations, refunds
- Real identity provider (test identities only)
- Maps, drag-and-drop, live presence, real-time collaborative editing
- Pagination, advanced filters, caching layers, message brokers
- Any abstraction with a single implementation
- Visual polish

If something feels like it needs a new layer, write the reason in `ARCHITECTURE.md` under "Limits I accept" instead of building it.

## AI usage protocol

- Use assistants freely for scaffolding, tests and docs.
- **Never accept provider-facing code without the contract open beside it.**
- Log every case where the assistant was wrong in `AI-USAGE.md`: what it got wrong, how it was detected, what was changed.
- Nothing is committed that cannot be explained line by line in a review.

## Documentation targets

- `README.md` — setup, run, tests, evaluations, proposed deployment
- `ARCHITECTURE.md` — decisions, alternatives, guarantees, limits, risks, scaling
- `AI-USAGE.md` — how AI was used and verified
- `DEMO.md` — main workflow + failure and concurrency scenarios

All repository documentation is written in **English**.

## Multi-model assistant protocol (PAL)

Development tooling only: none of it enters `app/` or its dependencies. Subagents live in `.claude/agents/`, and the evidence for this section is in `AI-USAGE.md`. Gemini is unavailable, and nothing may depend on it.

| Role | Who | How |
|---|---|---|
| Director | Claude Code, Opus, high effort | The main session or `director-proyecto`. Plans and decides, and never implements. |
| Dev | Claude Sonnet, high effort, and Codex | `dev-principal`. Codex writes with the Dev command below. |
| QA | Claude Sonnet, high effort, and Codex | `qa-lead`. Codex reviews with the read-only command below. |

Whoever wrote a change does not review it: Claude reviews Codex's work, and Codex reviews Claude's.

```bash
codex exec -C "<repo>/<dir>" --add-dir "<repo>/<dir2>" -s workspace-write "<prompt>" < /dev/null   # Dev
codex exec -C "<repo>" -s read-only "<prompt>" < /dev/null         # QA, or a second opinion for the Director
```

- **Sandbox:** tested. `workspace-write` reaches only `-C` and each `--add-dir`, and `read-only` denies every write. Always pass `-s`: with no flag, Codex wrote. `<repo>` is this repository's real absolute path, which may be a worktree.
- ⚠ **Never use `clink` on this repository.** Every role, `pal_qa` included, can write across `C:/DEV/TravelPlanner`, `contracts/` too, and it starts in the main checkout, not in a worktree. Its configuration is the owner's.
- ⚠ **Contract:** never pass `<repo>` or `contracts/` as a writable directory. Left outside, Codex can read it, and writing, deleting and renaming there are denied. Second line: the hash test, CI and `git diff -- contracts/`. Anything touching the provider keeps it beside the code. `attrib +R` is not a barrier (`AI-USAGE.md`).
- **Long runs:** run `codex exec` in the background. Unlike `clink`, it has no one-minute client limit. ⚠ Keep `< /dev/null`: with stdin left open, Codex waits on it forever before calling the model, and looks like it is running (`AI-USAGE.md`). Bound the run with `timeout`, and check that its log grows past `Reading additional input from stdin...`.
- **Models:** Codex uses its configured default, `gpt-5.6-sol` at high effort. `-m` picks another from `codex debug models`.
- **PAL's API tools** (`codereview`, `secaudit`, `consensus`, `apilookup`, `chat`) are optional second opinions. Pass real absolute paths.
- **Output:** everything an assistant returns is a proposal. Review it line by line and log its mistakes in `AI-USAGE.md`.
- **Cost:** PAL's API tools with OpenAI models spend API balance, which is low and has no automatic top-up. `codex exec` uses the ChatGPT subscription. Never use `gpt-5.2-pro` without asking the owner.
