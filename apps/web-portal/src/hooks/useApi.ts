export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  "http://localhost:8000";

const READ_REQUEST_TIMEOUT_MS = 15_000;

export type AccessTokenGetter = (options?: {
  forceRefresh?: boolean;
  redirectOnFailure?: boolean;
}) => Promise<string | null>;

export function isAbortError(err: unknown) {
  return typeof err === "object" && err !== null && "name" in err
    && (err as { name?: string }).name === "AbortError";
}

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = READ_REQUEST_TIMEOUT_MS,
) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export function withAuthHeaders(
  token: string,
  headers: HeadersInit = {},
  includeJson = false,
) {
  const next = new Headers(headers);
  next.set("Authorization", `Bearer ${token}`);
  if (includeJson && !next.has("Content-Type")) {
    next.set("Content-Type", "application/json");
  }
  return next;
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init: RequestInit = {},
  getAccessToken: AccessTokenGetter,
  options: {
    includeJson?: boolean;
    timeoutMs?: number;
  } = {},
) {
  const token = await getAccessToken();
  if (!token) throw new Error("Microsoft session expired. Please sign in again.");

  const run = (accessToken: string) => fetchWithTimeout(
    input,
    {
      ...init,
      headers: withAuthHeaders(accessToken, init.headers, Boolean(options.includeJson)),
    },
    options.timeoutMs,
  );

  const response = await run(token);
  if (response.status !== 401) return response;

  const freshToken = await getAccessToken({ forceRefresh: true, redirectOnFailure: true });
  if (!freshToken || freshToken === token) return response;
  return await run(freshToken);
}
