export const APIM_BASE_URL =
  import.meta.env.VITE_APIM_BASE_URL ||
  "https://apim-ai-platform-prod-san-001.azure-api.net";

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
