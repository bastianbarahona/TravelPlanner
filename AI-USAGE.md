# AI usage

How AI is used in this repository and how its output is verified. The log records what the assistant got wrong, how it was detected and what changed.

## Development harness

| Piece | Where | Needs |
|---|---|---|
| Project rules | `CLAUDE.md` | Claude Code |
| YAGNI for Claude | `.claude/settings.json` declares the Ponytail plugin | Claude Code, with the plugin installed |
| YAGNI for Codex | `AGENTS.md` | Codex |
| Role matrix and protocol | `CLAUDE.md`, "Multi-model assistant protocol (PAL)" | |
| Director: `director-proyecto` | `.claude/agents/` | Claude Code, Opus, high effort |
| Dev and QA: `dev-principal`, `qa-lead` | `.claude/agents/` | Claude Code, Sonnet, high effort |
| Codex for Dev and QA | `codex exec` with `-C`, `-s` and `< /dev/null` | The Codex CLI with a ChatGPT subscription |
| PAL's API tools, optional | `codereview`, `secaudit`, `consensus` and others | The `pal` MCP server, registered at user level, and an OpenAI API key with balance |

None of this is an application dependency.

## Log

### 2026-11-08: Iteration 4, bookings and reconciliation

**Way of working:** one brief, `docs/iteration-4-brief.md`, was the spec for both authors. Claude Sonnet (`dev-principal`) built `app/`. Codex wrote `tests/` from the brief without reading `app/`, and stopped for a usage limit after its first file, so `qa-ux` took Codex's role at once, as `CLAUDE.md` requires, and wrote the rest of the tests and the mutation entries without reading `app/` before its tests existed. Codex came back at 00:48 and reviewed `app/` read-only. The Director probed the two untested decisions on real Postgres before the owner approved them, read `app/` line by line, and ran every suite, mutation and compose check.

**Who validated whom**

| Artifact | Author | Reviewer | High, medium, low | Outcome |
|---|---|---|---|---|
| `app/`, first version | Claude Sonnet | `qa-ux`, in Codex's place | 1, 2, 7 | The High (attempt entities read after `rollback`, outside the snapshot, a 500 under a concurrent delete) confirmed by reading and fixed, with the two Mediums |
| `app/`, after those fixes | Claude Sonnet | Codex, read-only | 1, 2, 1 | The High (a loser of the insert looped into attempt 2 when the winner closed at once) reproduced by a failing test, then fixed. The two Mediums (poll ignored `Retry-After`, retry accepted `null`) and the Low (A16, A42 logs) reproduced and fixed |
| `tests/test_app_bookings.py` | Codex | Claude Sonnet (`qa-lead`) | 1, 7, 10, across all test files | All applied by `qa-ux`, holding Codex's role |
| The other new test files | `qa-ux` | Claude Sonnet (`qa-lead`) | | `CLAUDE.md`'s one exception: `qa-ux` is a separate Claude instance in Codex's place |
| Mutation entries A125 to A181 | `qa-ux` | Claude Sonnet (`qa-lead`) | 0, 2, 3 | Two probabilistic kills made deterministic, 12 entries added for rules left without one |
| Documents of this iteration (`STATUS.md`, `AI-USAGE.md` by the Director; `ARCHITECTURE.md`, `README.md` by Sonnet) | Director, Claude Sonnet | Codex, read-only | 1, 4, 3 | All 8 accepted as wording: a stale draft table read as undecided, the 24 and 12 racers conflated, three guarantees wider than their tests, and compose variables the README overstated |
| Recommendations for the 5 decisions | Director | Probes on real Postgres, each with a control | | Approved by the owner as recommended |

**Where an assistant was wrong**

| Who | What it got wrong | How it was detected | What changed |
|---|---|---|---|
| Claude Sonnet | `itinerary_view` built attempt views after `session.rollback()`, which expires ORM entities: each attribute lazy-loaded in a new READ COMMITTED transaction, and a concurrent delete made it a 500 | `qa-ux`'s review, with a probe of the ORM behaviour | Views built before the rollback, in `itinerary_view` and `_open` |
| Claude Sonnet | On a unique violation, `_open` looped. When the winner's POST had already closed as `failed` or `rejected`, the loser started attempt 2 and sent a second POST | Codex's review, then a failing test with a staged insert delay (two 201s) | The loser gets a 409 with the winner (`BOOKING_CONFLICT` when it closed). Mutation A181 restores the loop and dies |
| Claude Sonnet | The poller ignored a 429's `Retry-After` (A40), and the retry route accepted a JSON `null` body | Codex's review, then failing tests | Both fixed and tested |
| Codex | Hit its usage limit mid-run, after one test file | Its log, checked at once for `usage limit` | `qa-ux` took its role with no wait |
| `qa-ux` | Its `uq_booking_attempt_number` test collided on `client_reference` too, so another constraint fired first | `qa-lead`'s review, reproduced by the Director (`1 failed`) | A distinct reference in the second insert |
| `qa-ux` | Its first closed-winner race test counted a legitimate late click as a second winner: 24 racers outnumber the pool's 15 connections, so the last ones start after the winner closed | Sonnet's timing probe, then `qa-ux`'s own measurement | 12 racers and a staged delay, so every racer loses the insert. The late click is a limit in `ARCHITECTURE.md` |
| Codex and `qa-ux` | Teardown left `application.log` held on Windows: the venv's `python.exe` is a launcher, and its child exits a moment after the kill | 45 errors in Sonnet's run of the suite, explained by the Director with a process probe | `TemporaryDirectory(ignore_cleanup_errors=True)` |
| Director | The mutation runner ran every mapped file until it reached the killing test, about 370 s per mutation, or about 6 hours for 57 | Rule 10: a timed sample of 3 entries before the full run | A killing test node per entry, run first: 57 entries in 16 minutes |
| Director | The brief's 24-racer concurrency case did not foresee a winner that closes at once, or the connection pool's limit on simultaneous opens | Codex's review and the race test above | `ARCHITECTURE.md`, "Limits I accept" |

### 2026-11-07: Iteration 3, cross-review

**Way of working:** the same as Iteration 2. One brief from the Director was the spec for both authors, and it gained an addendum after the first run. Claude Sonnet built `app/`, and Codex wrote `tests/` from the brief without reading `app/`. The Director measured every recommendation before the owner approved it, and ran every local check of the iteration.

**Who validated whom**

| Artifact | Author | Reviewer | Blocker, major, minor | Outcome |
|---|---|---|---|---|
| `app/`, compose, first version | Claude Sonnet | Codex, read-only | 0, 2, 1 | All three reproduced on real processes and fixed by Sonnet |
| `app/`, fixes and the owner's O5, O7 and O8 | Claude Sonnet | Codex, read-only | 0, 2, 0 | The `PATCH /trips` race reproduced and fixed. The malformed-id race rejected |
| `tests/`, first round | Codex | Claude Sonnet (`qa-lead`) | 0, 9, 13. QA's 1 high and 8 medium count as major | All applied in round 2 |
| `tests/`, round 2, and the mutation runner | Codex | Claude Sonnet (`qa-lead`) | 0, 4, 2 | 4 surviving mutations: two test gaps closed, one redundant line of `app/` deleted, one equivalent mutant dropped |
| `tests/`, rounds 3 and 4 | Codex | Claude Sonnet (`qa-lead`) | 0, 3, 4 | Two stale mutations and one false kill corrected, and the O8 coverage widened, in round 6 |
| Documents of this iteration | Director | Codex, read-only | 0, 10, 8 | All 18 accepted. 17 were wording, now fixed. One was a code defect: a `Retry-After` over 9 digits was shortened to 5 s, sent to Sonnet |
| Recommendations for the 7 decisions | Director | Throwaway probes on real processes | 3 corrections | Approved by the owner |

**Where an assistant was wrong**

| Who | What it got wrong | How it was detected | What changed |
|---|---|---|---|
| Director | The first load probe created an HTTP client per request, which costs 0.7 s each to load certificates. Its numbers showed no difference between sync and async | The wall time was 15 to 20 s against a 3 s baseline | Rerun with one shared client and a baseline. The difference appeared (`ARCHITECTURE.md`, Provider client) |
| Director | The brief banned `float(` anywhere in `app/`, although the rule is about money | Sonnet worked around it with `Fraction` and reported it | `float` in `app/config.py`, and the scan exempts that file for that token |
| Director | The brief did not foresee contract-valid strings with a NUL or a lone surrogate | Codex's review of `app/` | Addendum to the brief, then fixed |
| Director | Told Codex that `app/` was final for round 4 while Sonnet was still building O8, so mutations A08 and A23 went stale | `qa-lead`'s third review, with a stale check of every entry | Anchors corrected in round 6 |
| Director | `TaskStop` on the mutation run left `mutation_check.py`, its `pytest`, an orphaned mock from 18:53 and a Postgres container alive, the same failure as in Iteration 2 | Listing processes by command line after the stop | Stopped by process tree and removed by name, then verified at 0 |
| Director | `tests/mutation_check.py` copies `app/` without `contracts/`, so every app mutation would die because the application cannot start, not because a test caught it | Reading the runner before the round that adds mutations | Sent to Codex: copy the contract, and report such a death as a false kill |
| Claude Sonnet | A NUL in a provider string reached jsonb, and add answered 500. It left a `ponytail:` comment instead of refusing it at the boundary | Codex's review, reproduced with the mock's control interface | `_clean` refuses it as `PROVIDER_INVALID_RESPONSE` |
| Claude Sonnet | Reorder and delete checked `version`, then read item state. A commit in between turned the 409 into a 422 or a 404 | Codex's review, reproduced by holding `LOCK TABLE itinerary_items` from a test connection | A refusal that depends on item state rechecks the version first |
| Claude Sonnet | `DELETE ?version=` capped the digits at 40, so 41 digits answered 422 while a body answered 409 | Codex's review, reproduced | The cap is removed |
| Claude Sonnet | Worked around a test rule with `Fraction` instead of raising the conflict | Its own report | Reverted. The rule was fixed at its source, in the brief |
| Codex | Tested lowercase `t`/`z` instants through the mock, which cannot handle them | The first run: the mock's search answered 503 | Sent back: through the stub |
| Codex | Compared `" Lisbon "` with the server's trimmed value | The first run | Sent back |
| Codex | Its overlap test does not separate instants from strings, so comparing strings would pass it | `qa-lead`, checked by the Director by hand | Sent back with two pairs that separate them |
| Codex | Predicted that mutation A39, removing `parse_instant` from the offer check, survives because the schema already refuses the bad instants. The cause was wrong: the contract's `date-time` check accepts `"2030-06-13T11:00:00+01:00\n"`, and only `parse_instant` refuses it, so a stored instant like that would make every later read a 500 | `qa-lead`, reproduced by the Director with the application's validator | A test gap, not redundant code: a case with that instant is added |
| Codex | Missed that A75, the model's deferrable flag, cannot die: the schema always comes from the migration | `qa-lead` | Dropped as an equivalent mutant |
| Claude Sonnet | `RETRY_AFTER` accepted at most 9 digits, so a contract-valid 10-digit wait fell back to 5 s and was retried, which shortened it (A40) | Codex's review of the documents, then confirmed in a benchmark by Sonnet and Haiku | 4300 digits, the integer limit, and mutation A94 |
| Claude Sonnet | `ProviderText` refused lone surrogates but not a NUL, so a NUL in a search's `providerId` reached the provider | Codex predicted it in round 2 and its test failed. The Director reproduced it | `_encodable` refuses a NUL, which also covers `offerId` |
| Director | Overclaimed in the documents: US-05 counted as done, "no connection held" where the test proves no open transaction, an item snapshot no test races, the compose smoke reading the item after the database replacement, and an Iteration 4 prompt that still pointed at Iteration 3 | Codex's review of the documents | 17 wording fixes |
| Claude Sonnet | `PATCH /trips` could pass its version check, then judge the dates against a stale start and answer 422 instead of 409 | Codex's second review, reproduced with `LOCK TABLE trip_destinations` held from a test connection | The version is rechecked before a 422 |
| Codex | As reviewer, reported that a malformed item id under a race answers 404 instead of 409 | The Director: a malformed id can never be an item, so its 404 does not depend on anything a concurrent write changes | Rejected |
| Claude Sonnet | Warned that removing the `trip_member` rollback could stall the pool under a burst of searches | The Director's probe: 80, then 200 simultaneous searches against a stub holding each answer 3 s all answered 200, in 4.1 s and 7.8 s | The deletion stands |
| Claude Sonnet | The `session.rollback()` in `trip_member` is redundant: every route that awaits the provider rolls back in its own service first, so no test can tell it is gone (mutation A53) | Codex predicted it, and `qa-lead` traced every caller | Deleted, with its mutation |

**Would more agents speed this up?** The owner asked whether a Haiku subagent and a second Codex agent on `gpt-5.6-terra` would help Sonnet. Each candidate ran the same read-only task with known answers: Q1 was a real defect (a `Retry-After` over 9 digits shortened to 5 s), Q2 was a trap (a malformed-id race that is not a defect, which Codex on `gpt-5.6-sol` had reported as one), and Q3 was a count.

| Candidate | Q1, real defect | Q2, trap | Q3, count | Time | Tokens |
|---|---|---|---|---|---|
| Claude Sonnet | Found | Right | Right | 25 s | 92,576 |
| Claude Haiku | Found | Right | Right | 44 s | 102,732 |
| Codex, `gpt-5.6-terra` | **Missed** | Right | Right | 78 s | 37,503 |

One task each, so this sizes the question rather than settling it. What bounded this iteration's wall time was the local runs, which no extra agent shortens: the suite (5 min), the mutation check (about 55 min) and the compose smoke (3 min). Running jobs side by side on one machine slowed the suite from 216 s to 354 s and flaked a timing test. A second Codex agent would also draw on the same subscription whose usage limit already stopped one round, although that the quota is shared is not verified.

**Decision, by the owner:** Haiku is used only for mechanical read-only checks, never as an author or a reviewer of record. `gpt-5.6-terra` is not used for reviews. What speeds up the work is parallel sessions with disjoint files and scripted checks (`CLAUDE.md`).

**Tooling:** Codex reached its usage limit in the middle of the second round, before changing any file. The run was queued for the reset 47 minutes later, rather than handing its tests to the application's author. The owner then set the rule in `CLAUDE.md`: when Codex stops, `qa-ux` takes its role at once.

### 2026-11-07: `clink` in a fresh PAL

The owner asked for the live test that the first check of Iteration 2 could not run. A script started a new PAL over stdio and called the real `clink` tool, so `working_dir` came from the current file. Each role tried to create a uniquely named empty file at six paths. The disk matched Codex's report, and every probe was removed.

| Run | Roles | Starts in | `C:\DEV` | Another project | Main `.git` | Main checkout | Main `contracts/` | This worktree |
|---|---|---|---|---|---|---|---|---|
| Fresh PAL, live `clink` | `pal_qa`, `pal_dev` | `C:\DEV\TravelPlanner` | Denied | Denied | Denied | **Written** | **Written** | **Written** |

A session's PAL keeps the configuration it started with: this session's, older than the edit, still answered `C:\DEV` in the same test.

**Result:** item 8 is proven live, and the change keeps Codex out of `C:\DEV` and other projects. `clink` stays banned here, because both roles still wrote in the main checkout and its `contracts/`. The `-s workspace-write` of `pal_dev` does not change that, because `working_dir` puts them inside its workspace. After the test, the contract hash was unchanged and the main checkout was clean.

### 2026-11-07: Iteration 2, cross-review

**First check.** The Iteration 2 plan asked for four proofs before relying on the team.

| Check | Result |
|---|---|
| Codex through the read-only command | Passed: it answered from the worktree, a write was denied, and no file reached the disk |
| Ponytail | Passed: the hook is active, and plugin 4.8.4 is enabled at user and project scope |
| Subagents load with their model and effort | Partly. `dev-principal` and `qa-lead` report `claude-sonnet-5-5`, `director-proyecto` reports `claude-opus-5-5`, and their tools match. This is self-report: their transcript files were empty, and effort is not visible. The owner accepted these models on 2026-11-07 |
| A fresh PAL starts `clink` in `C:\DEV\TravelPlanner` | Not run here. The permission classifier denied the probe, and it was not retried, because `CLAUDE.md` forbids `clink` here. The owner then asked for it, and it passed (entry above) |

**Who validated whom.** The owner asked that Codex and Claude Sonnet validate each other on every artifact. Codex wrote the tests from the brief without reading `app/`, so neither author could fit its work to the other's.

| Artifact | Author | Reviewer | Blocker, major, minor | Outcome |
|---|---|---|---|---|
| `app/`, compose | Claude Sonnet | Codex, read-only | 0, 1, 3 | Three fixed. Codex's fix for the fourth was rejected, and the limit recorded |
| `tests/`, first round | Codex | Claude Sonnet (`qa-lead`) | 0, 3, 11 | All majors and eight minors accepted, three minors rejected |
| `tests/`, second round and `tests/mutation_check.py` | Codex | Claude Sonnet (`qa-lead`) | 0, 1, 4 | The major and two minors applied, plus three more mutations. The rest was optional |
| Sonnet's fixes, `ARCHITECTURE.md` | Claude Sonnet, Director | Codex, read-only | 0, 0, 5 | All applied |
| This entry, `docs/STATUS.md` | Director | Codex, read-only | 0, 2, 3 | One major rejected: Codex looked for the 15 full starts in a log that held only the reduced measure, because the Director named that log. The run outputs confirm the figures. The rest was applied |

Running the suite found no defect in `app/`: every failure was in the tests. The defects in the application came from the read-only review and from a control run. Codex's sandbox denied Python and Docker, so the Director ran everything it wrote.

**Where an assistant was wrong**

| Who | What it got wrong | How it was detected | What changed |
|---|---|---|---|
| Director | Started `codex exec` in the background with stdin left open. It printed `Reading additional input from stdin...` and waited for over six hours, before any model call. The Director told the owner both runs were working, without checking | The owner asked for the status. The logs held 39 bytes and each process had used about 0.1 seconds of CPU. Reproduced: with stdin open the command stayed blocked, and with `< /dev/null` it answered | Both relaunched. `CLAUDE.md` now requires `< /dev/null` and a bounded run |
| Director | Told the owner that `alembic upgrade head` never exits | The probe called `p.wait()` before reading a full stderr pipe, so the probe blocked the process | Probe fixed with `communicate()`, and corrected to the owner |
| Director | The brief said no route may sit outside the schema, but FastAPI registers `/openapi.json` that way | Claude Sonnet reported it while building | The test names that one route |
| Director | Took a 422 on a `version` of 10^30 for a bug | Rerun with a real change: 409. The probe had sent nothing to change | No code change |
| Director | A control run removed the volume mount but left an empty `volumes:` key. Its exit code 1 looked like proof that the smoke can fail | The log held no `AssertionError`: compose had rejected the file | Control redone. It now fails with `trip read answered 404, so the trip was lost` |
| Director | The first measure of the compose race started only `db` and `migrate`, and its 0 of 10 would have cleared a real bug | A later full start failed on the real file | Measured on full starts |
| Director | `docs/STATUS.md` counted US-02 as implemented, although the PRD also asks for planning, document, status and assistant permissions | Codex's review of the documentation | Reported as partial |
| Director | `ARCHITECTURE.md` overclaimed four things: the refusal order, what replaces test identities, what the database refuses, and last-write-wins | Codex's re-review, each with a concrete input | All four corrected |
| Claude Sonnet | The `db` healthcheck used `pg_isready` over the Unix socket, so it reported healthy on the image's temporary init server. `docker compose up` from clean failed 1 of 15 full starts | The Director's control run, then the `db` log | `-h 127.0.0.1` forces TCP: 0 of 15 after |
| Claude Sonnet | An undecodable JSON body answered `400 BAD_REQUEST`, outside the catalog. `/trips/%2F` said "No such route." while a malformed id said "Trip not found.". One constant was unused, and two comments overclaimed | Codex's review, reproduced on real uvicorn and Postgres | Fixed |
| Codex | The same `pg_isready` mistake in `tests/app_support.py`: all 78 application tests errored in setup | The first real run. `qa-lead` found it independently | `-h 127.0.0.1`, and a 90 second deadline that also covers the image pull |
| Codex | Walked `app.routes` as a flat list, but FastAPI 0.162 wraps included routers | The real run: `AttributeError` | `fastapi.routing.iter_route_contexts` |
| Codex | Both 24-way concurrency tests passed with the requests running one after another, because the Python check answers before the guarded `UPDATE` | `qa-lead` | Test-installed triggers with `pg_sleep(0.3)` force the overlap. Mutations A19, A21, A22 and A25 die there |
| Codex | The new echo check failed on `" 420.10"`, which matches the example in the application's own message | The real run | Test value changed |
| Codex | Its first echo check could not see control characters, because `response.text` holds them escaped | `qa-lead` | Compares the JSON-escaped form too |
| Codex | Mutation A05 died on a crash in the handler, not on the rule it named | `qa-lead` | Replaced by a mutation that adds a route with no protection |
| Codex | As reviewer, proposed configuring integer decoding so a 4301-digit `version` reaches the comparison | The Director: Python's limit is a denial-of-service guard | Rejected, recorded under "Limits I accept" |

**Tooling.** `TaskStop` reported success while six processes of the hung runs stayed alive. They were stopped by process id, after matching each one to the worktree path and launch time.

**Debt ledger:** eight `ponytail:` comments. The six in `app/` are recorded under "Limits I accept" in `ARCHITECTURE.md`, and all six name their trigger. The two in `provider-mock/main.py` stay in `docs/STATUS.md`, Section 2.6.

### 2026-11-07: Ponytail

**Integration.** Ponytail 4.8.4 (MIT) applies YAGNI to every assistant. Two pieces, because Codex does not read Claude skills:

| Assistant | How it loads | Evidence |
|---|---|---|
| Claude | Project declaration in `.claude/settings.json`. The plugin's hooks also reach subagents. | Active here at user scope. **Not proven on a clean machine:** with an empty configuration, `claude plugin list` shows nothing, and the interactive offer to install could not be tested from here. `AGENTS.md` holds the same ladder as a fallback. |
| Codex | `AGENTS.md`, adapted from Ponytail's own file | Run from the root and from `tests/`, Codex quoted rung 1 and the precedence sentence. A control outside the repository answered `NO PROJECT RULES`. |

**Limits on it,** in `CLAUDE.md`: it shortens solutions, never guarantees. Its "one check" yields to the required tests, and its "fewest files" to the module layout.

**Debt ledger:** two `ponytail:` comments so far, both in `provider-mock/main.py`: control state in memory, and reads taking the write lock. Both are already in `docs/STATUS.md`, Section 2.6.

### 2026-11-07: `working_dir` change and the contract barrier

**Item 8.** The owner changed `working_dir` in `~/.pal/cli_clients/codex.json` from `C:/DEV` to `C:/DEV/TravelPlanner`. The file is right, but the change is not live yet: PAL caches its configuration in `_REGISTRY` when it starts, and its processes were older than the edit. Through `clink`, both roles still started in `C:\DEV` and wrote everywhere.

The effect was tested with the same command `clink` will run after a restart, from `C:\DEV\TravelPlanner`. Results were read from the disk:

| Run | Roles | `C:\DEV` | Another project | Main `.git` | Main checkout | Main `contracts/` | This worktree |
|---|---|---|---|---|---|---|---|
| Live `clink`, stale configuration | `pal_qa`, `pal_dev` | **Written** | **Written** | Not tried | Written | Written | Written |
| Same command after a restart | `pal_qa`, `pal_dev` | Denied | Denied | Denied | Written | Written | Written |

After the owner restarted PAL, a live `clink` probe from this session still started in `C:\DEV` and wrote there. Each session has its own PAL: this session's dates from 01:50, and the restart reached another one. A fresh load of PAL's registry returns `working_dir = C:\DEV\TravelPlanner`, so the file is proven. The live call is in the entry "`clink` in a fresh PAL".

So the change protects other projects once each PAL restarts. `clink` stays banned here: `pal_qa` passes no `-s` and can write, `contracts/` stays writable, and it starts in the main checkout, not in a session's worktree.

**`attrib +R` tested as a barrier,** on copies of the contract, under `workspace-write`. It is not one.

| Attempt | Result |
|---|---|
| `Set-Content` | Stopped |
| Python `open()` | Stopped |
| Codex's own edit tool | Stopped |
| `Set-Content -Force` | **Tampered** |
| `attrib -R`, then write | **Tampered** |
| Rename, then write | **Tampered** |
| `cmd /c del /f` | **Deleted** |

It is also not versioned. A fresh clone does not have it, and a `git switch` that changes the file silently drops it. It stops accidents, not an agent that insists, so it was not applied.

**What works instead: leaving `contracts/` outside the workspace.** Tested on copies with `codex exec -C <dir> --add-dir <dir2> -s workspace-write`:

| Attempt on `contracts/` | Result |
|---|---|
| Create a file | Denied |
| Overwrite with `Set-Content` | Denied |
| Overwrite with Python `open()` | Denied |
| `cmd /c del /f` | Denied |
| `cmd /c ren` | Denied |
| Read | Allowed |

The directories passed with `-C` and `--add-dir` stayed writable, and a file in the repository root was denied. This needs no per-clone step, and Codex cannot undo it. It does not cover Claude: the guards there are the hash test, CI and `git diff -- contracts/`.

| What went wrong | How it was detected | What changed |
|---|---|---|
| The instruction called `attrib +R` a real barrier. | Testing it on copies | Not applied. A narrower workspace replaces it. |
| Two "protected" results in the first `attrib` run, and one in the workspace run, were false: Python could not start, and a command policy blocked `Remove-Item` before the attribute or the sandbox came into play. | Reading Codex's report, not only the disk verdict | Retested with the `.venv` Python and `cmd /c del`. A blocked command proves nothing about the barrier. |
| The entry below says `contracts/` stays writable to Codex. That was true of `-C <repo>` only. | This test | The Dev command no longer passes `<repo>`. |

### 2026-11-07: Roles, sandbox and architecture notes

**Roles set by the owner.** Director: Claude Code on Opus at high effort. Dev and QA: Codex and Claude on Sonnet at high effort. This replaces the Codex-only matrix of the entry below.

**Sandbox.** The owner asked that Codex reach this repository only, not all of `C:\DEV`. `~/.pal/cli_clients/codex.json` fixes `working_dir` at `C:/DEV` for every `clink` call, and PAL's configuration is off limits, so the test was on the Codex CLI. Each probe asked Codex to create a uniquely named file, and the result was read from the disk, not from Codex.

| Command | Inside the repository | New file in `contracts/` | Main checkout | `C:\DEV` | Another project |
|---|---|---|---|---|---|
| `codex exec -C <repo> -s workspace-write` | Written | Written | Denied | Denied | Denied |
| `codex exec -C <repo> -s read-only` | Denied | Denied | Denied | Denied | Denied |
| `codex exec -C <repo>`, with no `-s` | Written | Written | Denied | Denied | Denied |
| `clink` codex, role `pal_qa` | Not tried | Not tried | **Written** | **Written** | Not tried |

Every probe file was removed. A search of `C:\DEV` found no other file changed since the first PAL call of the day.

**Decision:** Codex runs from the CLI with `-C` and `-s`, and `clink` is not used on this repository. `contracts/` stays writable to Codex, so its guards are the hash test, CI and a review of `git diff`.

**Codex models.** `codex debug models` lists six. One read-only call each, with the prompt "Responde solo: PONG":

| Model | Result |
|---|---|
| `gpt-6-astra` | OK, 9 seconds |
| `gpt-reserve` | OK, 8 seconds |
| `gpt-5.6-sol`, the configured default at high effort | OK, 9 seconds |
| `gpt-5.6-terra` | OK, 7 seconds |
| `gpt-5.6-luna` | OK, 7 seconds |
| `codex-auto-review` | Not tried: it looks internal |

**Subagents: not verified at runtime.** `claude -p --agent` answered `Failed to authenticate: OAuth`, and the session's agent tool only knows agents loaded at start. So `model: opus`, `model: sonnet` and `effort: high` are written but unproven. The next session confirms them first.

**The owner's `ARCHITECTURE_NOTES.md`,** checked against the contract, the PRD and `docs/CONTRACT_NOTES.md`. The result is in `ARCHITECTURE.md`.

| Claim | Verdict | Outcome |
|---|---|---|
| Modular monolith with optimistic locking | Holds. One limit added: the provider stays outside every transaction. | Decision 1 closed |
| Worker and MCP server as separate processes | Direction, not yet a decision | Growth path, decided in Iterations 4 and 6 |
| `Decimal` everywhere, including FastAPI schemas | Holds inside. Wrong at the API boundary, as tested. | `str` with the contract's pattern |
| MCP only through "parametrized" SQL views | Not adopted: views take no parameters, add no permission check, and cannot cover option search (US-12) | Open for Iteration 6 |
| `local_status` and `external_status`, with one immutable UUID | Partly: references are per attempt (A10), and passive reconciliation cannot close `unknown` (A13) | Open for Iteration 4 |
| `ROUND_HALF_UP` in one utility | Holds | Already rule 2 of `CLAUDE.md` |
| Doubt: the contract has no exact expiry | Wrong premise: `validUntil` is required (`:949`) | Closed |
| Doubt: a distributed lock while `unknown` | Not needed: A12 and A14 cover it | Closed |

**Independent reviews,** with `codex exec -s read-only`. Each finding was then checked by hand.

| Review | Time | Findings | Verdict |
|---|---|---|---|
| `docs/CONTRACT_NOTES.md` against the contract, complete. This is the review that timed out through `clink`. | 337 seconds | 3 | 0 contradictions, 3 wording notes |
| `ARCHITECTURE.md` against the contract, the notes, the PRD and the guide | 210 seconds | 1 | Right, and every other claim held |

| Finding | Severity from Codex | Verdict |
|---|---|---|
| Notes line 106: "Bookable" omits the revision and price checks | Medium | Not a contradiction. The line describes an offer's state, while revision and price compare what a client expected, and A28 makes `POST /bookings` the authority. |
| Notes line 182: "Steps 4 to 9 run as one atomic unit" goes beyond the contract | Low | Not a contradiction. Section 2.1 marks that sequence as Adopted, and "exactly one booking" requires it. |
| A44 calls contract-valid amounts "invalid provider data" | Medium | The behavior stands, as a limit the note already admits. The wording is unfair: it is our storage limit. |
| `ARCHITECTURE.md`: `NUMERIC(12,2)` does not cover every contract-valid amount | Medium | Right. Added to "Limits I accept". |

| What went wrong | How it was detected | What changed |
|---|---|---|
| Reviews through `clink` were called read-only, and the assistant trusted that. Only the prompt made them so: the roles pass no `-s`, and `pal_qa` wrote in `C:\DEV` when asked. | Probing the mode with no `-s`, then `clink` itself | `clink` is banned on this repository, and `-s` is mandatory. |
| The protocol said only `pal_dev` could write. Any `clink` role can. | The same probes | The protocol was rewritten. |
| The first probe reported every write as blocked. It was a usage error: `codex sandbox` needs a permission profile, and exit code 2 meant nothing ran. | Reading the exit code instead of the word "blocked" | The test moved to `codex exec`, and the disk decides. |
| `conf/cli_clients/codex.json` in PAL carries `--dangerously-bypass-approvals-and-sandbox`, which suggested no sandbox at all. It is not the file in effect: `~/.pal/cli_clients/codex.json` overrides it. | Comparing it with the command PAL reported | Reported to the owner. Nothing was changed. |

### 2026-11-07: PAL integration

**Preflight.** One call per provider, with no retries.

| Check | Call | Result | Literal cause |
|---|---|---|---|
| Model list | `listmodels` | OK: 2 providers, 12 models | The list was not trusted. Only the models below were called. |
| OpenAI | `chat`, model `gpt-5.2` | OK | |
| Gemini API | `chat`, model `flashlite` | OK | |
| Codex | `clink` codex, role `default` | OK: `PONG` in 13.5 seconds | |
| Gemini CLI | `clink` gemini, role `default` | **Failed** after 16 seconds | `CLI 'gemini' exited with status 1`, with `IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals`, reason code `UNSUPPORTED_CLIENT`, tier `free-tier` |

**Mode: Codex-only.** Full mode needs both the Gemini API and the Gemini CLI. The Gemini API answered, but no later step depends on Gemini.

**Verification.** A review with `clink` codex role `pal_qa`, of `docs/CONTRACT_NOTES.md` against `contracts/openAPI.json`. It was read-only by prompt only, as the entry above shows.

| Attempt | Scope | Result |
|---|---|---|
| 1 | Both files, complete | `Error: Request timed out` on the client, with Codex still running afterwards. No answer received. |
| 2 | Notes 2.2 to 2.4 and 3.1 | 0 findings, in 39.1 seconds |
| 3 | Same scope, on a copy with two seeded errors, read from a file | `Error: Request timed out`. No answer received. |
| 4 | Same two seeded errors, pasted in the prompt | Both found, in 34.2 seconds, with correct citations (`:230` and `:467`) |

**Was the assistant right?** Yes, within that scope. A separate script confirmed the 0 findings: every row of table 3.1 matches the contract's `responses`, the four 409 codes are in the `Error` enum, and the sentences behind 2.4 are in `:230`. Attempt 4 shows the review can fail. The rest of the notes was not verified in this run. No file changed during the reviews, and the hash of `contracts/openAPI.json` stayed the same.

| What went wrong | How it was detected | What changed |
|---|---|---|
| "Prefer `clink` for long work" was written into the protocol before `clink` had run anything long. Calls past about a minute time out on the client, and Codex keeps running with its answer lost. | Attempt 1 timed out, and the process list showed Codex still alive. | The protocol now limits each call to one section or module, and says a timeout is not a result. |
| A timed-out `pal_dev` call would keep writing with nobody watching. Nothing was written here, because the reviews were read-only. | Reasoning from the same process list. | The protocol says to wait for the process to end before reviewing `git diff`. |

### Iterations 0 and 1

Moved from `docs/STATUS.md`. All were detected by testing or by reading the contract. The first eight came up during the contract review. The recommendations behind them came from AI: the owner debated and validated ideas with Gemini Flash, and researched with Google AI (confirmed by the owner on 2026-11-07, closing `docs/STATUS.md` 1.4, item 5).

| What went wrong | How it was detected | What changed |
|---|---|---|
| `Path(max_length=128)` answers 422, not 404. | Running it | The mock takes the raw request, and A41 records the rule. |
| `Optional[str]` accepts an explicit `null`. | Running it | Rule 1 of `CONTRACT_NOTES.md` 1.0 |
| `offers: []` alone does not mean "no results", because `partial` may be true. | Reading the contract | A35 |
| A refresh right before the POST bypasses the user's review of the terms. | Reading the contract | A25 |
| A unique index on attempts would burn rejected references. | Review | A12 |
| A generic Retry on a `failed` booking only replays `failed`. | Reading the contract | Section 3.4 of the notes |
| `FormatChecker` skips `date-time` without `rfc3339-validator`. | Running it | `tests/requirements.txt`, and a test that proves the check fails |
| The first versions of A12 and A25 were imprecise. | Review | Both were rewritten. |
| A46 said no controllable clock was needed, which made required test 8 impossible to prove. | Comparing the note with the test list | Pinnable mock clock, and A46 rewritten |
| A plain SQLite `BEGIN` failed 620 of 640 concurrent requests with `database is locked`. | A spike before the storage decision | `BEGIN IMMEDIATE` |
| The first test suite let an invalid fixture through, because no test read that offer. | Mutation check | A test reads every fixture offer. |
| It also let a failed supplier's offers through, because no failed supplier had any. | Writing the mutations | A Porto fixture offer for the failed supplier |
| The `UPDATE` that stores a resolved booking looked redundant, because a read recomputes the same state. Without it, a clock stepping back reverses a terminal outcome. | Mutation check | It stayed, and it now has a test. |
| An assistant-written test helper contained a dead `if False` expression. | Reading the code before running the mutations | Removed |
| Pydantic's `Decimal` field looked safe with `strict=True`, but it accepts the JSON number `420.1` and emits `"420"`. | A spike for the Iteration 2 plan | The plan uses a `str` field with the contract's pattern. |
| The assistant reported a booking surviving `docker compose restart` and `kill` as proof of durability. Both reuse the container, so neither proved the volume. | Designing CI | `tests/compose_smoke.py` replaces the container and fails without the volume. |
