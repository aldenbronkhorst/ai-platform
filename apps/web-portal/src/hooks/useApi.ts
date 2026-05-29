const APIM_BASE_URL =
  import.meta.env.VITE_APIM_BASE_URL ||
  "https://apim-ai-platform-prod-san-001.azure-api.net";

export function getHeaders(accessToken: string) {
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };
}

export function apiFetch<T = any>(
  path: string,
  accessToken: string,
  options?: RequestInit
): Promise<T> {
  return fetch(`${APIM_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...getHeaders(accessToken),
      ...(options?.headers as Record<string, string>),
    },
  }).then(async (res) => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  });
}

export { APIM_BASE_URL };
