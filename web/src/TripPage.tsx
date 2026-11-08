import { useEffect, useRef, useState } from "react";
import { POLL_MS, download, failure, request, requestFull, type ApiFailure } from "./api";
import { Documents, ItinerarySection, Participants, SearchPanel, TripSummary, type Review } from "./sections";
import { bodyOf, type TripValues } from "./TripForm";
import type { Attempt, Doc, Item, Itinerary, Leg, Offer, Trip } from "./types";
import { fieldErrors, noticeOf, pollNotice, type FieldErrors, type Notify } from "./ui";

const OPEN = ["pending", "unknown"];
// A 409 on Confirm booking that says the terms the user reviewed are no longer the stored ones.
const TERMS_CHANGED = ["TERMS_CHANGED", "OFFER_CHANGED"];
const documentOpen = (doc: Doc) => doc.state === "waiting" || doc.state === "in_progress";

interface Handlers {
  fields?: (errors: FieldErrors) => void;
  /** Called with `details.current` on a 409 that carries one: the view the server says is current. */
  conflict?: (current: unknown) => void;
  onError?: (error: ApiFailure) => void;
  /** Disable the write buttons until this action ends. */
  busy?: boolean;
  /** Leave the notice on screen when the action starts: a follow-up of an action that just failed. */
  keepNotice?: boolean;
}

export function TripPage({ tripId, identity, notify }: { tripId: string; identity: string; notify: Notify }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [tripPending, setTripPending] = useState(true);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [legs, setLegs] = useState<Leg[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [tripErrors, setTripErrors] = useState<FieldErrors>({});
  const [participantErrors, setParticipantErrors] = useState<FieldErrors>({});
  const [searchErrors, setSearchErrors] = useState<FieldErrors>({});
  const [itineraryErrors, setItineraryErrors] = useState<FieldErrors>({});

  // A response that arrives after the identity or the trip changed is dropped.
  const generation = useRef(0);
  // What the polling timer reads: its closure is from an older render.
  const latest = useRef<{ itinerary: Itinerary | null; docs: Doc[] | null }>({ itinerary, docs });
  latest.current = { itinerary, docs };

  // The id comes from the hash, which anyone can edit: it is encoded in every path built from it.
  const base = `/trips/${encodeURIComponent(tripId)}`;
  const callFull = <T,>(method: string, path: string, body?: unknown) => requestFull<T>(identity, method, `${base}${path}`, body);
  const call = <T,>(method: string, path: string, body?: unknown) => request<T>(identity, method, `${base}${path}`, body);

  async function read<T>(path: string, set: (value: T) => void, poll = false) {
    const mine = generation.current;
    try {
      const value = await call<T>("GET", path);
      if (mine !== generation.current) return;
      set(value);
      notify((current) => (current && current.kind !== "saved" && current.poll ? null : current));
    } catch (error) {
      if (mine !== generation.current) return;
      const f = failure(error);
      notify(poll ? pollNotice(f) : noticeOf(f));
    }
  }

  async function loadTrip() {
    const mine = generation.current;
    await read<Trip>("", setTrip);
    if (mine === generation.current) setTripPending(false); // loaded or failed: "Loading..." ends either way
  }
  const loadItinerary = (poll = false) => read<Itinerary>("/itinerary", setItinerary, poll);
  const loadDocs = (poll = false) => read<{ documents: Doc[] }>("/documents", (body) => setDocs(body.documents), poll);
  const loadAll = () => {
    void loadTrip();
    void loadItinerary();
    void loadDocs();
  };

  /** The newest attempt of an item. A changed state also moves the budget and the price kind, so the itinerary is read again. */
  function applyAttempt(attempt: Attempt) {
    const before = latest.current.itinerary?.items.find((item) => item.id === attempt.itemId)?.booking;
    setItinerary((current) =>
      current && { ...current, items: current.items.map((item) => (item.id === attempt.itemId ? { ...item, booking: attempt } : item)) },
    );
    if (before?.id !== attempt.id || before.state !== attempt.state) void loadItinerary();
  }

  async function poll() {
    const { itinerary: current, docs: documents } = latest.current;
    const open = current?.items.filter((item) => item.booking && OPEN.includes(item.booking.state)) ?? [];
    await Promise.all([
      ...open.map((item) => read<Attempt>(`/itinerary/items/${item.id}/booking`, applyAttempt, true)),
      documents?.some(documentOpen) ? loadDocs(true) : undefined,
    ]);
  }

  // On load, on identity change and on focus everything is read again: the view recovers without any notification.
  useEffect(() => {
    generation.current++;
    setTrip(null);
    setTripPending(true);
    setItinerary(null);
    setDocs(null);
    setLegs(null);
    setReview(null);
    loadAll();
    window.addEventListener("focus", loadAll);
    return () => {
      window.removeEventListener("focus", loadAll);
      generation.current++;
    };
  }, [identity, tripId]);

  const pollKey = [
    ...(itinerary?.items.filter((item) => item.booking && OPEN.includes(item.booking.state)).map((item) => item.id) ?? []),
    docs?.some(documentOpen) ? "documents" : "",
  ].join();
  useEffect(() => {
    if (!pollKey) return; // nothing is open: no timer
    const timer = setInterval(poll, POLL_MS);
    return () => clearInterval(timer);
  }, [identity, tripId, pollKey]);

  /** Runs one user action: clears the notice, and turns a failure into the one alert, with its field errors and 409 view. */
  async function act<T>(run: () => Promise<T>, on: Handlers = {}): Promise<T | undefined> {
    if (!on.keepNotice) notify(null);
    on.fields?.({});
    if (on.busy) setBusy(true);
    try {
      return await run();
    } catch (error) {
      const f = failure(error);
      on.fields?.(fieldErrors(f));
      const current = f.details?.current;
      if (on.conflict && f.status === 409 && (f.code === "VERSION_CONFLICT" || current !== undefined)) {
        if (current !== undefined) on.conflict(current);
        notify(noticeOf(f, "conflict"));
      } else {
        notify(noticeOf(f));
      }
      on.onError?.(f);
      return undefined;
    } finally {
      if (on.busy) setBusy(false);
    }
  }

  // A write of the trip or of the itinerary bumps the one version, so the other view is read again to send it next time.
  const tripConflict: Handlers = {
    fields: setTripErrors,
    busy: true,
    conflict: (current) => {
      setTrip(current as Trip);
      void loadItinerary();
    },
  };
  const itineraryWrite = (run: () => Promise<Itinerary>, fields?: (errors: FieldErrors) => void) =>
    act(
      async () => {
        setItinerary(await run());
        void loadTrip();
        notify({ kind: "saved" });
      },
      {
        fields,
        busy: true,
        conflict: (current) => {
          setItinerary(current as Itinerary);
          void loadTrip();
        },
      },
    );

  /** `typed` holds only the fields the user changed: an edit never resends the others, so it cannot revert someone else's change. */
  async function saveTrip(_values: TripValues, typed: Partial<TripValues>) {
    if (!trip) return false;
    const saved = await act(async () => {
      setTrip(await call<Trip>("PATCH", "", { version: trip.version, ...bodyOf(typed) }));
      void loadItinerary();
      notify({ kind: "saved" });
      return true;
    }, tripConflict);
    return saved === true;
  }

  const participantWrite = (run: () => Promise<unknown>) =>
    act(async () => {
      await run();
      void loadTrip();
    }, { fields: setParticipantErrors });

  const search = (category: string, origin: string) =>
    act(async () => {
      setLegs(null);
      setSearching(true);
      try {
        setLegs((await call<{ legs: Leg[] }>("POST", "/search", { category, origin: origin.trim() || undefined })).legs);
      } finally {
        setSearching(false);
      }
    }, { fields: setSearchErrors });

  const addOffer = (offer: Offer) =>
    itinerary && itineraryWrite(() => call<Itinerary>("POST", "/itinerary/items", { version: itinerary.version, offerId: offer.id }));
  const removeItem = (item: Item) =>
    itinerary && itineraryWrite(() => call<Itinerary>("DELETE", `/itinerary/items/${item.id}?version=${itinerary.version}`));
  const moveItem = (index: number, by: -1 | 1) => {
    if (!itinerary) return;
    const ids = itinerary.items.map((item) => item.id);
    [ids[index], ids[index + by]] = [ids[index + by], ids[index]];
    void itineraryWrite(() => call<Itinerary>("PUT", "/itinerary/order", { version: itinerary.version, itemIds: ids }), setItineraryErrors);
  };

  // "Book" never books: it refreshes the terms, and the user reviews them first. `reviewed` is the review this one
  // replaces after a TERMS_CHANGED: the alert stays on screen, and the change notice compares with what the user saw.
  const startBooking = (item: Item, reviewed?: Offer) =>
    itinerary &&
    act(async () => {
      const next = await call<Itinerary>("POST", `/itinerary/items/${item.id}/refresh`, { version: itinerary.version });
      setItinerary(next);
      void loadTrip();
      const before = reviewed ?? item.offer;
      const offer = next.items.find((entry) => entry.id === item.id)?.offer ?? item.offer;
      setReview({
        itemId: item.id,
        offer,
        changed: offer.price.amount !== before.price.amount || offer.revision !== before.revision,
      });
    }, {
      keepNotice: reviewed !== undefined,
      conflict: (current) => {
        setItinerary(current as Itinerary);
        void loadTrip();
      },
    });

  const afterBookingError = () => {
    setReview(null);
    void loadItinerary();
  };
  const confirmBooking = () =>
    review &&
    act(async () => {
      applyAttempt(
        await call<Attempt>("POST", `/itinerary/items/${review.itemId}/booking`, {
          expectedRevision: review.offer.revision,
          expectedPriceUsd: review.offer.price.amount,
        }),
      );
      setReview(null);
    }, {
      busy: true,
      onError: (error) => {
        const item = itinerary?.items.find((entry) => entry.id === review.itemId);
        if (item && TERMS_CHANGED.includes(error.code)) {
          // The alert says why; the review opens again on the terms the server now holds.
          setReview(null);
          void startBooking(item, review.offer);
        } else {
          afterBookingError();
        }
      },
    });
  const retryBooking = (item: Item) =>
    act(async () => applyAttempt(await call<Attempt>("POST", `/itinerary/items/${item.id}/booking/retry`, {})), { busy: true, onError: afterBookingError });

  const generate = () =>
    act(async () => {
      const { status } = await callFull("POST", "/documents");
      await loadDocs();
      if (status === 200) notify({ kind: "saved", message: "This trip state already has a document." });
    }, { busy: true });
  const retryDocument = (doc: Doc) =>
    act(async () => {
      await call("POST", `/documents/${doc.id}/retry`);
      await loadDocs();
    }, { busy: true, onError: () => void loadDocs() });
  const downloadDocument = (doc: Doc) =>
    act(() => download(identity, `${base}/documents/${doc.id}/content`, `document-${doc.id}.html`), { onError: () => void loadDocs() });

  const canWrite = trip?.role === "organizer" || trip?.role === "editor";
  return (
    <>
      <p><a href="#/">Back to trips</a></p>
      {!trip && tripPending && <p>Loading...</p>}
      {trip && <TripSummary trip={trip} errors={tripErrors} busy={busy} onSave={saveTrip} />}
      {trip?.role === "organizer" && (
        <Participants
          trip={trip}
          errors={participantErrors}
          onAdd={(userId, role) => participantWrite(() => call("POST", "/participants", { userId, role }))}
          onRole={(userId, role) => participantWrite(() => call("PATCH", `/participants/${userId}`, { role }))}
          onRemove={(userId) => participantWrite(() => call("DELETE", `/participants/${userId}`))}
        />
      )}
      {trip && (
        <SearchPanel
          legs={legs}
          searching={searching}
          errors={searchErrors}
          offerIds={new Set(itinerary?.items.map((item) => item.offer.id))}
          canWrite={canWrite}
          busy={busy}
          onSearch={search}
          onAdd={addOffer}
        />
      )}
      {trip && itinerary && (
        <ItinerarySection
          itinerary={itinerary}
          canWrite={canWrite}
          busy={busy}
          errors={itineraryErrors}
          review={review}
          onMove={moveItem}
          onRemove={removeItem}
          onBook={startBooking}
          onRetry={retryBooking}
          onConfirm={confirmBooking}
          onCancel={() => setReview(null)}
        />
      )}
      {trip && <Documents docs={docs} canWrite={canWrite} busy={busy} onGenerate={generate} onDownload={downloadDocument} onRetry={retryDocument} />}
    </>
  );
}
