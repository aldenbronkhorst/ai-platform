import { useState, useEffect, useCallback, useRef } from "react";
import type { FormEvent } from "react";
import {
  RefreshCw,
  ChevronRight, Search, X,
} from "lucide-react";
import { Button } from "../components/ui/Button";
import { API_BASE_URL, fetchWithTimeout, isAbortError, type AccessTokenGetter } from "../hooks/useApi";
import {
  ConnectorLogo,
  GitHubConnectorSection,
  MicrosoftNativeConnectorSection,
  OdooConnectorSection,
  StatusBadge,
} from "../components/connections/ConnectorSections";
import {
  MICROSOFT_NATIVE_CONNECTOR_KEY_SET,
  formatStatusLabel,
  getStatusTone,
  panelTitleClass,
  panelToneClass,
  type ConnectorDef,
  type ConnectorMeta,
  type MicrosoftNativeDeviceCode,
  type OdooStatus,
} from "../components/connections/connectionShared";

const KV_ERROR_PHRASES = [
  "forbiddenbyrbac", "setsecret/action", "key vault secrets officer",
  "rbac", "authorization failed", "authorizationfailed",
];
const MICROSOFT_DEVICE_LOGIN_URL = "https://microsoft.com/devicelogin";

interface ConnectorNotice {
  status?: string;
  connector?: string;
  title?: string;
  message?: string;
  request_id?: string;
}

interface MicrosoftAuthCallbackResult {
  status: string;
  overall_status?: string;
  connector?: string;
  auth_session_id?: string;
  active_connector?: string;
  active_auth_session_id?: string;
  error?: string;
  error_type?: string;
  message?: string;
  interval?: number;
  scope_label?: string;
  auth_app_name?: string;
  request_id?: string;
}

type ApiRecord = Record<string, unknown>;

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function currentTimeMs() {
  return new Date().getTime();
}

function openMicrosoftAuthWindow(): Window | null {
  const authWindow = window.open("", "_blank", "width=540,height=760");
  if (!authWindow) return null;
  try {
    authWindow.opener = null;
    authWindow.document.title = "Microsoft Sign-In";
    authWindow.document.body.style.margin = "0";
    authWindow.document.body.style.fontFamily = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    authWindow.document.body.style.background = "#101010";
    authWindow.document.body.style.color = "#f4f4f5";
    authWindow.document.body.innerHTML = `
      <main style="min-height:100vh;display:grid;place-items:center;padding:32px;text-align:center;">
        <div>
          <h1 style="font-size:18px;margin:0 0 8px;">Preparing Microsoft sign-in</h1>
          <p style="font-size:14px;line-height:1.5;margin:0;color:#a1a1aa;">Keep this window open.</p>
        </div>
      </main>
    `;
  } catch {
    // Some browsers restrict about:blank writes. Navigation below still works.
  }
  return authWindow;
}

function openMicrosoftDeviceLogin(verificationUrl?: string, authWindow?: Window | null) {
  const targetUrl = verificationUrl || MICROSOFT_DEVICE_LOGIN_URL;
  const targetWindow = authWindow || openMicrosoftAuthWindow();
  if (!targetWindow) {
    window.open(targetUrl, "_blank", "noopener,noreferrer");
    return;
  }
  targetWindow.location.href = targetUrl;
}

function closeMicrosoftAuthWindow(authWindow?: Window | null) {
  if (!authWindow) return;
  try {
    if (!authWindow.closed) authWindow.close();
  } catch { /* ignore browsers that block closing the popup */ }
}

const CONNECTOR_ORDER = [
  "odoo",
  "azure_cli",
  "microsoft_graph",
  "exchange_online",
  "teams_admin",
  "sharepoint_pnp",
  "github",
];

function connectorDefinitions(meta: Record<string, ConnectorMeta> | null): ConnectorDef[] {
  if (!meta) return [];
  const keys = [
    ...CONNECTOR_ORDER.filter((key) => meta[key]),
    ...Object.keys(meta).filter((key) => !CONNECTOR_ORDER.includes(key)).sort(),
  ];
  return keys.map((key) => {
    const connector = meta[key];
    return {
      key,
      name: connector.display_name || formatStatusLabel(key),
      subtitle: connector.subtitle || connector.auth_method || "Connector",
    };
  });
}

function connectorDisplayNameForKey(key: string | undefined, meta: Record<string, ConnectorMeta> | null) {
  if (!key) return "Connector";
  return meta?.[key]?.display_name
    || formatStatusLabel(key);
}

interface ConnectionsPageProps {
  accessToken: string;
  getAccessToken: AccessTokenGetter;
}

export function ConnectionsPage({ accessToken, getAccessToken }: ConnectionsPageProps) {
  const [odooStatus, setOdooStatus] = useState<OdooStatus | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectorNotice, setConnectorNotice] = useState<ConnectorNotice | null>(null);
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [odooUrl, setOdooUrl] = useState("");
  const [odooDb, setOdooDb] = useState("");
  const [odooUsername, setOdooUsername] = useState("");
  const [odooApiKey, setOdooApiKey] = useState("");
  const [microsoftDeviceCode, setMicrosoftDeviceCode] = useState<(MicrosoftNativeDeviceCode & { connectorKey: string }) | null>(null);
  const [microsoftStartingConnector, setMicrosoftStartingConnector] = useState<string | null>(null);
  const [microsoftPollingConnector, setMicrosoftPollingConnector] = useState<string | null>(null);
  const [connectorMeta, setConnectorMeta] = useState<Record<string, ConnectorMeta> | null>(null);
  const [connectorStatusError, setConnectorStatusError] = useState<string | null>(null);
  const [connectorSearch, setConnectorSearch] = useState("");
  const microsoftAuthAttemptRef = useRef(0);
  const microsoftPollTimerRef = useRef<number | null>(null);

  const headers = useCallback(async () => {
    const token = await getAccessToken({ redirectOnFailure: true });
    if (!token) throw new Error("Microsoft session expired. Please sign in again.");
    return {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  }, [getAccessToken]);

  const clearMicrosoftPollTimer = useCallback(() => {
    if (microsoftPollTimerRef.current !== null) {
      window.clearTimeout(microsoftPollTimerRef.current);
      microsoftPollTimerRef.current = null;
    }
  }, []);

  const cancelMicrosoftAuthAttempt = useCallback(() => {
    clearMicrosoftPollTimer();
    microsoftAuthAttemptRef.current += 1;
    setMicrosoftDeviceCode(null);
    setMicrosoftStartingConnector(null);
    setMicrosoftPollingConnector(null);
  }, [clearMicrosoftPollTimer]);

  useEffect(() => () => clearMicrosoftPollTimer(), [clearMicrosoftPollTimer]);

  const fetchConnectors = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/connected-accounts`, { headers: await headers() });
      if (res.ok) {
        const data = await res.json() as { connectors?: ConnectorMeta[] } | ConnectorMeta[];
        const meta: Record<string, ConnectorMeta> = {};
        const connectors = Array.isArray(data) ? data : data.connectors || [];
        connectors.forEach((c) => {
          if (c.connector_key) meta[c.connector_key] = c;
        });
        setConnectorMeta(meta);
        setConnectorStatusError(null);
      } else {
        setConnectorStatusError(`Could not load connector statuses (${res.status}).`);
      }
    } catch (err) {
      setConnectorStatusError(
        isAbortError(err)
          ? "Connector statuses are taking too long to load. Please retry."
          : `Could not load connector statuses: ${errorMessage(err)}`,
      );
    }
  }, [accessToken, headers]);

  const fetchOdooStatus = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/connected-accounts/odoo/status`, { headers: await headers() });
      if (res.ok) {
        const data = await res.json() as OdooStatus;
        setOdooStatus(data);
        if (data.status === "connected" || data.status === "error") {
          if (data.odoo_url) setOdooUrl(data.odoo_url);
          if (data.odoo_db) setOdooDb(data.odoo_db);
          if (data.provider_username) setOdooUsername(data.provider_username);
        }
      }
    } catch { /* leave status empty until backend status can be read */ }
  }, [accessToken, headers]);

  useEffect(() => {
    if (!accessToken) return;
    void Promise.resolve().then(() => Promise.all([fetchOdooStatus(), fetchConnectors()]));
  }, [accessToken, fetchOdooStatus, fetchConnectors]);

  useEffect(() => {
    if (!accessToken) return;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code) return;
    void (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/connector/github/oauth-callback`, {
          method: "POST",
          headers: await headers(),
          body: JSON.stringify({ code, state }),
        });
        const data = await res.json() as ConnectorNotice;
        if (!res.ok) setConnectorNotice({ status: "failed", connector: "github", title: "GitHub connection failed", message: data.message || "GitHub OAuth failed.", request_id: data.request_id });
        await fetchConnectors();
      } catch (err) {
        setConnectorNotice({ status: "failed", connector: "github", title: "GitHub connection failed", message: errorMessage(err) });
      } finally {
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    })();
  }, [accessToken, fetchConnectors, headers]);

  const isKeyVaultError = (msg: string) =>
    KV_ERROR_PHRASES.some((p) => msg.toLowerCase().includes(p));

  const handleConnectOdoo = async (e: FormEvent) => {
    e.preventDefault(); if (!accessToken) return;
    setIsConnecting(true); setConnectorNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/connected-accounts/odoo/connect`, {
        method: "POST", headers: await headers(),
        body: JSON.stringify({ odoo_url: odooUrl, odoo_db: odooDb, odoo_username: odooUsername, odoo_api_key: odooApiKey }),
      });
      const data = await res.json() as ApiRecord;
      if (res.ok) {
        setSelectedConnector(null); setOdooApiKey("");
        void Promise.all([fetchOdooStatus(), fetchConnectors()]);
      } else {
        const rawDetail = data.detail;
        const detail = rawDetail && typeof rawDetail === "object" ? rawDetail as ApiRecord : {};
        const detailMessage = typeof rawDetail === "string" ? rawDetail : stringValue(detail.message, "Connection failed.");
        setConnectorNotice({
          status: "failed",
          connector: "odoo",
          title: isKeyVaultError(detailMessage) ? "Key Vault permission error" : "Connection failed",
          message: detailMessage,
          request_id: stringValue(detail.request_id),
        });
        void Promise.all([fetchOdooStatus(), fetchConnectors()]);
      }
    } catch (err) {
      setConnectorNotice({ status: "failed", connector: "odoo", title: "Connection failed", message: `Could not reach backend: ${errorMessage(err)}` });
    } finally { setIsConnecting(false); }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken || !confirm("Disconnect Odoo? Credentials will be permanently deleted.")) return;
    try {
      const res = await fetch(`${API_BASE_URL}/connected-accounts/odoo/disconnect`, { method: "POST", headers: await headers() });
      if (res.ok) { setOdooUrl(""); setOdooDb(""); setOdooUsername(""); setOdooApiKey(""); setConnectorNotice(null); }
      void Promise.all([fetchOdooStatus(), fetchConnectors()]);
    } catch { /* ignore transient disconnect errors */ }
  };

  const handleConnectMicrosoftNative = async (connectorKey: string) => {
    if (!accessToken) return; setConnectorNotice(null);
    if (microsoftStartingConnector || microsoftPollingConnector) return;
    cancelMicrosoftAuthAttempt();
    const attemptId = microsoftAuthAttemptRef.current + 1;
    microsoftAuthAttemptRef.current = attemptId;
    setMicrosoftStartingConnector(connectorKey);
    const displayName = connectorDisplayNameForKey(connectorKey, connectorMeta);
    const connectPayload: Record<string, string> = {};
    if (connectorKey === "sharepoint_pnp") {
      const siteUrl = window.prompt("Enter the SharePoint site or admin URL for this PnP connector");
      if (!siteUrl?.trim()) {
        setMicrosoftStartingConnector(null);
        setConnectorNotice({ status: "failed", connector: connectorKey, title: "Connection failed", message: "SharePoint / PnP sign-in requires a SharePoint site or admin URL." });
        return;
      }
      connectPayload.site_url = siteUrl.trim();
    }
    const authWindow = openMicrosoftAuthWindow();
    try {
      const res = await fetch(`${API_BASE_URL}/connector/microsoft-native/${connectorKey}/device-code`, {
        method: "POST",
        headers: await headers(),
        body: Object.keys(connectPayload).length ? JSON.stringify(connectPayload) : undefined,
      });
      const data = await res.json() as MicrosoftNativeDeviceCode & { error?: string; message?: string };
      if (data.status === "device_code_ready") {
        setMicrosoftStartingConnector(null);
        setMicrosoftDeviceCode({ ...data, connectorKey });
        setMicrosoftPollingConnector(connectorKey);
        const expiresAtMs = data.expires_at ? data.expires_at * 1000 : currentTimeMs() + (data.expires_in || 900) * 1000;
        openMicrosoftDeviceLogin(data.verification_url, authWindow);
        const poll = async () => {
          if (microsoftAuthAttemptRef.current !== attemptId) return;
          if (currentTimeMs() >= expiresAtMs) {
            closeMicrosoftAuthWindow(authWindow);
            setMicrosoftPollingConnector(null);
            setMicrosoftDeviceCode(null);
            setConnectorNotice({
              status: "failed",
              connector: connectorKey,
              title: "Sign-in expired",
              message: `The Microsoft sign-in code for ${displayName} expired before Microsoft completed authorization. Start a new sign-in and enter the newest code.`,
              request_id: data.request_id,
            });
            void fetchConnectors();
            return;
          }
          try {
            const pr = await fetch(`${API_BASE_URL}/connector/microsoft-native/${connectorKey}/token-callback`, {
              method: "POST", headers: await headers(),
              body: JSON.stringify({
                auth_session_id: data.auth_session_id,
                device_code: data.device_code,
                site_url: data.site_url || connectPayload.site_url,
              }),
            });
            const pd = await pr.json() as MicrosoftAuthCallbackResult;
            if (microsoftAuthAttemptRef.current !== attemptId) return;
            if (pd.status === "connected") {
              closeMicrosoftAuthWindow(authWindow);
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setConnectorNotice(null);
              void fetchConnectors();
            } else if (pd.status === "pending") {
              microsoftPollTimerRef.current = window.setTimeout(poll, (pd.interval || data.interval || 5) * 1000);
            } else if (pd.status === "stale") {
              closeMicrosoftAuthWindow(authWindow);
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setConnectorNotice({
                status: "failed",
                connector: connectorKey,
                title: "Sign-in restarted",
                message: pd.message || "A newer Microsoft sign-in was started. Use the newest device code.",
                request_id: pd.request_id,
              });
              void fetchConnectors();
            } else {
              closeMicrosoftAuthWindow(authWindow);
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setConnectorNotice({
                status: "failed",
                connector: connectorKey,
                title: "Connection failed",
                message: pd.message || pd.error || `${displayName} authentication failed.`,
                request_id: pd.request_id,
              });
              void fetchConnectors();
            }
          } catch (err) {
            if (microsoftAuthAttemptRef.current !== attemptId) return;
            closeMicrosoftAuthWindow(authWindow);
            setMicrosoftStartingConnector(null);
            setMicrosoftPollingConnector(null);
            setMicrosoftDeviceCode(null);
            setConnectorNotice({
              status: "failed",
              connector: connectorKey,
              title: "Connection failed",
              message: `Could not check Microsoft sign-in status for ${displayName}: ${errorMessage(err)}`,
              request_id: data.request_id,
            });
            void fetchConnectors();
          }
        };
        microsoftPollTimerRef.current = window.setTimeout(poll, (data.interval || 5) * 1000);
      } else {
        closeMicrosoftAuthWindow(authWindow);
        setMicrosoftStartingConnector(null);
        setMicrosoftDeviceCode(null);
        setMicrosoftPollingConnector(null);
        setConnectorNotice({ status: "failed", connector: connectorKey, title: "Connection failed", message: data.message || data.error || "Failed to start device code flow", request_id: data.request_id });
      }
    } catch (err) {
      closeMicrosoftAuthWindow(authWindow);
      setMicrosoftStartingConnector(null);
      setMicrosoftPollingConnector(null);
      setMicrosoftDeviceCode(null);
      setConnectorNotice({ status: "failed", connector: connectorKey, title: "Connection failed", message: errorMessage(err) });
    }
  };

  const handleMicrosoftNativeDisconnect = async (connectorKey: string) => {
    if (!accessToken) return;
    cancelMicrosoftAuthAttempt();
    await fetch(`${API_BASE_URL}/connector/microsoft-native/${connectorKey}/disconnect`, { method: "POST", headers: await headers() });
    await fetchConnectors();
    setConnectorNotice(null);
  };

  const handleGithubOAuth = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${API_BASE_URL}/connector/github/auth-url`, { method: "GET", headers: await headers() });
      const data = await res.json() as { auth_url?: string; message?: string };
      if (data.auth_url) window.location.href = data.auth_url;
      else setConnectorNotice({ status: "failed", connector: "github", title: "Connection failed", message: data.message || "GitHub OAuth not configured." });
    } catch (err) { setConnectorNotice({ status: "failed", connector: "github", title: "Connection failed", message: errorMessage(err) }); }
  };

  const connectorDetail = (key: string) => {
    const c = availableConnectors.find(x => x.key === key);
    if (!c) return null;
    const meta = connectorMeta?.[key];
    const metaStatus = connectorMeta?.[key]?.status;

    if (key === "odoo") {
      return (
        <OdooConnectorSection
          odooStatus={odooStatus}
          odooUrl={odooUrl}
          odooDb={odooDb}
          odooUsername={odooUsername}
          odooApiKey={odooApiKey}
          isConnecting={isConnecting}
          onConnect={handleConnectOdoo}
          onDisconnect={handleDisconnectOdoo}
          onOdooUrlChange={setOdooUrl}
          onOdooDbChange={setOdooDb}
          onOdooUsernameChange={setOdooUsername}
          onOdooApiKeyChange={setOdooApiKey}
        />
      );
    }

    if (MICROSOFT_NATIVE_CONNECTOR_KEY_SET.has(key)) {
      const isPolling = microsoftPollingConnector === key;
      const isStarting = microsoftStartingConnector === key;
      const activeDeviceCode = microsoftDeviceCode?.connectorKey === key ? microsoftDeviceCode : null;
      return (
        <MicrosoftNativeConnectorSection
          connector={c}
          meta={meta}
          status={metaStatus}
          isStarting={isStarting}
          isPolling={isPolling}
          activeDeviceCode={activeDeviceCode}
          onConnect={() => handleConnectMicrosoftNative(key)}
          onDisconnect={() => handleMicrosoftNativeDisconnect(key)}
          onOpenDeviceLogin={openMicrosoftDeviceLogin}
        />
      );
    }

    if (key === "github") return (
      <GitHubConnectorSection
        meta={connectorMeta?.github}
        status={metaStatus}
        onConnect={handleGithubOAuth}
      />
    );

    return null;
  };

  const availableConnectors = connectorDefinitions(connectorMeta);
  const normalizedConnectorSearch = connectorSearch.trim().toLowerCase();
  const filteredConnectors = normalizedConnectorSearch
    ? availableConnectors.filter((connector) => {
      const meta = connectorMeta?.[connector.key];
      const searchable = [
        connector.key,
        connector.name,
        connector.subtitle,
        meta?.status,
        meta?.state?.account_status,
        meta?.state?.token_status,
        meta?.metadata?.provider_username,
        meta?.metadata?.permission_summary,
      ].filter(Boolean).join(" ").toLowerCase();
      return searchable.includes(normalizedConnectorSearch);
    })
    : availableConnectors;
  const activeConnector = selectedConnector && availableConnectors.some((connector) => connector.key === selectedConnector)
    ? selectedConnector
    : null;

  return (
    <div className="settings-page mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8 animate-fade-in">
      <div className="settings-page-header">
        <div className="min-w-0">
          <h2 className="settings-title text-xl">Connectors</h2>
          <p className="settings-copy mt-1 max-w-2xl text-sm">
            Connect external systems and expose their account-specific tools to the AI platform.
          </p>
        </div>
        {connectorStatusError ? (
          <div className="settings-actions">
            <Button size="sm" onClick={fetchConnectors}>
              <RefreshCw className="w-3.5 h-3.5" /> Retry
            </Button>
          </div>
        ) : null}
      </div>

      {connectorMeta === null && !connectorStatusError ? (
        <div className="settings-empty">
          <p className="text-sm text-muted">Loading connectors...</p>
        </div>
      ) : null}

      {connectorStatusError ? (
        <div className="settings-inline-alert">
          <p className="text-sm text-muted">{connectorStatusError}</p>
        </div>
      ) : null}

      {connectorMeta && availableConnectors.length === 0 && !connectorStatusError ? (
        <div className="settings-empty">
          <p className="text-sm text-muted">No connectors are available.</p>
        </div>
      ) : null}

      {availableConnectors.length > 0 ? (
        <>
          <div className="settings-toolbar settings-toolbar-search-only">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-soft" />
              <input
                value={connectorSearch}
                onChange={(e) => setConnectorSearch(e.target.value)}
                placeholder="Search connectors..."
                className="settings-search pl-9"
              />
            </div>
          </div>

          {filteredConnectors.length === 0 ? (
            <div className="settings-empty">
              <p className="text-sm text-muted">No connectors match your search.</p>
            </div>
          ) : (
            <div className="settings-list">
              <div className="settings-list-head connector-grid">
                <span>Connector</span>
                <span>Status</span>
                <span className="text-right">Details</span>
              </div>

              {filteredConnectors.map((c) => {
                const meta = connectorMeta?.[c.key];
                const status = meta?.status;
                return (
                  <button
                    key={c.key}
                    onClick={() => { cancelMicrosoftAuthAttempt(); setSelectedConnector(c.key); setConnectorNotice(null); }}
                    className="settings-list-row connector-grid group w-full text-left"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-default bg-transparent">
                        <ConnectorLogo connectorKey={c.key} />
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-extrabold text-default">{c.name}</span>
                        <span className="mt-1 block truncate text-xs font-semibold text-muted">{c.subtitle}</span>
                      </span>
                    </div>

                    <div className="flex items-center">
                      <StatusBadge
                        status={status}
                        fallback={connectorStatusError ? "Status Unavailable" : "Checking..."}
                        hasError={Boolean(connectorStatusError && !status)}
                      />
                    </div>

                    <span className="flex justify-end">
                      <ChevronRight className="h-4 w-4 text-soft transition-colors group-hover:text-default" />
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      ) : null}

      {/* Detail Drawer */}
      {activeConnector && (
        <div className="fixed inset-0 z-50 flex justify-end bg-[color-mix(in_srgb,var(--ui-chat-surface-background)_84%,transparent)] animate-fade-in">
          <div className="w-full max-w-lg bg-raised border-l border-default overflow-y-auto">
            <div className="p-4 sm:p-6 border-b border-default flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-transparent border border-default shrink-0">
                  <ConnectorLogo connectorKey={activeConnector} />
                </div>
                <h2 className="font-bold text-lg text-default truncate">
                  {availableConnectors.find(c => c.key === activeConnector)?.name || activeConnector}
                </h2>
              </div>
              <button onClick={() => { cancelMicrosoftAuthAttempt(); setSelectedConnector(null); setConnectorNotice(null); }}
                className="p-2 rounded-lg hover-bg-subtle text-muted hover-text-default">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 sm:p-6">
              {connectorDetail(activeConnector)}

              {connectorNotice && (
                <div className={`mt-4 p-3 rounded-lg text-sm border ${panelToneClass(getStatusTone(connectorNotice.status, connectorNotice.status === "failed"))}`}>
                  <p className={`font-semibold ${panelTitleClass(getStatusTone(connectorNotice.status, connectorNotice.status === "failed"))}`}>
                    {connectorNotice.title || connectorDisplayNameForKey(connectorNotice.connector, connectorMeta)}
                  </p>
                  {connectorNotice.message ? <p className="text-muted mt-1">{connectorNotice.message}</p> : null}
                  {connectorNotice.request_id ? <p className="mt-2 text-xs text-muted">Support reference: {connectorNotice.request_id}</p> : null}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
