# Contract notes: Travel Provider Mock API

Source: `contracts/openAPI.json` (OpenAPI 3.1.0, API version 0.1.0), read together with `docs/prd.md`, `docs/candidate-guide.md` and `CLAUDE.md`. If these notes disagree with the contract, the contract wins. References such as `:230` are line numbers in `contracts/openAPI.json`.

## How to read these notes

- ⚠ marks a field a naive implementation gets wrong.
- **Stated** means the contract says it. **Adopted** means the contract leaves a gap and Section 4 records the reading used here, by ID (`A1`, `A2`, ...).
- The provider base URL is configuration, with the path prefix `/v1` (`:11`). Every operation requires the `providerToken` bearer token (`:20`).

Local terms, which do not exist in the contract:

| Term | Meaning |
|---|---|
| Attempt | One user-confirmed request to book one itinerary item at exact terms. It owns one `Idempotency-Key`, one `clientReference` and one stored request body. HTTP retries belong to the same attempt. |
| `unknown` | We cannot tell whether the provider accepted the attempt. |
| `rejected` | The provider refused the attempt and no booking exists for it. This is the PRD's "failed request". It is not the provider's `failed`. |
| Open / closed | An attempt is open while its outcome can still change (in flight, `unknown`, `pending`). It is closed once `confirmed`, `failed` or `rejected`. |

---

## 1. Schemas

The contract defines 11 schemas under `components.schemas`, plus 4 parameters and 1 response header (1.12).

### 1.0 Rules that apply to every schema

1. ⚠ **Absent is not `null`.** No field in the contract is nullable. Optional fields must be omitted. Serializing `"origin": null`, `"providerId": null` or `"failure": null` violates the schema, and on a request it earns a 400. With pydantic, writing means `exclude_none=True`, on the client's requests and on the mock's responses. Reading needs its own rule: a field typed `str | None` accepts an explicit `null`, so declare it `str` with a `None` default, which allows absence and rejects `null`.
2. **All nine object schemas set `additionalProperties: false`.** With pydantic this is `extra="forbid"`. The only open object is `Error.details`, which stays a plain dict. An unknown request field is a 400.
3. ⚠ **`format` is not enforced by default.** In JSON Schema 2020-12, `date` and `date-time` are annotations. A contract test needs an explicit format checker, or malformed dates pass. With the Python `jsonschema` package, `FormatChecker` silently skips `date-time` unless `rfc3339-validator` is installed.
4. ⚠ **Timestamps are instants with an offset.** Parse, then compare. `Z` and `+01:00` are both legal, so string comparison is wrong. Pydantic's `datetime` also accepts naive values and Unix integers, so use `AwareDatetime`. Postgres `TIMESTAMPTZ` discards the original offset, and re-serializing rewrites `+00:00` as `Z`, so the snapshot stores the raw JSON, not a re-dumped model. Schedule conflicts and `validUntil` compare instants. The search window uses the literal local date (A31).
5. ⚠ **Identifiers are opaque strings.** They are not UUIDs or integers. Many strings have no `maxLength`, so store them as `TEXT` rather than `VARCHAR(128)`.
6. ⚠ **Pydantic coerces by default.** `"2"` passes as an integer and `"true"` as a boolean. Use `strict=True`, and parse with `model_validate_json`: strict mode rejects date strings when given an already-decoded dict.

### 1.1 `Currency`

String, enum: `USD`. ⚠ A one-value enum, not a free string. `usd` is invalid.

### 1.2 `Category`

String, enum: `transport`, `accommodation`, `activity`. ⚠ The PRD says "transportation" and "activities". The wire values are `transport` and `activity`.

### 1.3 `Money`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `amount` | string | yes | pattern below | ⚠ A JSON string, never a number. Exactly two decimals. `"420"`, `"420.0"`, `"01.00"` and `"-1.00"` are all invalid. `"0.00"` is valid. |
| `currency` | `Currency` | yes | | |

```text
^(0|[1-9][0-9]*)\.[0-9]{2}$
```

⚠ More `amount` traps:

- `str(Decimal("420"))` gives `"420"`, and `Decimal("420.10").normalize()` gives `"420.1"`. Both fail the pattern. Format through `app/money.py`.
- `json.dumps` raises on `Decimal`, and `float()` silently loses precision.
- `Decimal("0.00")` is falsy, so `if price.amount:` mishandles a free activity.
- Keep the string inside JSONB snapshots. Do not convert it to a JSON number.

### 1.4 `SearchRequest`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `category` | `Category` | yes | | |
| `destination` | string | yes | minLength 1, maxLength 200 | Matched case-insensitively (A32). |
| `origin` | string | cond | minLength 1, maxLength 200 | ⚠ Required only when `category` is `transport`, through `allOf`/`if`/`then`. Code generators usually drop that rule, so it needs an explicit validator. |
| `startsOn` | string (date) | yes | | ⚠ A date, not a datetime. First included destination-local day. |
| `endsOn` | string (date) | yes | not before `startsOn` | ⚠ A date, and inclusive. The ordering rule exists only in prose, so the schema will not catch it. |
| `travelers` | integer | yes | minimum 1, maximum 20 | |
| `providerId` | string | no | minLength 1, maxLength 128 | ⚠ Omit to query all suppliers. Sending `null` is a 400. |

### 1.5 `SearchResponse`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `offers` | array of `Offer` | yes | | ⚠ `offers: []` with `partial: true` is legal. Show it as partial, not as "no results". |
| `partial` | boolean | yes | | True exactly when at least one queried supplier failed and at least one succeeded (`:828`). |
| `warnings` | array of `SupplierWarning` | yes | | ⚠ Required even when empty. Non-empty only for a partial search (`:832`). |
| `searchedAt` | string (date-time) | yes | | Provider time. Use it for the freshness label (US-03). |

### 1.6 `SupplierWarning`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `providerId` | string | yes | minLength 1 | No `maxLength` here, unlike `Offer.providerId`. |
| `code` | string | yes | enum: `PROVIDER_UNAVAILABLE`, `PROVIDER_TIMEOUT`, `RATE_LIMITED` | ⚠ Not the same set as `Error.code`. `PROVIDER_TIMEOUT` exists only here, so one shared enum is wrong for both. |
| `message` | string | yes | minLength 1 | |

### 1.7 `Offer`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `id` | string | yes | minLength 1, maxLength 128 | ⚠ Stable across revisions, so it is not unique per quote. A stored quote is identified by `(id, revision)`. |
| `providerId` | string | yes | minLength 1, maxLength 128 | |
| `category` | `Category` | yes | | |
| `title` | string | yes | minLength 1 | |
| `description` | string | yes | minLength 1 | |
| `destination` | string | yes | minLength 1 | Casing may differ from the search input. |
| `origin` | string | no | minLength 1 | ⚠ Optional in the schema, even for transport (A30). |
| `startsAt` | string (date-time) | yes | | ⚠ Includes the offset. Converting to UTC loses the local date. |
| `endsAt` | string (date-time) | yes | later than `startsAt` | ⚠ Same as `startsAt`. The ordering rule exists only in prose. |
| `travelers` | integer | yes | minimum 1, maximum 20 | ⚠ Informational. Never multiply `price` by it. |
| `price` | `Money` | yes | | ⚠ Total for all travelers and the whole segment, stay or activity. Not per person, not per night. |
| `revision` | string | yes | minLength 1 | ⚠ Not an integer, despite the name. Opaque and unordered: compare with equality only. No `maxLength`. |
| `available` | boolean | yes | | ⚠ Not enough to book, and a `200` can carry `false`. Bookable means `available` is true and `now < validUntil`. |
| `validUntil` | string (date-time) | yes | | ⚠ Exclusive: an acceptance at or after this instant is rejected (`:951`). Use `<`, not `<=`. Inside `Booking.offer` it describes the original quote, so a confirmed booking never "expires". |
| `restrictions` | array of string | no | | ⚠ Optional, so direct key access can fail. Absent is legal. |

### 1.8 `CreateBookingRequest`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `clientReference` | string | yes | minLength 1, maxLength 128 | ⚠ Appears in a URL path later, so generate URL-safe values. |
| `offerId` | string | yes | minLength 1, maxLength 128 | |
| `expectedOfferRevision` | string | yes | minLength 1 | ⚠ The field is not called `revision`. A string, with no `maxLength`. |
| `expectedPrice` | `Money` | yes | | ⚠ An object with `amount` and `currency`, not a bare string. |

`Idempotency-Key` is a header, not a body field (1.12).

### 1.9 `Booking`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `id` | string | yes | minLength 1, maxLength 128 | ⚠ Provider identifier, not our primary key. |
| `clientReference` | string | yes | minLength 1, maxLength 128 | |
| `offer` | `Offer` | yes | | ⚠ Immutable snapshot. `Booking` has no price field: the booking total is `offer.price`. |
| `status` | string | yes | enum: `pending`, `confirmed`, `failed` | ⚠ No `unknown` here. Adding it to the shared provider enum would make the mock able to emit it. |
| `createdAt` | string (date-time) | yes | | Acceptance time. |
| `updatedAt` | string (date-time) | yes | not before `createdAt` | ⚠ Last outcome change, not last time checked. Track our own last-checked time. |
| `failure` | `BookingFailure` | no | | ⚠ Present on `failed`, omitted otherwise (`:996`). The schema does not enforce this (A16). Never send `failure: null`. |

### 1.10 `BookingFailure`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `code` | string | yes | minLength 1 | ⚠ A free string, not an enum. Reusing `Error.code` makes valid responses fail to parse. |
| `message` | string | yes | minLength 1 | |

### 1.11 `Error`

| Field | Type | Req | Constraints | Trap |
|---|---|---|---|---|
| `code` | string | yes | enum: `BAD_REQUEST`, `UNAUTHORIZED`, `NOT_FOUND`, `OFFER_CHANGED`, `OFFER_UNAVAILABLE`, `IDEMPOTENCY_CONFLICT`, `REFERENCE_CONFLICT`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE` | ⚠ Branch on `code`, not only on the HTTP status: four different codes share 409. |
| `message` | string | yes | minLength 1 | |
| `requestId` | string | yes | minLength 1 | Log it on every error. Success bodies carry no equivalent. |
| `details` | object | no | additionalProperties true | ⚠ No application may require an undocumented field (`:1097`). `OFFER_CHANGED` carries no guaranteed new terms, so call `GET /offers/{offerId}`. |

### 1.12 Parameters and headers

| Name | In | Type | Req | Constraints | Trap |
|---|---|---|---|---|---|
| `offerId` | path | string | yes | minLength 1, maxLength 128 | |
| `bookingId` | path | string | yes | minLength 1, maxLength 128 | |
| `clientReference` | path | string | yes | minLength 1, maxLength 128 | ⚠ Must be URL-encoded if it is not URL-safe. |
| `Idempotency-Key` | header | string | yes | minLength 1, maxLength 128 | ⚠ Required on `POST /bookings`, scoped to the caller, retained at least seven days. |
| `Retry-After` | 429 response header | integer | not declared | minimum 1 | ⚠ Arrives as header text. Whole seconds only, never an HTTP date. May be absent (A40). |

---

## 2. Booking lifecycle

Source: `POST /bookings` description (`:230`), the `Conflict` response (`:624`) and the lookup descriptions (`:394`, `:461`).

### 2.1 Resolution order

The contract states one ordering rule: the key is resolved before offer terms are checked. The rest of this sequence is Adopted (A2, A3, A4, A5).

| Step | Check | Result | Source |
|---|---|---|---|
| 1 | Bearer token is valid | `401 UNAUTHORIZED` | Adopted |
| 2 | Header and body are valid | `400 BAD_REQUEST` | Adopted |
| 3 | Rate limit or outage | `429 RATE_LIMITED` or `503 PROVIDER_UNAVAILABLE` | Adopted |
| 4a | Key exists for this caller, same semantic body | `200` with the booking's current state. Offer terms are not checked. | Stated |
| 4b | Key exists for this caller, different semantic body | `409 IDEMPOTENCY_CONFLICT` | Stated |
| 5 | New key, `clientReference` belongs to an accepted request | `409 REFERENCE_CONFLICT` | Stated, position Adopted |
| 6 | Offer does not exist | `404 NOT_FOUND` | Adopted |
| 7 | Offer is not available | `409 OFFER_UNAVAILABLE` | Stated, position Adopted |
| 8 | `now >= validUntil`, or revision differs, or price differs | `409 OFFER_CHANGED` | Stated |
| 9 | All checks pass | `201` with status `pending`, `confirmed` or `failed` | Stated |

Steps 4 to 9 run as one atomic unit. Concurrent identical requests create exactly one booking, and the others return that same record.

### 2.2 What returns 201 and what returns 200

| Status | When | Body | What it does not mean |
|---|---|---|---|
| `201` | First acceptance of a new request | `Booking`, status `pending`, `confirmed` or `failed` | ⚠ Not success. `201` may be pending or terminal (`:295`), and an accepted booking may be `failed`. The contract gives no failed example, but it is legal. |
| `200` | Replay: existing key, identical semantic body, including concurrent duplicates (A8) | `Booking` in its current state, which may differ from the first response | ⚠ Not a fresh price check. Returned even if the quote has since expired or changed. A failed booking replays as failed and is never replaced. |

**Rule:** booking state comes from `body.status`, never from the HTTP code.

### 2.3 Every 409

All four mean this request created no booking (`:624`).

| Code | Trigger | Can a booking already exist? | Next read |
|---|---|---|---|
| `OFFER_CHANGED` | New request and any of: quote expired (`now >= validUntil`), revision mismatch, price mismatch | Not under this key | `GET /offers/{offerId}` |
| `OFFER_UNAVAILABLE` | New request and the offer is unavailable | Not under this key | None required |
| `IDEMPOTENCY_CONFLICT` | Same key, different semantic body. JSON property order does not count as a difference. | Maybe | `GET /bookings/by-reference/{clientReference}` with the reference stored for that key |
| `REFERENCE_CONFLICT` | New key, reference already tied to an accepted request. A new key can never create a second booking for a reference. | Yes | `GET /bookings/by-reference/{clientReference}` |

### 2.4 Responses that do not create a booking

| Response | Creates a booking? | Source |
|---|---|---|
| `400` | No | Stated |
| `401` | No | Stated |
| `404` | No | Adopted (A5): missing from the prose list, but there is no offer to book |
| `409`, all four codes | No | Stated |
| `429` | No | Stated |
| `503` | No new booking | Stated, see A1 |
| Transport failure, timeout, missing response | **Unknown** | Stated: not proof of rejection |
| Undeclared status, non-JSON body, schema-invalid `2xx` | **Unknown** | Adopted (A42) |

⚠ The contract's wording is "a *new* booking". These responses describe the request that received them. They say nothing about an earlier attempt that may have been accepted, so a non-creating response on a retry does not prove that no booking exists.

### 2.5 States

```text
POST /bookings, new request
  |-- refused: no record created
  `-- accepted (201) --+-- pending --+-- confirmed  (terminal)
                       |             `-- failed     (terminal)
                       |-- confirmed                (terminal)
                       `-- failed                   (terminal)
```

- `pending` may become `confirmed` or `failed`. Terminal outcomes never reverse.
- Immutable after acceptance: `id`, `clientReference`, `offer` (including price), `createdAt`.
- Mutable, only while pending: `status`, `updatedAt`, and `failure` when it becomes `failed`.
- `unknown` is ours alone (`:394`, `:1026`). The provider never sends it.
- Booking lookups are limited to the creating caller. Missing and nonvisible records both return 404. The contract has no 403.
- Lookups are consistent with committed accepted bookings. A 404 only means nothing is visible at lookup time, and an in-flight create may still complete later (`:394`).

### 2.6 Retention

| Record | Lifetime | Source |
|---|---|---|
| Idempotency key and payload association | At least seven days | `:230`, `:568` |
| `clientReference` uniqueness | Lifetime of the mock dataset | `:230` |
| Accepted bookings, references, idempotency records | Survive transport failures and normal provider restarts. A deliberate fixture reset may clear them. | `:7` |

⚠ An in-memory mock does not satisfy this. It needs durable storage.

### 2.7 Client rules that follow

1. Commit the attempt (key, reference, exact body) before sending the POST. A crash after that commit leaves an `unknown` attempt, never a lost one.
2. Allow one active attempt per itinerary item, enforced by a database constraint (A12). Two editors clicking Book must land on the same attempt, because two keys with two references are two bookings.
3. Retries reuse the stored key and the stored body, unchanged. A lost key cannot be replaced: a new key with the same reference returns `REFERENCE_CONFLICT`.
4. New terms mean a new attempt, with a new key and a new reference. A new attempt can only start after the previous one closed as `rejected` or `failed`. An open attempt blocks it, because it may still be accepted.
5. A `failed` booking spends its key and its reference. Trying again needs both to be new.
6. Store `Booking.offer` as the accepted terms. Later price changes never touch it (US-05).
7. Stored terminal outcomes never change locally, whatever a later lookup returns (A19).
8. Background work only reads. Only a user action sends a POST (A14).

### 2.8 POST outcome to local state

| POST outcome | First POST of the attempt | Retry of an `unknown` attempt |
|---|---|---|
| `200`, `201` | Adopt `body.status` | Adopt `body.status` |
| `409 OFFER_CHANGED`, `409 OFFER_UNAVAILABLE` | `rejected` | `rejected` (A13) |
| `404` | `rejected` | `rejected` (A13) |
| `400` | `rejected`, logged as our bug | Stays `unknown`, logged as our bug |
| `401` | Stays open, same key, alert the operator | Stays `unknown`, alert the operator |
| `429` | Stays open, same key, wait for `Retry-After` | Stays `unknown` |
| `503` | `unknown` (A1) | Stays `unknown` |
| `409 IDEMPOTENCY_CONFLICT`, `409 REFERENCE_CONFLICT` | Look up by reference (Section 3.2) | Look up by reference (Section 3.2) |
| Timeout, connection failure, undeclared response | `unknown` | Stays `unknown` |

### 2.9 Reconciling `unknown`

1. `GET /bookings/by-reference/{clientReference}`. On `200`, adopt the body.
2. On `404`, stay `unknown` and repeat with backoff. A 404 is not proof, and no timer turns `unknown` into `failed`.
3. When a user chooses Retry, re-POST the stored key and body. This can only book the terms that user already accepted. Apply the table in 2.8.

Pending bookings use the same read path until they reach a terminal status.

---

## 3. Error codes

Every `Error` response has its `requestId` logged with the attempt or search it belongs to. `Retry-After` is declared on 429 only.

### 3.1 Status codes by operation

| Operation | 200 | 201 | 400 | 401 | 404 | 409 | 429 | 503 |
|---|---|---|---|---|---|---|---|---|
| `searchOffers` | ✓ | | ✓ | ✓ | | | ✓ | ✓ |
| `getOffer` | ✓ | | | ✓ | ✓ | | ✓ | ✓ |
| `createBooking` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `getBookingByReference` | ✓ | | | ✓ | ✓ | | ✓ | ✓ |
| `getBooking` | ✓ | | | ✓ | ✓ | | ✓ | ✓ |

### 3.2 `Error.code`

| Code | HTTP | Retry? | What the application does |
|---|---|---|---|
| `BAD_REQUEST` | 400 | No | Our request is wrong, so app-side validation should have caught it first. Search: show a failed search with an actionable message. An unknown `providerId` also lands here. Booking: see 2.8. |
| `UNAUTHORIZED` | 401 | Not until configuration is fixed | The service token is missing or invalid. This is an operator problem, so show a provider integration error and alert. Never sign the end user out. |
| `NOT_FOUND` | 404 | Depends on operation | `getOffer`: the offer is gone, so mark the selection unavailable and keep its snapshot. `createBooking`: `rejected`. `getBookingByReference`: not proof of anything, stay `unknown`. `getBooking`: treat like a by-reference 404. |
| `OFFER_CHANGED` | 409 | Not with the same body | Close the attempt as `rejected`, then call `GET /offers/{offerId}` because the error carries no new terms. That call may show `available: false` or return 404. Show old and new terms. A new attempt needs the user's confirmation. Never rebook automatically. |
| `OFFER_UNAVAILABLE` | 409 | No | Close the attempt as `rejected`, mark the item unavailable and point the user to search. Other items in the trip keep their own outcomes (US-08). |
| `IDEMPOTENCY_CONFLICT` | 409 | No | Our bug: the body changed under an existing key. Do not mint a new key. Look up the stored reference. On `200`, adopt the body. On `404`, leave the attempt open and alert. |
| `REFERENCE_CONFLICT` | 409 | No | A booking exists for this reference. Look it up. Adopt it only if `offer.id` and `offer.revision` match the attempt's stored body. Otherwise alert and adopt nothing. This is expected after key expiry (A18). |
| `RATE_LIMITED` | 429 | Reads: yes, after `Retry-After`. Booking: user only (A14). | Reads: wait, retry a bounded number of times, then show a clear status. Booking: show the wait and let the user retry with the same key. Never shorten the wait. |
| `PROVIDER_UNAVAILABLE` | 503 | Reads: yes, with backoff. Booking: user only (A14). | Search: show a failed search, never "no results". Reads: retry with bounded backoff, then show a clear status. Booking: `unknown`, then reconcile by reading (A1). The app never resends the POST on its own. |

### 3.3 `SupplierWarning.code`

These arrive inside a `200` with `partial: true`, not as errors. In all three cases, show the surviving offers, name the failed supplier and offer a retry limited to that `providerId`. Never present the result as a complete search.

| Code | Meaning | Specific handling |
|---|---|---|
| `PROVIDER_UNAVAILABLE` | Supplier failed | Retry later. |
| `PROVIDER_TIMEOUT` | Supplier missed its deadline | Retry later. ⚠ Not a valid `Error.code`. |
| `RATE_LIMITED` | Supplier was throttled | Back off. No per-supplier `Retry-After` exists. |

### 3.4 `BookingFailure.code`

Not an HTTP error. It arrives in a `200` or `201` body, or a lookup, when `status` is `failed`. Store the whole `failure` object as JSONB, show `message`, and never branch on `code` (A17). The next step is a new attempt with a new key and a new reference, after the user reviews current terms (A27). ⚠ This is not the `unknown` Retry: a same-key POST only replays `failed`, so the two UI actions must not share a code path.

### 3.5 Outcomes outside the contract

| Outcome | On `POST /bookings` | On reads |
|---|---|---|
| Timeout or connection failure | `unknown` | Read failure, retry with backoff. Booking state does not change. |
| Undeclared status (for example 500, 502, 504) | `unknown` | Same |
| Non-JSON body, or `2xx` failing the schema | `unknown` | Same, nothing partial is displayed |

---

## 4. Ambiguities and adopted interpretations

"Applies to" says who must honor the reading: the **mock**, the **client**, or both. The client must stay correct if a different provider chose differently, so where possible it does not depend on the mock's choice.

### 4.1 Booking acceptance

| ID | Gap | Interpretation adopted | Applies to |
|---|---|---|---|
| A1 | `:230` says 503 creates no new booking. `CLAUDE.md` rule 4 says a 5xx is never proof of rejection. The contract covers only that request, not an earlier attempt, and it cannot speak for a 503 from something in front of the provider. | A 503 on `POST /bookings` never sets `failed` or `rejected`. The attempt becomes `unknown` and keeps its key. This does not contradict the contract, because nothing assumes a booking exists. The cost is one read. | Client |
| A2 | Only "key before offer terms" is stated. The position of auth, validation and rate limiting is not. | Auth first, because the key is caller-scoped. Validation second, because "different body" presupposes a valid body. Then 429 and 503. A missing `Idempotency-Key` is a 400. | Mock |
| A3 | Order between `REFERENCE_CONFLICT` and the offer checks is not stated. | Reference first. Otherwise a stale request could get `OFFER_CHANGED` and suggest no booking exists when one does. | Mock |
| A4 | Order between `OFFER_UNAVAILABLE` and `OFFER_CHANGED` is not stated. Availability changes also change the revision (`:170`), so a stale client usually matches both. If revision were compared first, `OFFER_UNAVAILABLE` would be close to unreachable. | Availability first. Client: both close the attempt, and after `OFFER_CHANGED` it must still handle `available: false`. | Both |
| A5 | 404 is declared on `POST /bookings` but absent from the prose and from the "no new booking" list. | 404 means the `offerId` is unknown and no booking is created. A valid replay returns 200 even if the offer has since vanished, because the key resolves first. | Both |
| A6 | Does a rejected request consume its `Idempotency-Key`? The text ties replay to accepted bookings, but `:624` says a booking only "may" exist on a key conflict, which suggests keys might be bound without one. | Mock: only accepted requests record a key. A rejected key with an identical body is evaluated again. Client: does not depend on this, because any changed body gets a new key. | Both |
| A7 | Does a rejected request consume its `clientReference`? | No. `:230` limits the conflict to references of accepted requests. The client still mints a new reference per attempt. | Both |
| A8 | The status code returned to the losers of a concurrent create is not stated. | Exactly one `201`, all others `200` with the same record. | Mock |
| A9 | "Semantic request body" is not defined. | Parse the JSON and compare canonical form with sorted keys, so whitespace and property order do not matter. Every field counts, including `clientReference`. Keys and references compare byte for byte. | Mock |
| A10 | Both key and reference are described as "one booking intent", yet the example reference ends in `attempt-1`. | One attempt owns one key and one reference. Reference format: `trip-{tripId}-item-{itemId}-attempt-{n}`, kept URL-safe and within 128 characters. | Client |
| A11 | `:624` says to "use the matching reference" for a key conflict, and that may not be the reference in the conflicting request. | Use the reference stored with the key at attempt creation. | Client |
| A12 | The provider deduplicates per intent, not per offer. Two keys with two references can book one offer twice, and nothing forbids it. | The application allows one active attempt per itinerary item, and none after `confirmed`. Only `rejected` or `failed` allows another. A database uniqueness constraint enforces this, because a UI check or an availability read cannot: `getOffer` holds no inventory (`:170`), and two editors can pass both at once. | Client |

### 4.2 Outcomes and reconciliation

| ID | Gap | Interpretation adopted | Applies to |
|---|---|---|---|
| A13 | A by-reference 404 is explicitly not proof, so the contract offers no read that closes `unknown` negatively. | Only a same-key retry closes it. Because the key resolves first, reaching `OFFER_CHANGED`, `OFFER_UNAVAILABLE` or `404` proves no accepted booking exists under that key. An in-flight original carries the same expected revision, so it fails the same check. Assumes revisions are not reused, which the mock guarantees. | Both |
| A14 | The contract does not say whether retries are automatic. US-07 requires an explicit user action to book. | One user action sends one POST. Only the user can resend it: the app never does so on its own, after a 503, a 429, a timeout or any other outcome. Background reconciliation only reads. The expected revision and price guarantee a retry can only book accepted terms. | Client |
| A15 | Pending has no deadline, and there are no callbacks (`:7`). | Polling uses `GET` only, with backoff (for example 5s, 10s, 30s), for as long as the attempt is open, and resumes from the database after a restart. No timeout changes a pending booking, and none triggers a POST. For the first 60 to 90 seconds (configurable) the UI shows an active wait. After that it shows last-checked time and lets the user move on, while polling continues at a longer interval. The mock resolves pending to the fixture's outcome, `confirmed` or `failed`, after a configurable delay (for example 5 seconds). | Both |
| A16 | "`failure` only on failed" is in prose (`:996`), not enforced by the schema. | Mock always obeys it. If the client sees a violation, it trusts `status`, logs it and uses a generic message when `failure` is missing. | Both |
| A17 | `BookingFailure.code` has no defined vocabulary. | The mock defines its own values. The client stores and displays them and never branches on them. | Both |
| A18 | Keys may expire after seven days, references never do. A same-key retry after expiry takes the new-key path. | Mock never expires keys, which satisfies "at least". Client treats `REFERENCE_CONFLICT` on a same-key retry as key expiry and looks up the reference. | Both |
| A19 | A fixture reset may clear bookings, so a confirmed booking can later return 404. | Stored terminal outcomes stay as they are, and the 404 is logged. Open attempts stay `unknown`. Reset is a demo operation, and application workflows never call it. | Client |
| A20 | Two lookups exist, and the contract does not say which to use for polling. | The client uses by-reference for reconciliation and pending polling, because the reference always exists and the booking id may not. The mock implements both. | Both |

### 4.3 Offers and quotes

| ID | Gap | Interpretation adopted | Applies to |
|---|---|---|---|
| A21 | `GET /offers/{offerId}` takes no party, dates or supplier, yet an offer has all three. | An offer id identifies one product for one party and schedule, and ids are unique across suppliers. A different party or window yields a different id. | Mock |
| A22 | "Fit the requested party" could mean equal or capacity. | Mock returns `offer.travelers` equal to the request. Client shows `offer.travelers` and flags a mismatch with the trip party. | Both |
| A23 | When does `getOffer` return 404 rather than `available: false`? Both are allowed. | `200` with `available: false` for a known unavailable offer, 404 for an unknown id. Client treats both as not bookable. | Both |
| A24 | `:170` refreshes an expired quote "when terms can still be quoted". The other case is undefined. | Return `available: false` with a new revision. The client's bookable rule (1.7) covers this whatever the `validUntil`. | Both |
| A25 | Search "may" refresh expired quotes, so results can already be expired. | Mock always refreshes. Client still shows a past `validUntil` as expired and refreshes through `getOffer` before the user confirms. ⚠ Never between confirmation and the POST: `expectedOfferRevision` is the revision the user reviewed (`:985`), so a silent refresh would defeat the check. If a refresh shows a new revision, show it and ask again. | Both |
| A26 | Revision triggers (`:170`) omit restrictions, title and description, and "whenever" is not "only when". | Mock: any `Offer` field change produces a new revision, and an unchanged quote keeps its revision. Client: infers nothing from a change beyond inequality, and treats `Booking.offer` as the record of accepted terms. That snapshot is stored when the provider's response arrives, because it does not exist yet when the user confirms. | Both |
| A27 | US-07 requires reconfirmation for changed terms. A revision can change with only `validUntil` moving. | Every revision change requires reconfirmation. The request body changes anyway, so it is a new attempt, and it costs the user one click. | Client |
| A28 | The clock that decides expiry is not stated, and the mock's clock may not be the app's. | `POST /bookings` is the only authority. The app's expired label uses its own clock and is a hint. Freshness is shown from `searchedAt`. | Client |
| A29 | Can search return `available: false`? The schema allows it. | Yes. The client shows such offers as unavailable and does not let them be booked. | Both |
| A30 | `Offer.origin` is not required for transport in the schema, only implied by its description. | Mock always sends it for transport. Client treats its absence as missing information (US-04), not a parse error. An explicit `null` is still invalid (1.0, rule 1), so the field is not `Optional[str]`. | Both |

### 4.4 Search

| ID | Gap | Interpretation adopted | Applies to |
|---|---|---|---|
| A31 | The window is destination-local, but no field gives the destination's time zone. The transport example cannot settle this, because London and Lisbon share `+01:00` in June. | Mock writes `startsAt` and `endsAt` in the destination's offset. An offer fits when both literal dates fall inside the window. The client does not refilter by date, and schedule conflicts compare instants. | Both |
| A32 | "Case-insensitive" does not say exact or substring. | Exact match after case folding, with no aliases or fuzzy matching. The folding happens in the mock. The app only trims input, then rejects it if nothing is left, and keeps the user's casing. Fixture location names get documented. | Both |
| A33 | The schema allows `origin` on non-transport searches. | Mock ignores it. Client omits it. | Both |
| A34 | A known `providerId` that does not support the category is undefined. | `200`, empty `offers`, `partial: false`. Only an unknown supplier is a 400. | Mock |
| A35 | "503 or 429 for a rate-limited single-supplier query" does not cover several suppliers all being rate-limited. A provider-wide limit is also possible. | Multi-supplier total failure is 503. 429 means a single queried supplier was rate-limited, or a provider-wide limit. The client treats both as a failed search. Per-supplier detail is lost, because `Error` has no warnings. ⚠ The "no results" screen requires `200`, empty `offers` and `partial: false`. An empty list with `partial: true` means a supplier failed, so it is shown as partial. | Both |
| A36 | A window in the past is not addressed. | Not an error for the mock: `200` with whatever matches. The app refuses a past start date before calling the provider, on search and when trip dates are set, but not on other edits, so a trip under way stays editable. It compares against the UTC date minus one day, because the destination's date can lag UTC and `date.today()` depends on the server's zone. | Both |
| A37 | Search takes one destination, while US-01 allows several. A transport offer is one segment. | One search per destination. A return trip is a separate transport search with origin and destination swapped, and its own dates. Legs may run concurrently with `asyncio.gather(..., return_exceptions=True)`, so a failed leg does not lose the others (US-03). The app returns one result per leg, each with its own `offers`, `partial`, `warnings`, `searchedAt` or error, never one merged list. | Client |

### 4.5 Cross-cutting

| ID | Gap | Interpretation adopted | Applies to |
|---|---|---|---|
| A38 | "Authenticated caller" could be the end user or the application (`:527` says service-to-service). | The caller is the application, with one stable token. Roles are enforced in the app. ⚠ Changing the token makes every existing booking invisible, and that 404 looks like "no booking". The mock maps configured tokens to callers so a test can prove scoping. Offers are not caller-scoped. | Both |
| A39 | `:974` says unique within the caller's dataset, while `:230` says lifetime of the mock dataset. | The second gives duration, the first gives scope: unique per `(caller, clientReference)`. A global rule would leak the existence of other callers' references. This is the mock's constraint, on accepted bookings only, because a rejected request leaves no record (A6, A7). Idempotency keys get the same scope. Lookups filter by the caller from the token and return 404 for anything else. The application has a single caller (A38), so its own tables need no caller column. | Mock |
| A40 | `Retry-After` is not marked required, and 503 declares none. | Mock always sends it on 429. Client waits at least `Retry-After`, or a configured default (for example 5 seconds) if it is absent or invalid. On 503 it uses exponential backoff with jitter (for example 2s, 4s, 8s). Both apply to reads, because a POST is only resent by the user (A14). | Both |
| A41 | GET path parameters have length limits, but the GET operations declare no 400. | A value outside the limits returns 404. ⚠ FastAPI's `Path(max_length=128)` answers 422 with a `detail` body, and the contract declares neither. The mock must map validation errors itself, to 404 on `GET` and 400 on `POST`, and every error body must be an `Error`. | Mock |
| A42 | Responses outside the contract are not addressed. | On `POST /bookings` they mean `unknown`, never `rejected` or `failed`, and the attempt keeps its key and reference. Log the status and a truncated body, never the `Authorization` header. On reads they are read failures (3.5): local state stays as it was, and a failure count only widens the polling interval, never ends polling. | Client |
| A43 | No timeout values exist, and `PROVIDER_TIMEOUT` has no threshold. | Both are environment configuration, in separate settings for each service (for example 10 seconds for the client, 5 for the mock's supplier deadline). The client timeout exceeds the whole search, so a slow supplier shows up as a warning and not as a client timeout. That requires the mock to query suppliers concurrently, because sequential deadlines add up. ⚠ Set the `httpx` timeout explicitly: its 5 second default equals the example deadline. | Both |
| A44 | The `amount` pattern has no upper bound, and `CLAUDE.md` fixes `NUMERIC(12,2)`. | Amounts above `9999999999.99` are invalid provider data at the client boundary. The limit lives only in the client's model: the mock keeps the contract's pattern, because a larger amount is still contract-valid. Fixtures stay far below it. | Client |
| A45 | `additionalProperties: false` could be read as strict or lenient for a client. | The client parses responses strictly, following the rules in 1.0. Contract tests validate the mock with format checking on. A schema-invalid read is a provider error. A schema-invalid `2xx` on POST is `unknown`: the attempt keeps its key and reference, and is never marked `rejected`. Rules that exist only in prose stay outside this check (A16). | Both |
| A46 | Fixtures need future dates or a documented clock, and control endpoints sit outside the public contract. | Schedules use fixed far-future dates. `validUntil` is mock time plus a configurable TTL, so tests shorten the TTL and no controllable clock is needed. Expiry tests use their own mock instance and wait past the TTL, because a short TTL shared with other tests would turn slow happy paths into `OFFER_CHANGED`. Tests may use mock controls. Application workflows never do. | Mock |
