import type { AccountInfo, RedirectRequest } from "@azure/msal-browser";

const AUTH_HINT_KEY = "ai-platform.auth.lastAccount";
const AUTH_HINT_COOKIE = "ai_platform_last_account";
const RESTORE_ATTEMPT_PREFIX = "ai-platform.auth.promptlessRestore.";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90;

export interface StoredAuthHint {
  username: string;
  homeAccountId?: string;
  localAccountId?: string;
  name?: string;
  savedAt: number;
}

function accountKey(value: Pick<StoredAuthHint, "username" | "homeAccountId" | "localAccountId">) {
  return (value.homeAccountId || value.localAccountId || value.username).toLowerCase();
}

function safeJsonParse(value: string | null): StoredAuthHint | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<StoredAuthHint>;
    if (!parsed.username || typeof parsed.username !== "string") return null;
    return {
      username: parsed.username,
      homeAccountId: typeof parsed.homeAccountId === "string" ? parsed.homeAccountId : undefined,
      localAccountId: typeof parsed.localAccountId === "string" ? parsed.localAccountId : undefined,
      name: typeof parsed.name === "string" ? parsed.name : undefined,
      savedAt: typeof parsed.savedAt === "number" ? parsed.savedAt : Date.now(),
    };
  } catch {
    return null;
  }
}

function getCookieValue(name: string) {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  return document.cookie
    .split(";")
    .map(cookie => cookie.trim())
    .find(cookie => cookie.startsWith(prefix))
    ?.slice(prefix.length) || null;
}

function writeCookie(name: string, value: string) {
  if (typeof document === "undefined") return;
  document.cookie = [
    `${name}=${encodeURIComponent(value)}`,
    `Max-Age=${COOKIE_MAX_AGE_SECONDS}`,
    "Path=/",
    "SameSite=Lax",
    "Secure",
  ].join("; ");
}

function clearCookie(name: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax; Secure`;
}

export function rememberAuthAccount(account: AccountInfo | null | undefined) {
  if (!account?.username) return;
  const hint: StoredAuthHint = {
    username: account.username,
    homeAccountId: account.homeAccountId,
    localAccountId: account.localAccountId,
    name: account.name,
    savedAt: Date.now(),
  };
  const serialized = JSON.stringify(hint);
  try {
    window.localStorage.setItem(AUTH_HINT_KEY, serialized);
  } catch {
    // Browser privacy/storage policies can deny localStorage in installed web apps.
  }
  writeCookie(AUTH_HINT_COOKIE, serialized);
}

export function readStoredAuthHint() {
  try {
    const hint = safeJsonParse(window.localStorage.getItem(AUTH_HINT_KEY));
    if (hint) return hint;
  } catch {
    // Fall back to the first-party cookie below.
  }

  const cookieValue = getCookieValue(AUTH_HINT_COOKIE);
  if (!cookieValue) return null;
  try {
    return safeJsonParse(decodeURIComponent(cookieValue));
  } catch {
    return null;
  }
}

export function clearStoredAuthHint() {
  try {
    window.localStorage.removeItem(AUTH_HINT_KEY);
  } catch {
    // Ignore storage cleanup failures; the MSAL cache clear remains authoritative.
  }
  clearCookie(AUTH_HINT_COOKIE);
}

export function loginRequestWithAuthHint(
  request: RedirectRequest,
  hint: AccountInfo | StoredAuthHint | null | undefined,
) {
  const username = hint?.username;
  return username ? { ...request, loginHint: username } : request;
}

export function promptlessLoginRequest(
  request: RedirectRequest,
  hint: AccountInfo | StoredAuthHint,
) {
  return {
    ...loginRequestWithAuthHint(request, hint),
    prompt: "none",
  };
}

export function shouldAttemptPromptlessRestore(hint: AccountInfo | StoredAuthHint) {
  if (typeof window === "undefined") return false;
  const key = `${RESTORE_ATTEMPT_PREFIX}${accountKey(hint)}`;
  try {
    return window.sessionStorage.getItem(key) !== "1";
  } catch {
    return true;
  }
}

export function markPromptlessRestoreAttempted(hint: AccountInfo | StoredAuthHint) {
  if (typeof window === "undefined") return;
  const key = `${RESTORE_ATTEMPT_PREFIX}${accountKey(hint)}`;
  try {
    window.sessionStorage.setItem(key, "1");
  } catch {
    // If sessionStorage is unavailable, MSAL will still guard concurrent interactions.
  }
}
