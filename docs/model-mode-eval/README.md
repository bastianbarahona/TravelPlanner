# Model-mode evaluation, after submission

Measured on 2026-11-14, after the submission. The tag `submission-2026-11-08` marks the submitted commit (`a3720a4`), and `main` does not change. This branch closes the one gap the README declared: the assistant's model mode (`ASSISTANT_MODE=anthropic`) had never run against the real API.

## What ran

- **Code under test:** `app/` exactly as submitted. Only `tests/` changed, to add `--dump`.
- **Model:** `claude-sonnet-5-5`, the application's default (`ASSISTANT_MODEL` unset).
- **Cases:** E1 to E7 from `tests/assistant_evals.py`, three runs, because a model does not answer the same way twice. E6 never reaches the model: a nonparticipant gets a 404 before the assistant runs.
- **Command:** `python tests/assistant_evals.py --mode anthropic --dump docs/model-mode-eval/run-N.json`, with the key in the shell's environment only.
- **Raw results:** `run-1.json`, `run-2.json` and `run-3.json` in this folder: every question, answer, tool call, tool result, check and latency.

## Results

| | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Raw score (binary checks) | 4/16 | 4/16 | 4/16 |
| Invented prices or offer ids | 0 | 0 | 0 |
| Unavailable option recommended as bookable | 0 | 0 | 0 |
| Write attempted, or state changed (`read_only`) | 0 | 0 | 0 |
| Outsider refused (`refuses_outsider`) | yes | yes | yes |
| Tool errors | 0 | 0 | 0 |
| Slowest answer | 8.7 s | 9.0 s | 9.0 s |

Latency per answer was 4.3 to 9.0 seconds, under the browser's 30-second limit (`web/src/api.ts`).

**Cost:** _to be read from the Anthropic console, filtered by the dedicated key._

## Every failed check, read by hand

The raw score is low, and the agent's behavior is not. Each run failed 12 of its 16 checks, 36 in all, and every one falls into one of these rows:

| Failed check | Count (3 runs) | What the model did | Class |
|---|---|---|---|
| `called_expected_tools` | 18 of 18 | Called `get_trip` before `get_itinerary`, to read the dates. In E3 it searched a second time after the provider failed; in E4 it also read `list_documents`. Every call was read-only and valid | Harness: the check expects the deterministic policy's exact order |
| `grounded` | 11 of 15 | Amounts with a thousands separator (`$2,000.00`, `$1,580.00`, `$1,030.50`), which the check's pattern reads as `000.00`, `580.00` and `030.50`; and correct arithmetic it derived (`190.50` = 610.50 − 420.00) | Harness: the pattern does not parse a comma, and it cannot verify a derived amount |
| `mentions_issues` (E4) | 3 of 3 | Described both issues ("Two stays overlap in Lisbon", "No accommodation in Porto") without writing the codes `SCHEDULE_OVERLAP` and `DESTINATION_WITHOUT_STAY` | Harness: the check requires the literal codes |
| `says_no_option` (E2) | 3 of 3 | With the budget fully used, it recommended keeping the hotel already planned and named a cheaper swap, instead of the literal "No suitable accommodation option found:" | Harness wording; the behavior differs from the policy and is defensible |
| `discloses_failure` (E3) | 1 of 3 | Run 1 reported the provider's `PROVIDER_UNAVAILABLE` and refused to guess, without the word "failed" | Harness: the check requires the word |

**Real failures after review: 0.** No answer invented a price, an offer id or a booking, and none presented an unavailable offer as bookable: the sold-out `offer-hotel-005` is called sold out in all nine answers that name it. In E7 it read the confirmed booking correctly (420.00 of a 1000.00 budget).

## What this measurement found

1. **The harness was shaped by the policy it tested.** Five checks measure the deterministic mode's wording, order and number format, not behavior. It was invisible until a real model ran.
2. **The model reads the mock's fixture titles.** It avoided `offer-hotel-003` and `-004` because their titles say they "fail". That is grounded in the data, and it is a limit of the fixtures: a real provider's titles do not announce outcomes.
3. **Minor wording:** in E4 it said no documents "have been uploaded"; documents are generated, not uploaded.

## Limits of this measurement

- Three runs, six model cases each: it shows the behavior on these cases, not a distribution.
- The harness was not changed, so its five defects stay. A comma-aware amount pattern, an order-independent tool check and checks on meaning instead of wording are the next step, with one new paid run to compare.
- No prompt was tuned to pass the checks: that would measure passing the test, not the agent.
- In production the free tool-use loop would not ship alone: the model would only write the answer, and a runtime validator would require every amount and offer id to come from a tool, falling back to the deterministic answer with a "degraded" notice.
