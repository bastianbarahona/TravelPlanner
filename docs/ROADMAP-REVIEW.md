# Roadmap review

2026-11-07, during Iteration 3. Written by the Director for the owner to evaluate. Sections 1 to 4 are a snapshot from before the owner's decisions and the later review rounds. Section 5 records what the owner adopted.

## 1. Summary

| Item | State |
|---|---|
| Iterations 1 and 2 | Merged, CI green |
| Iteration 3 | Built, in cross-review. The second round of tests and the mutation check are pending |
| Iterations 4 to 8 | Not started |
| User stories | US-01 done, US-02 partial. US-03 to US-06 built at API level, with no interface yet |
| Deadline | Unknown. The candidate guide still says "to be confirmed" |

## 2. Iteration 3

| Part | State | Evidence |
|---|---|---|
| 7 decisions | Approved, each measured first | `ARCHITECTURE.md`, `docs/STATUS.md` 2.4 |
| `app/` | Built. Three defects found by Codex were reproduced and fixed | `AI-USAGE.md` |
| `tests/`, first round | 419 of 421 passed. After the fixes to `app/`, 418 passed and 3 failed, all three test defects already sent back | QA verdict: no-ship until round 2 |
| `tests/`, round 2, and the mutation check | Queued for when Codex's usage limit resets | |
| `docker compose up` from clean | Passed in Sonnet's run. The Director has not rerun it | |

## 3. Roadmap ahead

| # | Goal | Main risk | Decision it needs |
|---|---|---|---|
| 4 | Booking and reconciliation (US-07, US-08) | The hardest guarantees: a crash after commit, `unknown`, and one active attempt per item | The background mechanism without a broker. Whether an item may repeat an offer (O6) |
| 5 | Documents (US-09, US-10) | Keeping files and records consistent when interrupted | Format and storage. It should share the mechanism of Iteration 4 |
| 6 | MCP and assistant (US-11, US-12) | Permissions through tools, and evaluations that run without a model | SDK, transport and LLM provider, parked by the owner (`docs/STATUS.md` 1.4, item 2) |
| 7 | Minimal frontend | The largest unknown effort, and every workflow depends on it | How small "minimal" is |
| 8 | Delivery | A clean clone must run from a README that does not exist yet | Deployment proposal, parked (1.4, item 3) |

## 4. Improvement opportunities

Effort is an estimate. "Accept" means record it as a limit and build nothing.

| ID | Opportunity | Evidence | Impact | Effort | Recommendation |
|---|---|---|---|---|---|
| O1 | Confirm the deadline and set a cut line | 5 iterations remain, and the guide has no date | High: scope | A decision | Owner, now |
| O2 | A fallback for Codex's usage limit | Round 2 stopped at 19:00 until 19:47. The protocol has no rule for it | Medium: throughput | 5 min, one rule | Wait if the reset is under an hour. Otherwise Sonnet writes and Codex reviews later, with the owner's approval  |
| O3 | Rule: report a blocking rule, never work around it | Sonnet dodged the `float(` scan with `Fraction` | Medium: trust in the evidence | 2 min, one line in the agents | Now  |
| O4 | Make diagnostics traceable and tested | The application's `requestId` and the provider's go to separate log lines. A failed leg answers 200 with no id. `AppProcess` discards stdout, so "logged" is unproven. The guide evaluates tracing a failure to its cause | Medium | About 45 min | Iteration 4, which needs it most |
| O5 | A readiness check | `/health` is liveness only. The guide evaluates readiness | Low | About 15 min | Iteration 8, or now if cheap |
| O6 | One item per offer | The same offer may be added twice. In Iteration 4 two items mean two attempts, which can book the same offer twice (A12) | High for Iteration 4 | About 15 min | Decide when planning Iteration 4 |
| O7 | Shorter waits on add and refresh | The worst case is about 25 s: two attempts of up to 10 s each, with up to 5 s between them. The PRD forbids an indefinite wait without status | Medium for the UI | About 10 min, a separate `getOffer` timeout | Decide before Iteration 7 |
| O8 | A consistent trip view | `PATCH /trips` reads its view in three `READ COMMITTED` queries. The itinerary view already uses `REPEATABLE READ` | Low | About 10 min, but hard to test | Accept, or take it in Iteration 4 |
| O9 | A faster mutation check | Each app mutation runs the app suite, about 3 min when nothing fails early, and about 75 are planned | Medium: the time to close an iteration | Measure first | Measure in this iteration. If over 60 min, select tests per mutation |
| O10 | Start the README now | A deliverable, and the done-when of Iteration 8 | Medium: final risk | About 20 min per iteration | Start a skeleton (run, test) when Iteration 4 closes |
| O11 | Tidy test helpers | Four test files import helpers from other test modules. `_iteration3` file suffixes | Low | About 20 min | When those files are next touched |
| O12 | The mock breaks on lowercase `t`/`z` instants | Found in this iteration. Only the control interface can store such an offer | Low | Small, but the mock is out of scope | Accept (`docs/STATUS.md` 2.6) |
| O13 | Open owner items | 1.4 items 5, 8 and 9: the origin of the pasted recommendations, write access for `pal_qa`, and three wording notes. Item 5 blocks Iteration 8 | Low to medium | Owner | Close before Iteration 8 |

## 5. Owner's decisions, 2026-11-07

| ID | Decision |
|---|---|
| O1 | Deadline: Sunday 2026-11-08, 09:00 (UTC-3) |
| O2 | Approved, with no wait: when Codex stops for a usage limit, a separate Sonnet instance with QA and UI/UX skills takes over at once, `qa-ux` in `.claude/agents/` |
| O3 | Approved, with hardening applied to every subagent and to `AGENTS.md` |
| O4 | Approved for Iteration 4 |
| O6 | Approved: decide when planning Iteration 4 |
| O7 | Adjust the waits now, to meet the PRD: one deadline of `PROVIDER_TIMEOUT_SECONDS` per user request (brief, Section 7) |
| O5 | Now, being simple: `GET /ready` |
| O8 | Taken now: trip views read in one snapshot |
| O11 | Approved, now |
| O9, O10, O12 | Approved as recommended |
| O13 | The pasted recommendations came from AI: Gemini Flash for debate and validation, Google AI for research. The owner asked for an explanation of the `pal_qa` integration and its risks, and for examples of the three wording notes, before deciding on them |
