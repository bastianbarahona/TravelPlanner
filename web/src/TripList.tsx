import { useEffect, useState } from "react";
import { failure, request } from "./api";
import { EMPTY, TripForm, bodyOf, type TripValues } from "./TripForm";
import type { Trip } from "./types";
import { fieldErrors, noticeOf, type FieldErrors, type Notify } from "./ui";

export function TripList({ identity, notify }: { identity: string; notify: Notify }) {
  const [trips, setTrips] = useState<Trip[] | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setTrips((await request<{ trips: Trip[] }>(identity, "GET", "/trips")).trips);
    } catch (error) {
      notify(noticeOf(failure(error)));
    }
  }

  useEffect(() => {
    setTrips(null);
    void load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [identity]);

  async function create(values: TripValues) {
    notify(null);
    setErrors({});
    setBusy(true);
    try {
      const trip = await request<Trip>(identity, "POST", "/trips", bodyOf(values));
      window.location.hash = `#/trips/${trip.id}`;
      return true;
    } catch (error) {
      const f = failure(error);
      setErrors(fieldErrors(f));
      notify(noticeOf(f));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h2>Trips</h2>
      <ul data-testid="trip-list">
        {trips?.map((trip) => (
          <li key={trip.id}>
            <a href={`#/trips/${trip.id}`}>
              {trip.destinations.join(", ")} ({trip.startsOn} to {trip.endsOn})
            </a>
          </li>
        ))}
      </ul>
      {trips?.length === 0 && <p>No trips yet</p>}
      <h2>New trip</h2>
      <TripForm initial={EMPTY} submitLabel="Create trip" errors={errors} busy={busy} onSubmit={create} />
    </>
  );
}
