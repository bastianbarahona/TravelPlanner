import { useState } from "react";
import type { Trip } from "./types";
import { Field, OtherErrors, type FieldErrors } from "./ui";

export interface TripValues {
  destinations: string;
  startsOn: string;
  endsOn: string;
  budgetUsd: string;
  travelers: string;
}

export const EMPTY: TripValues = { destinations: "", startsOn: "", endsOn: "", budgetUsd: "", travelers: "" };

export const valuesOf = (trip: Trip): TripValues => ({
  destinations: trip.destinations.join(", "),
  startsOn: trip.startsOn,
  endsOn: trip.endsOn,
  budgetUsd: trip.budgetUsd,
  travelers: String(trip.travelers),
});

/**
 * The request body for the fields given: create sends all five, an edit only the ones the user typed. Nothing is
 * validated here: the server is the authority, and its 422 is shown next to the field.
 */
export function bodyOf(v: Partial<TripValues>) {
  const body: Record<string, unknown> = {};
  if (v.destinations !== undefined) body.destinations = v.destinations.split(",").map((name) => name.trim()).filter(Boolean);
  if (v.startsOn !== undefined) body.startsOn = v.startsOn;
  if (v.endsOn !== undefined) body.endsOn = v.endsOn;
  if (v.budgetUsd !== undefined) body.budgetUsd = v.budgetUsd;
  if (v.travelers !== undefined) body.travelers = v.travelers === "" ? null : Number(v.travelers);
  return body;
}

const FIELDS = ["destinations", "startsOn", "endsOn", "budgetUsd", "travelers"];

/**
 * Create and edit share it. A field shows what the user typed, else what the server last said, so a 409 never erases
 * the user's input and never freezes the fields they did not touch: `onSubmit` also gets the typed fields alone, which
 * is all an edit sends (a second Save must not revert another user's change). Typing is forgotten after a save.
 */
export function TripForm({ initial, submitLabel, errors, busy = false, onSubmit }: {
  initial: TripValues;
  submitLabel: string;
  errors: FieldErrors;
  busy?: boolean;
  onSubmit: (values: TripValues, typed: Partial<TripValues>) => Promise<boolean>;
}) {
  const [typed, setTyped] = useState<Partial<TripValues>>({});
  const values = { ...initial, ...typed };
  const bind = (name: keyof TripValues) => ({
    value: values[name],
    onChange: (event: { target: { value: string } }) => {
      const { value } = event.target;
      setTyped((current) => ({ ...current, [name]: value }));
    },
  });
  return (
    <form
      onSubmit={async (event) => {
        event.preventDefault();
        if (await onSubmit(values, typed)) setTyped({});
      }}
    >
      <Field id="trip-destinations" label="Destinations" name="destinations" errors={errors} hint="Separate with commas">
        {(control) => <input {...control} {...bind("destinations")} />}
      </Field>
      <Field id="trip-startsOn" label="Start date" name="startsOn" errors={errors}>
        {(control) => <input type="date" {...control} {...bind("startsOn")} />}
      </Field>
      <Field id="trip-endsOn" label="End date" name="endsOn" errors={errors}>
        {(control) => <input type="date" {...control} {...bind("endsOn")} />}
      </Field>
      <Field id="trip-budgetUsd" label="Budget (USD)" name="budgetUsd" errors={errors}>
        {(control) => <input inputMode="decimal" {...control} {...bind("budgetUsd")} />}
      </Field>
      <Field id="trip-travelers" label="Travelers" name="travelers" errors={errors}>
        {(control) => <input type="number" {...control} {...bind("travelers")} />}
      </Field>
      <OtherErrors errors={errors} known={FIELDS} />
      <button type="submit" disabled={busy}>{submitLabel}</button>
    </form>
  );
}
