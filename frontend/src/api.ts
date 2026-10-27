export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Access token lives in module memory only: never persisted, exported,
// placed in a URL/body, logged, or sent anywhere except as an
// Authorization header on same-origin /api/ paths.
let accessToken: string | null = null;
let sessionGeneration = 0;
let unauthorizedCallback: (() => void) | null = null;

// Server access keys are ASCII URL-safe tokens of 32..128 characters
// (URL-safe base64 alphabet: letters, digits, '-' and '_').
// The explicit length check plus negated forbidden-character test
// rejects every other character anywhere in the string, including a
// final newline (which a $-anchored regex could otherwise allow),
// CR, spaces, and non-ASCII characters.
export function isValidAccessKeyShape(key: string): boolean {
  return (
    typeof key === "string" &&
    key.length >= 32 &&
    key.length <= 128 &&
    !/[^A-Za-z0-9_-]/.test(key)
  );
}

export function setAccessToken(token: string): void {
  sessionGeneration += 1;
  accessToken = token;
}

export function clearAccessToken(): void {
  sessionGeneration += 1;
  accessToken = null;
}

export function registerUnauthorizedCallback(callback: () => void): () => void {
  unauthorizedCallback = callback;
  return () => {
    if (unauthorizedCallback === callback) unauthorizedCallback = null;
  };
}

function friendly(status: number): string {
  if (status === 401) return "Sign-in required. Your access key was not accepted or your session has ended — please sign in again.";
  if (status === 403) return "This account is not permitted to perform that action.";
  if (status === 404) return "The requested record was not found.";
  if (status === 408) return "The request timed out. Please try again; nothing was automatically retried.";
  if (status === 409) return "The underlying data changed since this preview. This approval is no longer valid — run a fresh investigation before continuing.";
  if (status === 413) return "That request was too large to be accepted.";
  if (status === 422) return "That request could not be accepted. Review the current state and try again.";
  if (status === 429) return "Too many requests right now. Please wait before trying again — nothing was automatically retried or spent again.";
  if (status === 503) return "The service is currently unavailable or not configured. Your evidence is still available; please try again later.";
  if (status >= 500) return "Something went wrong while contacting the local service. Please try again.";
  return "The request could not be completed.";
}

const STALE = "Your session changed while a request was in progress, so its result was discarded.";
const NOT_PERMITTED = "That request path is not permitted.";
const NOT_PREPARED = "The request could not be prepared. Please try again.";

// Constrain every request to a same-origin /api/ URL. The origin is
// resolved here, during the request, never at module initialization,
// so importing this module does not require a browser environment.
// Dot-segment escapes (including encoded forms, which URL parsing
// normalizes), backslashes, absolute and protocol-relative paths are
// rejected before any token can be attached.
function validateApiPath(path: string): string {
  if (
    typeof path !== "string" ||
    path.length === 0 ||
    path.includes("\\") ||
    /[\u0000-\u0020\u007f]/.test(path) ||
    !path.startsWith("/api/")
  ) {
    throw new ApiError(0, NOT_PERMITTED);
  }
  const rawPath = path.split(/[?#]/, 1)[0];
  if (rawPath.split("/").some((segment) => segment === "." || segment === "..")) {
    throw new ApiError(0, NOT_PERMITTED);
  }
  const baseOrigin =
    typeof window !== "undefined" && window.location && window.location.origin && window.location.origin !== "null"
      ? window.location.origin
      : "http://localhost";
  let url: URL;
  let expectedOrigin: string;
  try {
    expectedOrigin = new URL(baseOrigin).origin;
    url = new URL(path, expectedOrigin);
  } catch {
    throw new ApiError(0, NOT_PERMITTED);
  }
  if (url.origin !== expectedOrigin || !url.pathname.startsWith("/api/")) {
    throw new ApiError(0, NOT_PERMITTED);
  }
  // Fetch the original relative path so the request stays same-origin.
  return path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const safePath = validateApiPath(path);
  const generation = sessionGeneration;
  const token = accessToken;
  // Authorization is owned by this module and cannot be overridden by callers.
  // Header construction or setting Authorization could throw an error
  // whose text includes a credential-bearing value, so any such failure
  // is converted to a generic ApiError without interpolating values.
  let headers: Headers;
  try {
    headers = new Headers(init?.headers ?? {});
    headers.delete("Authorization");
    headers.delete("authorization");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init?.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  } catch {
    if (generation !== sessionGeneration) throw new ApiError(0, STALE);
    throw new ApiError(0, NOT_PREPARED);
  }
  let res: Response;
  try {
    // redirect:'error' (forced after init so callers cannot override it)
    // prevents the Authorization header being carried through a redirect.
    res = await fetch(safePath, { ...init, headers, redirect: "error" });
  } catch {
    if (generation !== sessionGeneration) throw new ApiError(0, STALE);
    throw new ApiError(0, "Cannot reach the local AfterFix service. Check that it is running, then try again.");
  }
  if (generation !== sessionGeneration) throw new ApiError(0, STALE);
  let text: string;
  try {
    text = await res.text();
  } catch {
    if (generation !== sessionGeneration) throw new ApiError(0, STALE);
    throw new ApiError(0, "The local service returned an unreadable response.");
  }
  // Refuse to deliver a stale response after a token/session change.
  if (generation !== sessionGeneration) throw new ApiError(0, STALE);
  if (!res.ok) {
    if (res.status === 401 && generation === sessionGeneration) {
      // Only a 401 belonging to the current session ends that session;
      // a stale 401 from an older request must not sign out a newer one.
      clearAccessToken();
      const callback = unauthorizedCallback;
      if (callback) callback();
    }
    // Server bodies are never displayed raw.
    throw new ApiError(res.status, friendly(res.status));
  }
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch {
    throw new ApiError(0, "The local service returned an unreadable response.");
  }
  if (generation !== sessionGeneration) throw new ApiError(0, STALE);
  return data as T;
}

export const apiGet = <T,>(path: string) => request<T>(path);
export const apiPost = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
