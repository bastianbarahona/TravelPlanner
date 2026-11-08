import { useEffect, useMemo, useRef, useState } from "react";
import { IDENTITIES } from "./api";
import { TripForm, valuesOf, type TripValues } from "./TripForm";
import type { AssistantAnswer, Attempt, BookingState, Doc, Item, Itinerary, Leg, Offer, Quote, Trip } from "./types";
import { Field, FieldError, OtherErrors, type FieldErrors } from "./ui";

export const PENDING_RELEASE_MS = 75_000;

const BOOKING_TEXT: Record<BookingState, string> = {
  pending: "Pending: the provider has not decided yet",
  confirmed: "Confirmed",
  failed: "Failed: the provider refused this booking",
  rejected: "Not booked: the terms were refused before booking",
  unknown: "Unknown: we could not confirm the outcome. This is not a failure.",
};
const DOCUMENT_TEXT: Record<Doc["state"], string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  complete: "Complete",
  failed: "Failed",
};

// Trip summary, and the organizer's edit form.

export function TripSummary({ trip, errors, busy, onSave }: {
  trip: Trip;
  errors: FieldErrors;
  busy: boolean;
  onSave: (values: TripValues, typed: Partial<TripValues>) => Promise<boolean>;
}) {
  const initial = useMemo(() => valuesOf(trip), [trip]);
  return (
    <section data-testid="trip-summary">
      <h2>{trip.destinations.join(", ")}</h2>
      <p>Dates: {trip.startsOn} to {trip.endsOn}</p>
      <p>Budget: {trip.budgetUsd} USD</p>
      <p>Travelers: {trip.travelers}</p>
      <p>Version {trip.version}</p>
      <p>Your role: {trip.role}</p>
      {trip.role === "organizer" && <TripForm initial={initial} submitLabel="Save trip" errors={errors} busy={busy} onSubmit={onSave} />}
    </section>
  );
}

// Participants (organizer only).

export function Participants({ trip, errors, onAdd, onRole, onRemove }: {
  trip: Trip;
  errors: FieldErrors;
  onAdd: (userId: string, role: string) => void;
  onRole: (userId: string, role: string) => void;
  onRemove: (userId: string) => void;
}) {
  const free = IDENTITIES.filter((name) => !trip.participants.some((p) => p.userId === name));
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState("viewer");
  const chosen = free.includes(userId) ? userId : (free[0] ?? "");
  return (
    <section>
      <h3>Participants</h3>
      <ul data-testid="participants">
        {trip.participants.map((p) => (
          <li key={p.userId}>
            <span>{p.userId}</span> <span>{p.role}</span>
            {p.role !== "organizer" && (
              <>
                {" "}
                <select aria-label={`Role of ${p.userId}`} value={p.role} onChange={(e) => onRole(p.userId, e.target.value)}>
                  <option value="viewer">viewer</option>
                  <option value="editor">editor</option>
                </select>{" "}
                <button type="button" onClick={() => onRemove(p.userId)}>Remove</button>
              </>
            )}
          </li>
        ))}
      </ul>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onAdd(chosen, role);
        }}
      >
        <Field id="participant-user" label="User" name="userId" errors={errors}>
          {(control) => (
            <select {...control} value={chosen} onChange={(e) => setUserId(e.target.value)}>
              {free.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          )}
        </Field>
        <Field id="participant-role" label="Role" name="role" errors={errors}>
          {(control) => (
            <select {...control} value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="viewer">viewer</option>
              <option value="editor">editor</option>
            </select>
          )}
        </Field>
        <OtherErrors errors={errors} known={["userId", "role"]} />
        <button type="submit" disabled={free.length === 0}>Add participant</button>
      </form>
    </section>
  );
}

// Search.

function Option({ quote, inItinerary, canWrite, busy, onAdd }: { quote: Quote; inItinerary: boolean; canWrite: boolean; busy: boolean; onAdd: () => void }) {
  const { offer } = quote;
  const titleId = `option-title-${offer.id}`;
  return (
    <li data-testid={`option-${offer.id}`}>
      <h5 id={titleId}>{offer.title}</h5>
      <p>{offer.startsAt} to {offer.endsAt}</p>
      <p>Price: {offer.price.amount} {offer.price.currency}</p>
      <p>{offer.available ? "Available" : "Unavailable"}</p>
      {offer.restrictions?.map((text) => <p key={text}>Restriction: {text}</p>)}
      <p>Estimate, not a confirmed price</p>
      {inItinerary && <p>Already in the itinerary</p>}
      {canWrite && <button type="button" aria-describedby={titleId} disabled={inItinerary || busy} onClick={onAdd}>Add to itinerary</button>}
    </li>
  );
}

function LegView({ leg, offerIds, canWrite, busy, onAdd }: { leg: Leg; offerIds: Set<string>; canWrite: boolean; busy: boolean; onAdd: (offer: Offer) => void }) {
  return (
    <section data-testid={`leg-${leg.destination}`} data-status={leg.status}>
      <h4>{leg.destination}</h4>
      {leg.status === "complete" && <p>Complete</p>}
      {leg.status === "partial" && <p>Partial results: some suppliers did not answer</p>}
      {leg.status === "empty" && <p>No options found</p>}
      {leg.status === "failed" && <p>Search failed</p>}
      {leg.warnings.map((w, i) => <p key={i}>{w.message}</p>)}
      {leg.error && <p>{leg.error.message}</p>}
      {leg.error?.retryAfterSeconds != null && <p>Try again in {leg.error.retryAfterSeconds} s</p>}
      {leg.status !== "failed" && leg.searchedAt && <p>Searched at {leg.searchedAt}</p>}
      <ul>
        {leg.options.map((quote) => (
          <Option key={quote.offer.id} quote={quote} inItinerary={offerIds.has(quote.offer.id)} canWrite={canWrite} busy={busy} onAdd={() => onAdd(quote.offer)} />
        ))}
      </ul>
    </section>
  );
}

export function SearchPanel({ legs, searching, errors, offerIds, canWrite, busy, onSearch, onAdd }: {
  legs: Leg[] | null;
  searching: boolean;
  errors: FieldErrors;
  offerIds: Set<string>;
  canWrite: boolean;
  busy: boolean;
  onSearch: (category: string, origin: string) => void;
  onAdd: (offer: Offer) => void;
}) {
  const [category, setCategory] = useState("transport");
  const [origin, setOrigin] = useState("");
  return (
    <section>
      <h3>Search</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSearch(category, origin);
        }}
      >
        <Field id="search-category" label="Category" name="category" errors={errors}>
          {(control) => (
            <select {...control} value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="transport">transport</option>
              <option value="accommodation">accommodation</option>
              <option value="activity">activity</option>
            </select>
          )}
        </Field>
        <Field id="search-origin" label="Origin" name="origin" errors={errors}>
          {(control) => <input {...control} value={origin} onChange={(e) => setOrigin(e.target.value)} />}
        </Field>
        <OtherErrors errors={errors} known={["category", "origin"]} />
        <button type="submit" disabled={searching}>Search</button>
      </form>
      <div aria-live="polite">
        {searching && <p>Searching...</p>}
        {legs?.map((leg) => (
          <LegView key={leg.destination} leg={leg} offerIds={offerIds} canWrite={canWrite} busy={busy} onAdd={onAdd} />
        ))}
      </div>
    </section>
  );
}

// Itinerary.

function BookingView({ item, attempt, describedBy, canWrite, busy, onBook, onRetry }: {
  item: Item;
  attempt: Attempt;
  describedBy: string;
  canWrite: boolean;
  busy: boolean;
  onBook: () => void;
  onRetry: () => void;
}) {
  // The release counts from the attempt's own createdAt, so it survives a reload. An unreadable date never releases.
  const released = attempt.state === "pending" && Date.now() - Date.parse(attempt.createdAt) >= PENDING_RELEASE_MS;
  return (
    <div data-testid={`booking-${item.id}`} data-state={attempt.state}>
      <p>{BOOKING_TEXT[attempt.state]}</p>
      {released && <p>Still pending. You can leave this page: the booking keeps being checked.</p>}
      <p>{attempt.nextStep}</p>
      {attempt.lastCheckedAt && <p>Last checked {attempt.lastCheckedAt}</p>}
      {canWrite && (attempt.state === "failed" || attempt.state === "rejected") && (
        <button type="button" aria-describedby={describedBy} onClick={onBook}>Book</button>
      )}
      {canWrite && attempt.state === "unknown" && (
        <button type="button" aria-describedby={describedBy} disabled={busy} onClick={onRetry}>Retry booking</button>
      )}
    </div>
  );
}

export interface Review {
  itemId: string;
  offer: Offer;
  changed: boolean;
}

/** Takes focus when it opens, so the keyboard and a screen reader land on the terms to review. */
function ReviewView({ review, busy, onConfirm, onCancel }: { review: Review; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  const shown = useRef<HTMLDivElement>(null);
  useEffect(() => {
    shown.current?.focus();
  }, [review]);
  return (
    <div ref={shown} tabIndex={-1} data-testid="booking-review">
      <p>Price: {review.offer.price.amount} {review.offer.price.currency}</p>
      <p>Revision: {review.offer.revision}</p>
      <p>Availability: {review.offer.available ? "Available" : "Unavailable"}</p>
      {review.changed && <p>The terms changed. Review the new price before confirming.</p>}
      {review.offer.available ? (
        <button type="button" disabled={busy} onClick={onConfirm}>Confirm booking</button>
      ) : (
        <p>This option is unavailable</p>
      )}{" "}
      <button type="button" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export function ItinerarySection({ itinerary, canWrite, busy, errors, review, onMove, onRemove, onBook, onRetry, onConfirm, onCancel }: {
  itinerary: Itinerary;
  canWrite: boolean;
  busy: boolean;
  errors: FieldErrors;
  review: Review | null;
  onMove: (index: number, by: -1 | 1) => void;
  onRemove: (item: Item) => void;
  onBook: (item: Item) => void;
  onRetry: (item: Item) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { items, budget } = itinerary;
  const kind = (priceKind: string) => (priceKind === "confirmed" ? "Confirmed price" : "Estimate");
  return (
    <section>
      <h3>Itinerary</h3>
      <FieldError errors={errors} name="itemIds" />
      {items.length === 0 && <p>No items yet</p>}
      <ol>
        {items.map((item, index) => {
          const { offer } = item;
          const titleId = `item-title-${item.id}`;
          // A confirmed item is shown at the price the provider accepted, like its budget line.
          const price = (item.booking?.state === "confirmed" && item.booking.booking?.offer.price) || offer.price;
          return (
            <li key={item.id} data-testid={`item-${item.id}`} data-offer-id={offer.id}>
              <h4 id={titleId}>{offer.title}</h4>
              <p>Category: {offer.category}</p>
              <p>{offer.startsAt} to {offer.endsAt}</p>
              <p>Price: {price.amount} {price.currency}</p>
              <p>{kind(item.priceKind)}</p>
              {canWrite && (
                <p>
                  <button type="button" aria-describedby={titleId} disabled={busy || index === 0} onClick={() => onMove(index, -1)}>Move up</button>{" "}
                  <button type="button" aria-describedby={titleId} disabled={busy || index === items.length - 1} onClick={() => onMove(index, 1)}>Move down</button>{" "}
                  <button type="button" aria-describedby={titleId} disabled={busy} onClick={() => onRemove(item)}>Remove</button>
                  {!item.booking && (
                    <>
                      {" "}
                      <button type="button" aria-describedby={titleId} onClick={() => onBook(item)}>Book</button>
                    </>
                  )}
                </p>
              )}
              {item.booking && (
                <BookingView item={item} attempt={item.booking} describedBy={titleId} canWrite={canWrite} busy={busy} onBook={() => onBook(item)} onRetry={() => onRetry(item)} />
              )}
              {review?.itemId === item.id && <ReviewView review={review} busy={busy} onConfirm={onConfirm} onCancel={onCancel} />}
            </li>
          );
        })}
      </ol>
      <h4>Issues</h4>
      {itinerary.issues.length === 0 && <p>No issues found</p>}
      <ul data-testid="issues">
        {itinerary.issues.map((issue, i) => <li key={i}>{issue.message}</li>)}
      </ul>
      <h4>Rules</h4>
      <ul data-testid="rules">
        {itinerary.rules.map((rule, i) => <li key={i}>{rule}</li>)}
      </ul>
      <div data-testid="budget">
        <h4>Budget</h4>
        <ul>
          {budget.lines.map((line) => (
            <li key={line.itemId}>{line.title}: {line.amountUsd} {budget.currency} ({kind(line.priceKind)})</li>
          ))}
        </ul>
        <p>Estimated (USD): {budget.estimatedUsd}</p>
        <p>Confirmed (USD): {budget.confirmedUsd}</p>
        <p>Total (USD): {budget.totalUsd}</p>
        <p>Budget (USD): {budget.budgetUsd}</p>
        <p>Difference (USD): {budget.differenceUsd}</p>
        <p>{budget.overBudget ? "Over budget" : "Within budget"}</p>
        <p>{budget.roundingRule}</p>
      </div>
    </section>
  );
}

// Documents.

export function Documents({ docs, canWrite, busy, onGenerate, onDownload, onRetry }: {
  docs: Doc[] | null;
  canWrite: boolean;
  busy: boolean;
  onGenerate: () => void;
  onDownload: (doc: Doc) => void;
  onRetry: (doc: Doc) => void;
}) {
  return (
    <section>
      <h3>Documents</h3>
      {canWrite && <button type="button" disabled={busy} onClick={onGenerate}>Generate document</button>}
      {docs?.length === 0 && <p>No documents yet</p>}
      <ul data-testid="documents">
        {docs?.map((doc) => {
          const versionId = `document-version-${doc.id}`;
          return (
            <li key={doc.id} data-testid={`document-${doc.id}`} data-state={doc.state}>
              <p id={versionId}>Trip version {doc.tripVersion}</p>
              <p>Requested at {doc.requestedAt}</p>
              <p>Attempts: {doc.attempts}</p>
              <p>{DOCUMENT_TEXT[doc.state]}</p>
              <p>{doc.nextStep}</p>
              {doc.state === "failed" && doc.error && <p>{doc.error}</p>}
              {doc.state === "complete" && <button type="button" aria-describedby={versionId} onClick={() => onDownload(doc)}>Download</button>}
              {canWrite && doc.state === "failed" && (
                <button type="button" aria-describedby={versionId} disabled={busy} onClick={() => onRetry(doc)}>Retry</button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// Assistant.

export function AssistantPanel({ answer, asking, errors, onAsk }: {
  answer: AssistantAnswer | null;
  asking: boolean;
  errors: FieldErrors;
  onAsk: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");
  return (
    <section data-testid="assistant">
      <h3>Assistant</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onAsk(question);
        }}
      >
        <Field id="assistant-question" label="Question" name="question" errors={errors}>
          {(control) => <textarea {...control} maxLength={1000} value={question} onChange={(e) => setQuestion(e.target.value)} />}
        </Field>
        <OtherErrors errors={errors} known={["question"]} />
        <button type="submit" disabled={asking}>Ask</button>
      </form>
      <div aria-live="polite">
        {asking && <p>Asking...</p>}
        {answer && (
          <>
            <div data-testid="assistant-answer">
              {answer.answer.split("\n").map((line, index) => <p key={index}>{line}</p>)}
            </div>
            <p data-testid="assistant-mode">Mode: {answer.mode}</p>
            <ul data-testid="assistant-tools">
              {answer.toolCalls.length === 0 && <li>No tools were called.</li>}
              {answer.toolCalls.map((call, index) => (
                <li key={index} data-testid="tool-call" data-error={call.isError ? "true" : "false"}>
                  {call.name}
                  {call.isError && " (tool error)"}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
