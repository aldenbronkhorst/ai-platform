import { useCallback, useEffect, useMemo, useState } from "react";
import { useMsal } from "@azure/msal-react";
import { loginRequest } from "../authConfig";
import type { UserProfile } from "../types";

interface TokenClaims {
  roles?: string[];
}

const ENABLE_LOCAL_MOCK =
  import.meta.env.VITE_ENABLE_LOCAL_MOCK_AUTH === "true" &&
  typeof window !== "undefined" &&
  (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1");

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
  const [localMockAuthenticated, setLocalMockAuthenticated] = useState(false);
  const [localMockUser, setLocalMockUser] = useState<UserProfile | null>(null);
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
    if (ENABLE_LOCAL_MOCK && localMockAuthenticated && localMockUser) {
      return localMockUser;
    }
    return null;
  }, [activeAccount, localMockAuthenticated, localMockUser]);

  const accessToken = useMemo(() => {
    if (ENABLE_LOCAL_MOCK && localMockAuthenticated && localMockUser && !activeAccount) {
      return "mock-local-token";
    }
    if (!activeAccount || tokenResult?.accountId !== activeAccount.homeAccountId) {
      return "";
    }
    return tokenResult.accessToken;
  }, [activeAccount, localMockAuthenticated, localMockUser, tokenResult]);

  useEffect(() => {
    if (!activeAccount) {
      const timerId = window.setTimeout(() => {
        setTokenResult(null);
        setIsTokenLoading(false);
        if (!localMockAuthenticated) setAuthError(null);
      }, 0);
      return () => window.clearTimeout(timerId);
    }

    let cancelled = false;
    const acquireToken = (showLoading: boolean) => {
      if (showLoading) setIsTokenLoading(true);
      instance.acquireTokenSilent({ ...loginRequest, account: activeAccount })
        .then(response => {
          if (cancelled) return;
          setTokenResult({ accountId: activeAccount.homeAccountId, accessToken: response.accessToken });
          setAuthError(null);
        })
        .catch(() => {
          if (!cancelled) {
            if (showLoading) setTokenResult(null);
            setAuthError("Token acquisition failed. Please sign in again.");
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
  }, [activeAccount, instance, localMockAuthenticated]);

  const signInLocalMock = useCallback(() => {
    setLocalMockUser({
      email: "alden@lotslotsmore.com",
      displayName: "Alden Bronkhorst (Local Mock)",
      roles: ["AIPlatform.Admin", "AIPlatform.User", "AIPlatform.Developer", "AIPlatform.Auditor"],
    });
    setLocalMockAuthenticated(true);
  }, []);

  const signOut = useCallback(async () => {
    if (localMockAuthenticated) {
      setLocalMockAuthenticated(false);
      setLocalMockUser(null);
      return;
    }
    instance.setActiveAccount(null);
    try {
      await instance.clearCache();
    } catch {
      // Fall back to explicit browser cache cleanup below.
    } finally {
      clearMsalStorage(sessionStorage);
      clearMsalStorage(localStorage);
      window.location.href = "/";
    }
  }, [instance, localMockAuthenticated]);

  return {
    accessToken,
    accounts,
    activeUser,
    authError,
    enableLocalMock: ENABLE_LOCAL_MOCK,
    inProgress,
    instance,
    isTokenLoading,
    signInLocalMock,
    signOut,
  };
}
