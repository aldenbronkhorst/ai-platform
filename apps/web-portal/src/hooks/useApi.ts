export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  "http://localhost:8000";

const READ_REQUEST_TIMEOUT_MS = 15_000;

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
