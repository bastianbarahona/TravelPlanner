// Every request goes to /api (nginx and the Vite dev server strip the prefix) with the identity's bearer token.
// ponytail: the identity list mirrors APP_TOKENS' compose default (token-<name>). A real identity provider replaces it.
export const IDENTITIES = ["ana", "ben", "cam", "dee"];

/** How often an open booking or document is read again (a decision of the brief). */
export const POLL_MS = 3000;
const TIMEOUT_MS = 30_000;

/** What every failure becomes: the server's own {code, message, requestId, details}, or a network failure (status 0). */
export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
    readonly details?: { errors?: { field: string; message: string }[]; current?: unknown },
    /** An extra line the alert shows under the message. */
    readonly hint?: string,
  ) {
    super(message);
  }
}

export function failure(error: unknown): ApiFailure {
  return error instanceof ApiFailure
    ? error
    : new ApiFailure(0, "CLIENT_ERROR", error instanceof Error ? error.message : String(error));
}

async function send(identity: string, method: string, path: string, body?: unknown): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `Bearer token-${identity}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    // A network failure or a timeout proves nothing about what the server did: it is never shown as a success.
    throw new ApiFailure(
      0,
      "NETWORK",
      "Could not reach the server",
      undefined,
      undefined,
      method === "GET" ? undefined : "The request may have been received. Reload to check before repeating.",
    );
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => undefined);
    throw new ApiFailure(
      response.status,
      data?.code ?? "HTTP_ERROR",
      data?.message ?? `Unexpected response from the server (HTTP ${response.status})`,
      data?.requestId ?? response.headers?.get?.("x-request-id") ?? undefined,
      data?.details,
    );
  }
  return response;
}

export async function requestFull<T>(identity: string, method: string, path: string, body?: unknown) {
  const response = await send(identity, method, path, body);
  return { status: response.status, data: (await response.json().catch(() => undefined)) as T }; // a 204 has no body
}

export async function request<T>(identity: string, method: string, path: string, body?: unknown): Promise<T> {
  return (await requestFull<T>(identity, method, path, body)).data;
}

/** A download is a fetch, then a blob: an <a href> cannot send the Authorization header. */
export async function download(identity: string, path: string, fallbackName: string) {
  const response = await send(identity, "GET", path);
  const named = /filename="?([^";]+)"?/.exec(response.headers?.get?.("content-disposition") ?? "");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = named?.[1] ?? fallbackName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000); // the browser may still be reading it
}
