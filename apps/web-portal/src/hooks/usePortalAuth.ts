import { useCallback, useEffect, useMemo, useState } from "react";
import { useMsal } from "@azure/msal-react";
import { loginRequest } from "../authConfig";
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
}

function clearMsalStorage(storage: Storage) {
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index))
    .filter((key): key is string => typeof key === "string" && key.startsWith("msal."));
  keys.forEach(key => storage.removeItem(key));
}

export function usePortalAuth() {
  const { instance, accounts, inProgress } = useMsal();
  const [authError, setAuthError] = useState<string | null>(null);
  const [isTokenLoading, setIsTokenLoading] = useState(false);
  const [tokenResult, setTokenResult] = useState<TokenResult | null>(null);

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
      if (showLoading) setIsTokenLoading(true);
      instance.acquireTokenSilent({ ...loginRequest, account: activeAccount })
        .then(response => {
          if (cancelled) return;
          rememberAuthAccount(response.account || activeAccount);
          setTokenResult({ accountId: activeAccount.homeAccountId, accessToken: response.accessToken });
          setAuthError(null);
        })
        .catch(error => {
          if (!cancelled) {
            if (showLoading) setTokenResult(null);
            setAuthError("Token acquisition failed. Please sign in again.");
            if (
              showLoading &&
              inProgress === "none" &&
              shouldAttemptPromptlessRestore(activeAccount)
            ) {
              markPromptlessRestoreAttempted(activeAccount);
              instance.acquireTokenRedirect(promptlessLoginRequest(loginRequest, activeAccount)).catch(() => {
                setAuthError(
                  error instanceof Error ? error.message : "Token acquisition failed. Please sign in again.",
                );
              });
            }
          }
        })
        .finally(() => {
          if (!cancelled && showLoading) setIsTokenLoading(false);
        });
    };

    acquireToken(true);
    const refreshInterval = window.setInterval(() => acquireToken(false), 30 * 60 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(refreshInterval);
    };
  }, [activeAccount, inProgress, instance]);

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
    inProgress,
    instance,
    isTokenLoading,
    signOut,
  };
}
