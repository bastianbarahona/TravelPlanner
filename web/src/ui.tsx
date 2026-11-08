import { useEffect, useRef, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type { ApiFailure } from "./api";

// One notice at a time, so exactly one role="alert" is on screen. `poll` marks a background failure that the next
// successful read clears. `message` on a saved notice replaces the word "Saved".
export type Notice =
  | { kind: "error" | "conflict"; message: string; requestId?: string; hint?: string; poll?: boolean }
  | { kind: "saved"; message?: string };
export type Notify = Dispatch<SetStateAction<Notice | null>>;
export type FieldErrors = Record<string, string>;

export const noticeOf = (failure: ApiFailure, kind: "error" | "conflict" = "error", poll = false): Notice => ({
  kind,
  message: failure.message,
  requestId: failure.requestId,
  hint: failure.hint,
  poll,
});

/** A background failure never replaces a notice the user is reading, and a repeat of itself changes nothing. */
export const pollNotice = (failure: ApiFailure) => (current: Notice | null): Notice | null =>
  current && current.kind !== "saved" && (!current.poll || current.message === failure.message)
    ? current
    : noticeOf(failure, "error", true);

export function fieldErrors(error: ApiFailure): FieldErrors {
  const result: FieldErrors = {};
  if (error.status === 422) {
    for (const { field, message } of error.details?.errors ?? []) result[field] = result[field] ? `${result[field]} ${message}` : message;
  }
  return result;
}

export function NoticeView({ notice }: { notice: Notice | null }) {
  const shown = useRef<HTMLDivElement>(null);
  // A new notice may be off screen: bring it into view (jsdom has no scrollIntoView).
  useEffect(() => {
    shown.current?.scrollIntoView?.({ block: "nearest" }); // a block body: Chromium returns a Promise, which React would take as a destroy function
  }, [notice]);
  if (!notice) return null;
  if (notice.kind === "saved") {
    return (
      <div ref={shown}>
        <p role="status" data-testid="saved">{notice.message ?? "Saved"}</p>
      </div>
    );
  }
  return (
    <div ref={shown} role="alert" data-testid={notice.kind === "conflict" ? "conflict" : undefined}>
      {notice.kind === "conflict" && <p>Not saved</p>}
      <p>{notice.message}</p>
      {notice.hint && <p>{notice.hint}</p>}
      {notice.kind === "conflict" && <p>The page now shows the latest saved version. Check your changes and save again.</p>}
      {notice.requestId && <p>Request id: {notice.requestId}</p>}
    </div>
  );
}

interface ControlProps {
  id: string;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
}

/** A labelled control with its server-side error (and an optional hint) tied to it for assistive technology. */
export function Field({ id, label, errors, name, hint, children }: {
  id: string;
  label: string;
  errors: FieldErrors;
  name: string;
  hint?: string;
  children: (props: ControlProps) => ReactNode;
}) {
  const failed = Boolean(errors[name]);
  const describedBy = [hint ? `${id}-hint` : "", failed ? `${id}-error` : ""].filter(Boolean).join(" ");
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      {children({ id, "aria-invalid": failed ? true : undefined, "aria-describedby": describedBy || undefined })}
      {hint && <p id={`${id}-hint`}>{hint}</p>}
      <FieldError errors={errors} name={name} id={`${id}-error`} />
    </div>
  );
}

export function FieldError({ errors, name, id }: { errors: FieldErrors; name: string; id?: string }) {
  return errors[name] ? <p id={id} data-testid={`field-error-${name}`}>{errors[name]}</p> : null;
}

/** Errors for fields the form has no control for (for example `body`), so a 422 never disappears. */
export function OtherErrors({ errors, known }: { errors: FieldErrors; known: string[] }) {
  return Object.keys(errors).filter((name) => !known.includes(name)).map((name) => <FieldError key={name} errors={errors} name={name} />);
}
