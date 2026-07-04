import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMsal } from "@azure/msal-react";
import { loginRequest } from "../authConfig";
import type { AccessTokenGetter } from "./useApi";
import {
  clearStoredAuthHint,
  markPromptlessRestoreAttempted,
  promptlessLoginRequest,
  readStoredAuthHint,
  rememberAuthAccount,
  shouldAttemptPromptlessRestore,
} from "../authSession";
import type { UserProfile } from "../types";

interface TokenClaims {
  roles?: string[];
}

interface TokenResult {
  accountId: string;
  accessToken: string;
  expiresAtMs: number | null;
}

const TOKEN_REFRESH_WINDOW_MS = 5 * 60 * 1000;
const TOKEN_REFRESH_INTERVAL_MS = 2 * 60 * 1000;

function clearMsalStorage(storage: Storage) {
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
    .filter((key): key is string => typeof key === "string" && key.startsWith("msal."));
  keys.forEach(key => storage.removeItem(key));
}

function tokenUsableForAccount(tokenResult: TokenResult | null, accountId: string) {
  if (!tokenResult || tokenResult.accountId !== accountId || !tokenResult.accessToken) return false;
  if (!tokenResult.expiresAtMs) return true;
  return tokenResult.expiresAtMs - Date.now() > TOKEN_REFRESH_WINDOW_MS;
}

export function usePortalAuth() {
  const { instance, accounts, inProgress } = useMsal();
  const [authError, setAuthError] = useState<string | null>(null);
  const [isTokenLoading, setIsTokenLoading] = useState(false);
  const [tokenResult, setTokenResult] = useState<TokenResult | null>(null);
  const tokenResultRef = useRef<TokenResult | null>(null);
  const activeAccountRef = useRef<typeof accounts[number] | null>(null);
  const inProgressRef = useRef(inProgress);

  const activeAccount = useMemo(
    () => instance.getActiveAccount() || (accounts.length > 0 ? accounts[0] : null),
    [accounts, instance],
  );

  const activeUser = useMemo<UserProfile | null>(() => {
    if (activeAccount) {
      const idTokenClaims = activeAccount.idTokenClaims as TokenClaims | undefined;
      return {
        email: activeAccount.username,
        displayName: activeAccount.name || activeAccount.username,
        roles: idTokenClaims?.roles || ["AIPlatform.User"],
      };
    }
    return null;
  }, [activeAccount]);

  const accessToken = useMemo(() => {
    if (!activeAccount || tokenResult?.accountId !== activeAccount.homeAccountId) {
      return "";
    }
    return tokenResult.accessToken;
  }, [activeAccount, tokenResult]);

  useEffect(() => {
    tokenResultRef.current = tokenResult;
  }, [tokenResult]);

  useEffect(() => {
    activeAccountRef.current = activeAccount;
  }, [activeAccount]);

  useEffect(() => {
    inProgressRef.current = inProgress;
  }, [inProgress]);

  const getAccessToken = useCallback<AccessTokenGetter>(async (options = {}) => {
    const account = activeAccountRef.current;
    if (!account) return null;

    const cached = tokenResultRef.current;
    if (!options.forceRefresh && tokenUsableForAccount(cached, account.homeAccountId)) {
      return cached?.accessToken || null;
    }

    if (options.redirectOnFailure) setIsTokenLoading(true);
    try {
      const response = await instance.acquireTokenSilent({
        ...loginRequest,
        account,
        forceRefresh: Boolean(options.forceRefresh),
      });
      rememberAuthAccount(response.account || account);
      const nextToken = {
        accountId: account.homeAccountId,
        accessToken: response.accessToken,
        expiresAtMs: response.expiresOn?.getTime() ?? null,
      };
      tokenResultRef.current = nextToken;
      setTokenResult(nextToken);
      setAuthError(null);
      return response.accessToken;
    } catch (error) {
      const stillUsable = tokenUsableForAccount(tokenResultRef.current, account.homeAccountId);
      if (!stillUsable) setTokenResult(null);
      setAuthError("Token acquisition failed. Please sign in again.");
      if (
        options.redirectOnFailure &&
        inProgressRef.current === "none" &&
        shouldAttemptPromptlessRestore(account)
      ) {
        markPromptlessRestoreAttempted(account);
        instance.acquireTokenRedirect(promptlessLoginRequest(loginRequest, account)).catch(() => {
          setAuthError(
            error instanceof Error ? error.message : "Token acquisition failed. Please sign in again.",
          );
        });
      }
      return stillUsable ? tokenResultRef.current?.accessToken || null : null;
    } finally {
      if (options.redirectOnFailure) setIsTokenLoading(false);
    }
  }, [instance]);

  useEffect(() => {
    if (!activeAccount) {
      const storedHint = readStoredAuthHint();
      if (
        inProgress === "none" &&
        storedHint &&
        shouldAttemptPromptlessRestore(storedHint)
      ) {
        markPromptlessRestoreAttempted(storedHint);
        instance.loginRedirect(promptlessLoginRequest(loginRequest, storedHint)).catch(error => {
          setAuthError(error instanceof Error ? error.message : "Microsoft session restore failed.");
        });
        return;
      }
      const timerId = window.setTimeout(() => {
        setTokenResult(null);
        setIsTokenLoading(false);
        setAuthError(null);
      }, 0);
      return () => window.clearTimeout(timerId);
    }

    let cancelled = false;
    const acquireToken = (showLoading: boolean) => {
      if (cancelled) return;
      void getAccessToken({ redirectOnFailure: showLoading });
    };

    acquireToken(true);
    const refreshInterval = window.setInterval(() => acquireToken(false), TOKEN_REFRESH_INTERVAL_MS);
    const refreshWhenActive = () => {
      if (document.visibilityState === "visible") acquireToken(false);
    };
    window.addEventListener("focus", refreshWhenActive);
    document.addEventListener("visibilitychange", refreshWhenActive);
    return () => {
      cancelled = true;
      window.clearInterval(refreshInterval);
      window.removeEventListener("focus", refreshWhenActive);
      document.removeEventListener("visibilitychange", refreshWhenActive);
    };
  }, [activeAccount, getAccessToken, inProgress, instance]);

  const signOut = useCallback(async () => {
    instance.setActiveAccount(null);
    try {
      await instance.clearCache();
    } catch {
      // Fall back to explicit browser cache cleanup below.
    } finally {
      clearStoredAuthHint();
      clearMsalStorage(sessionStorage);
      clearMsalStorage(localStorage);
      window.location.href = "/";
    }
  }, [instance]);

  return {
    accessToken,
    accounts,
    activeUser,
    authError,
    getAccessToken,
    inProgress,
    instance,
    isTokenLoading,
    signOut,
  };
}
