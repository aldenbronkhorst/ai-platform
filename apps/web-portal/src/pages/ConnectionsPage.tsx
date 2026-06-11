import { useState, useEffect, useCallback, useRef } from "react";
import type { FormEvent } from "react";
import {
  Plug, RefreshCw,
  ChevronRight, Search, X,
} from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { GlassButton } from "../components/ui/GlassButton";
import { APIM_BASE_URL, fetchWithTimeout, isAbortError } from "../hooks/useApi";
import {
  ConnectorLogo,
  DetailCard,
  GitHubConnectorSection,
  MicrosoftNativeConnectorSection,
  OdooConnectorSection,
  StatusBadge,
  ToolLogo,
} from "../components/connections/ConnectorSections";
import {
  MICROSOFT_NATIVE_CONNECTOR_KEYS,
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
const MICROSOFT_SESSION_RESET_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/logout";
const MICROSOFT_SESSION_RESET_DELAY_MS = 1800;

interface PlatformTool {
  id: string;
  name: string;
  display_name: string;
  description?: string | null;
  target_system: string;
  version: string;
  status: string;
  requires_approval: string;
  created_at: string;
}

interface ConnectionTestResult {
  success: boolean;
  message: string;
  isKeyVaultError?: boolean;
  errorType?: string;
  stage?: string;
  technicalDetail?: string;
  requestId?: string;
  connectionAttemptId?: string;
  trace?: { trace_id?: string } | null;
}

interface CliCommandResult {
  command?: string;
  stdout?: string;
  stderr?: string;
  error_message?: string;
  exit_code?: number;
}

interface CliTestResult {
  success?: boolean;
  status?: string;
  connector?: string;
  message?: string;
  stdout?: string;
  stderr?: string;
  request_id?: string;
  commands?: CliCommandResult[];
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
  targetWindow.location.href = MICROSOFT_SESSION_RESET_URL;
  window.setTimeout(() => {
    if (!targetWindow.closed) targetWindow.location.href = targetUrl;
  }, MICROSOFT_SESSION_RESET_DELAY_MS);
}

function closeMicrosoftAuthWindow(authWindow?: Window | null) {
  if (!authWindow) return;
  try {
    if (!authWindow.closed) authWindow.close();
  } catch { /* ignore browsers that block closing the popup */ }
}

function cliResultHeading(result: CliTestResult) {
  const tone = getStatusTone(result.status, result.success === false);
  if (tone === "success") return "Ready";
  if (tone === "warning") return "Attention needed";
  return "Issues found";
}

const CONNECTOR_TOOL_TARGET_SYSTEMS = new Set([
  "odoo",
  "github",
  ...MICROSOFT_NATIVE_CONNECTOR_KEYS,
]);

const CONNECTOR_FALLBACKS: ConnectorDef[] = [
  { key: "odoo", name: "Odoo", subtitle: "ERP connector" },
  { key: "azure_cli", name: "Azure CLI", subtitle: "Native Azure CLI" },
  { key: "microsoft_graph", name: "Microsoft Graph", subtitle: "Direct Microsoft Graph" },
  { key: "exchange_online", name: "Exchange Online", subtitle: "Exchange Online PowerShell" },
  { key: "teams_admin", name: "Teams Admin", subtitle: "Microsoft Teams PowerShell" },
  { key: "sharepoint_pnp", name: "SharePoint / PnP", subtitle: "SharePoint / PnP PowerShell" },
  { key: "github", name: "GitHub", subtitle: "Native GitHub CLI connector" },
];
const CONNECTOR_FALLBACK_BY_KEY = new Map(CONNECTOR_FALLBACKS.map((connector) => [connector.key, connector]));

function connectorDefinitions(meta: Record<string, ConnectorMeta> | null): ConnectorDef[] {
  if (!meta) return CONNECTOR_FALLBACKS;
  const preferredOrder = CONNECTOR_FALLBACKS.map((connector) => connector.key);
  const keys = [
    ...preferredOrder.filter((key) => meta[key]),
    ...Object.keys(meta).filter((key) => !preferredOrder.includes(key)).sort(),
  ];
  return keys.map((key) => {
    const fallback = CONNECTOR_FALLBACK_BY_KEY.get(key);
    const connector = meta[key];
    return {
      key,
      name: connector.display_name || fallback?.name || formatStatusLabel(key),
      subtitle: connector.subtitle || fallback?.subtitle || connector.auth_method || "Connector",
    };
  });
}

function connectorDisplayNameForKey(key: string | undefined, meta: Record<string, ConnectorMeta> | null) {
  if (!key) return "Connector";
  return meta?.[key]?.display_name
    || CONNECTOR_FALLBACK_BY_KEY.get(key)?.name
    || formatStatusLabel(key);
}

interface ConnectionsPageProps { accessToken: string; }

function canonicalPlatformTools(tools: PlatformTool[]) {
  const seen = new Set<string>();
  return tools.filter((tool) => {
    if (CONNECTOR_TOOL_TARGET_SYSTEMS.has(tool.target_system)) return false;
    const key = `${(tool.display_name || tool.name).trim().toLowerCase()}::${tool.target_system}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function ConnectionsPage({ accessToken }: ConnectionsPageProps) {
  const [odooStatus, setOdooStatus] = useState<OdooStatus | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [showTechDetails, setShowTechDetails] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [odooUrl, setOdooUrl] = useState("");
  const [odooDb, setOdooDb] = useState("");
  const [odooUsername, setOdooUsername] = useState("");
  const [odooApiKey, setOdooApiKey] = useState("");
  const [cliTestResult, setCliTestResult] = useState<CliTestResult | null>(null);
  const [microsoftDeviceCode, setMicrosoftDeviceCode] = useState<(MicrosoftNativeDeviceCode & { connectorKey: string }) | null>(null);
  const [microsoftStartingConnector, setMicrosoftStartingConnector] = useState<string | null>(null);
  const [microsoftPollingConnector, setMicrosoftPollingConnector] = useState<string | null>(null);
  const [connectorMeta, setConnectorMeta] = useState<Record<string, ConnectorMeta> | null>(null);
  const [connectorStatusError, setConnectorStatusError] = useState<string | null>(null);
  const [connectorSearch, setConnectorSearch] = useState("");
  const [platformTools, setPlatformTools] = useState<PlatformTool[] | null>(null);
  const [platformToolsError, setPlatformToolsError] = useState<string | null>(null);
  const microsoftAuthAttemptRef = useRef(0);
  const microsoftPollTimerRef = useRef<number | null>(null);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

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
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/connected-accounts`, { headers: headers() });
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

  const fetchPlatformTools = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/tools`, { headers: headers() });
      if (res.ok) {
        const data = await res.json() as PlatformTool[];
        setPlatformTools(canonicalPlatformTools(data));
        setPlatformToolsError(null);
      } else {
        setPlatformToolsError(`Could not load platform tools (${res.status}).`);
      }
    } catch (err) {
      setPlatformToolsError(
        isAbortError(err)
          ? "Platform tools are taking too long to load. Please retry."
          : `Could not load platform tools: ${errorMessage(err)}`,
      );
    }
  }, [accessToken, headers]);

  const fetchOdooStatus = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/connected-accounts/odoo/status`, { headers: headers() });
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
    void Promise.resolve().then(() => Promise.all([fetchOdooStatus(), fetchConnectors(), fetchPlatformTools()]));
  }, [accessToken, fetchOdooStatus, fetchConnectors, fetchPlatformTools]);

  useEffect(() => {
    if (!accessToken) return;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code) return;
    void (async () => {
      try {
        const res = await fetch(`${APIM_BASE_URL}/connector/github/oauth-callback`, {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({ code, state }),
        });
        const data = await res.json() as CliTestResult;
        setCliTestResult({ ...data, status: res.ok ? "success" : "failed", connector: "github" });
        await fetchConnectors();
      } catch (err) {
        setCliTestResult({ status: "failed", connector: "github", message: errorMessage(err) });
      } finally {
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    })();
  }, [accessToken, fetchConnectors, headers]);

  const isKeyVaultError = (msg: string) =>
    KV_ERROR_PHRASES.some((p) => msg.toLowerCase().includes(p));

  const handleConnectOdoo = async (e: FormEvent) => {
    e.preventDefault(); if (!accessToken) return;
    setIsConnecting(true); setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/connect`, {
        method: "POST", headers: headers(),
        body: JSON.stringify({ odoo_url: odooUrl, odoo_db: odooDb, odoo_username: odooUsername, odoo_api_key: odooApiKey }),
      });
      const data = await res.json() as ApiRecord;
      if (res.ok) {
        setTestResult({ success: true, message: "Odoo connection established!" });
        setSelectedConnector(null); setOdooApiKey("");
        void Promise.all([fetchOdooStatus(), fetchConnectors()]);
      } else {
        const rawDetail = data.detail;
        const detail = rawDetail && typeof rawDetail === "object" ? rawDetail as ApiRecord : {};
        const detailMessage = typeof rawDetail === "string" ? rawDetail : stringValue(detail.message, "Connection failed.");
        setTestResult({
          success: false, message: detailMessage,
          isKeyVaultError: isKeyVaultError(detailMessage),
          errorType: stringValue(detail.error_type), stage: stringValue(detail.stage),
          technicalDetail: stringValue(detail.technical_detail), requestId: stringValue(detail.request_id),
          connectionAttemptId: stringValue(detail.connection_attempt_id),
          trace: detail.trace && typeof detail.trace === "object" ? detail.trace as { trace_id?: string } : null,
        });
        void Promise.all([fetchOdooStatus(), fetchConnectors()]);
      }
    } catch (err) {
      setTestResult({ success: false, message: `Could not reach backend: ${errorMessage(err)}` });
    } finally { setIsConnecting(false); }
  };

  const handleTestOdoo = async () => {
    if (!accessToken) return; setIsTesting(true); setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/test`, { method: "POST", headers: headers() });
      const data = await res.json() as { status?: string; detail?: string };
      if (res.ok) {
        const status = data.status || "unknown";
        setTestResult({ success: status === "connected", message: `Connection state: ${status.toUpperCase()}` });
      }
      else setTestResult({ success: false, message: data.detail || "Verification failed." });
      void Promise.all([fetchOdooStatus(), fetchConnectors()]);
    } catch (err) { setTestResult({ success: false, message: `Test failed: ${errorMessage(err)}` }); }
    finally { setIsTesting(false); }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken || !confirm("Disconnect Odoo? Credentials will be permanently deleted.")) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/disconnect`, { method: "POST", headers: headers() });
      if (res.ok) { setOdooUrl(""); setOdooDb(""); setOdooUsername(""); setOdooApiKey(""); setTestResult(null); }
      void Promise.all([fetchOdooStatus(), fetchConnectors()]);
    } catch { /* ignore transient disconnect errors */ }
  };

  const handleConnectMicrosoftNative = async (connectorKey: string) => {
    if (!accessToken) return; setCliTestResult(null);
    if (microsoftStartingConnector || microsoftPollingConnector) return;
    cancelMicrosoftAuthAttempt();
    const attemptId = microsoftAuthAttemptRef.current + 1;
    microsoftAuthAttemptRef.current = attemptId;
    setMicrosoftStartingConnector(connectorKey);
    const displayName = connectorMeta?.[connectorKey]?.display_name || CONNECTOR_FALLBACK_BY_KEY.get(connectorKey)?.name || formatStatusLabel(connectorKey);
    const connectPayload: Record<string, string> = {};
    if (connectorKey === "sharepoint_pnp") {
      const siteUrl = window.prompt("Enter the SharePoint site or admin URL for this PnP connector");
      if (!siteUrl?.trim()) {
        setMicrosoftStartingConnector(null);
        setCliTestResult({ status: "failed", connector: connectorKey, message: "SharePoint / PnP sign-in requires a SharePoint site or admin URL." });
        return;
      }
      connectPayload.site_url = siteUrl.trim();
    }
    const authWindow = openMicrosoftAuthWindow();
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/microsoft-native/${connectorKey}/device-code`, {
        method: "POST",
        headers: headers(),
        body: Object.keys(connectPayload).length ? JSON.stringify(connectPayload) : undefined,
      });
      const data = await res.json() as MicrosoftNativeDeviceCode & { error?: string; message?: string };
      if (data.status === "device_code_ready") {
        setMicrosoftStartingConnector(null);
        setMicrosoftDeviceCode({ ...data, connectorKey });
        setMicrosoftPollingConnector(connectorKey);
        const authApp = data.auth_app_name ? ` in ${data.auth_app_name}` : "";
        const expiresAtMs = data.expires_at ? data.expires_at * 1000 : currentTimeMs() + (data.expires_in || 900) * 1000;
        setCliTestResult({ status: "pending", connector: connectorKey, message: `Sign in to ${displayName}${authApp}. This stores only the ${displayName} connector token.` });
        openMicrosoftDeviceLogin(data.verification_url, authWindow);
        const poll = async () => {
          if (microsoftAuthAttemptRef.current !== attemptId) return;
          if (currentTimeMs() >= expiresAtMs) {
            setMicrosoftPollingConnector(null);
            setMicrosoftDeviceCode(null);
            setCliTestResult({
              status: "failed",
              connector: connectorKey,
              message: `The Microsoft sign-in code for ${displayName} expired before Microsoft completed authorization. Start a new sign-in and enter the newest code.`,
              request_id: data.request_id,
            });
            void fetchConnectors();
            return;
          }
          try {
            const pr = await fetch(`${APIM_BASE_URL}/connector/microsoft-native/${connectorKey}/token-callback`, {
              method: "POST", headers: headers(),
              body: JSON.stringify({
                auth_session_id: data.auth_session_id,
                device_code: data.device_code,
                site_url: data.site_url || connectPayload.site_url,
              }),
            });
            const pd = await pr.json() as MicrosoftAuthCallbackResult;
            if (microsoftAuthAttemptRef.current !== attemptId) return;
            if (pd.status === "connected") {
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setCliTestResult({
                status: "success",
                connector: connectorKey,
                message: pd.message || `${displayName} connected.`,
                request_id: pd.request_id,
              });
              void fetchConnectors();
            } else if (pd.status === "pending") {
              const remainingSeconds = Math.max(0, Math.ceil((expiresAtMs - currentTimeMs()) / 1000));
              setCliTestResult({
                status: "pending",
                connector: connectorKey,
                message: `Waiting for Microsoft to complete ${displayName} sign-in. Code expires in ${remainingSeconds}s.`,
                request_id: pd.request_id || data.request_id,
              });
              microsoftPollTimerRef.current = window.setTimeout(poll, (pd.interval || data.interval || 5) * 1000);
            } else if (pd.status === "stale") {
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setCliTestResult({
                status: "failed",
                connector: connectorKey,
                message: pd.message || "A newer Microsoft sign-in was started. Use the newest device code.",
                stderr: pd.error_type,
                request_id: pd.request_id,
              });
              void fetchConnectors();
            } else {
              setMicrosoftPollingConnector(null);
              setMicrosoftDeviceCode(null);
              setCliTestResult({
                status: "failed",
                connector: connectorKey,
                message: pd.message || pd.error || `${displayName} authentication failed.`,
                stderr: pd.error_type,
                request_id: pd.request_id,
              });
              void fetchConnectors();
            }
          } catch (err) {
            if (microsoftAuthAttemptRef.current !== attemptId) return;
            setMicrosoftStartingConnector(null);
            setMicrosoftPollingConnector(null);
            setMicrosoftDeviceCode(null);
            setCliTestResult({
              status: "failed",
              connector: connectorKey,
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
        setCliTestResult({ status: "failed", connector: connectorKey, message: data.message || data.error || "Failed to start device code flow", request_id: data.request_id });
      }
    } catch (err) {
      closeMicrosoftAuthWindow(authWindow);
      setMicrosoftStartingConnector(null);
      setMicrosoftPollingConnector(null);
      setMicrosoftDeviceCode(null);
      setCliTestResult({ status: "failed", connector: connectorKey, message: errorMessage(err) });
    }
  };

  const handleMicrosoftNativeStatus = async (connectorKey: string) => {
    if (!accessToken) return;
    const displayName = connectorMeta?.[connectorKey]?.display_name || CONNECTOR_FALLBACK_BY_KEY.get(connectorKey)?.name || formatStatusLabel(connectorKey);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/microsoft-native/${connectorKey}/diagnose`, { method: "POST", headers: headers() });
      const data = await res.json() as { status?: string; overall_status?: string; message?: string; stderr?: string; request_id?: string };
      if (data.status === "success") {
        setCliTestResult({ status: "success", connector: connectorKey, message: data.message || `${displayName} connected`, request_id: data.request_id });
        await fetchConnectors();
      } else if (data.status === "partial" || data.status === "limited" || data.status === "warning") {
        setCliTestResult({ status: "warning", connector: connectorKey, message: data.message || `${displayName} status: ${formatStatusLabel(data.status || "partial")}`, stderr: data.stderr, request_id: data.request_id });
        await fetchConnectors();
      } else {
        setCliTestResult({ status: "failed", connector: connectorKey, message: data.message || `${displayName} status: ${formatStatusLabel(data.status || "not_connected")}`, stderr: data.stderr, request_id: data.request_id });
        await fetchConnectors();
      }
    } catch { /* ignore transient Microsoft connector status errors */ }
  };

  const handleMicrosoftNativeDisconnect = async (connectorKey: string) => {
    if (!accessToken) return;
    cancelMicrosoftAuthAttempt();
    await fetch(`${APIM_BASE_URL}/connector/microsoft-native/${connectorKey}/disconnect`, { method: "POST", headers: headers() });
    await fetchConnectors();
    setCliTestResult({ status: "success", connector: connectorKey, message: "Disconnected" });
  };

  const handleGithubOAuth = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/github/auth-url`, { method: "GET", headers: headers() });
      const data = await res.json() as { auth_url?: string; message?: string };
      if (data.auth_url) window.location.href = data.auth_url;
      else setCliTestResult({ status: "failed", connector: "github", message: data.message || "GitHub OAuth not configured." });
    } catch (err) { setCliTestResult({ status: "failed", connector: "github", message: errorMessage(err) }); }
  };

  const handleGithubStatus = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/github/diagnose`, { method: "POST", headers: headers() });
      const data = await res.json() as { status?: string; message?: string; stderr?: string; request_id?: string };
      if (data.status === "success") {
        setCliTestResult({ status: "success", connector: "github", message: data.message || "GitHub connected", request_id: data.request_id });
        await fetchConnectors();
      } else {
        setCliTestResult({ status: "failed", connector: "github", message: data.message || `GitHub status: ${formatStatusLabel(data.status || "not_connected")}`, stderr: data.stderr, request_id: data.request_id });
        await fetchConnectors();
      }
    } catch { /* ignore transient GitHub status errors */ }
  };

  const connectorDetail = (key: string) => {
    const c = availableConnectors.find(x => x.key === key);
    if (!c) return null;
    const meta = connectorMeta?.[key];
    const metaStatus = connectorMeta?.[key]?.status;
    const statusFallback = connectorStatusError ? "Status Unavailable" : "Checking...";
    const hasStatusError = Boolean(connectorStatusError && !metaStatus);

    if (key === "odoo") {
      return (
        <OdooConnectorSection
          connector={c}
          status={metaStatus}
          statusFallback={statusFallback}
          hasStatusError={hasStatusError}
          odooStatus={odooStatus}
          odooUrl={odooUrl}
          odooDb={odooDb}
          odooUsername={odooUsername}
          odooApiKey={odooApiKey}
          isConnecting={isConnecting}
          isTesting={isTesting}
          onConnect={handleConnectOdoo}
          onTest={handleTestOdoo}
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
          statusFallback={statusFallback}
          hasStatusError={hasStatusError}
          isStarting={isStarting}
          isPolling={isPolling}
          activeDeviceCode={activeDeviceCode}
          onConnect={() => handleConnectMicrosoftNative(key)}
          onCheckStatus={() => handleMicrosoftNativeStatus(key)}
          onDisconnect={() => handleMicrosoftNativeDisconnect(key)}
          onOpenDeviceLogin={openMicrosoftDeviceLogin}
        />
      );
    }

    if (key === "github") return (
      <GitHubConnectorSection
        connector={c}
        meta={connectorMeta?.github}
        status={metaStatus}
        statusFallback={statusFallback}
        hasStatusError={hasStatusError}
        onConnect={handleGithubOAuth}
        onCheckStatus={handleGithubStatus}
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
        meta?.state?.diagnostics_status,
        meta?.state?.cli_status,
        meta?.metadata?.provider_username,
        meta?.metadata?.permission_summary,
      ].filter(Boolean).join(" ").toLowerCase();
      return searchable.includes(normalizedConnectorSearch);
    })
    : availableConnectors;
  const activeConnector = selectedConnector && availableConnectors.some((connector) => connector.key === selectedConnector)
    ? selectedConnector
    : null;
  const filteredPlatformTools = (platformTools || []).filter((tool) => {
    if (!normalizedConnectorSearch) return true;
    const searchable = [
      tool.name,
      tool.display_name,
      tool.description,
      tool.target_system,
      tool.status,
      tool.version,
    ].filter(Boolean).join(" ").toLowerCase();
    return searchable.includes(normalizedConnectorSearch);
  });

  return (
    <div className="max-w-6xl mx-auto space-y-5 sm:space-y-8 animate-fade-in">
      <GlassPanel className="p-5 sm:p-8 rounded-2xl sm:rounded-3xl flex items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-default mb-2">Connectors</h2>
          <p className="text-sm text-muted max-w-2xl">
            Connect external systems and expose their account-specific tools to the AI platform.
          </p>
        </div>
        <Plug className="hidden sm:block w-12 h-12 text-soft shrink-0" />
      </GlassPanel>

      {connectorMeta === null && !connectorStatusError ? (
        <DetailCard>
          <p className="text-sm text-muted">Loading connectors...</p>
        </DetailCard>
      ) : null}

      {connectorStatusError ? (
        <DetailCard>
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted">{connectorStatusError}</p>
            <GlassButton size="sm" onClick={fetchConnectors}>
              <RefreshCw className="w-3.5 h-3.5" /> Retry
            </GlassButton>
          </div>
        </DetailCard>
      ) : null}

      {connectorMeta && availableConnectors.length === 0 && !connectorStatusError ? (
        <DetailCard>
          <p className="text-sm text-muted">No connectors are available.</p>
        </DetailCard>
      ) : null}

      {availableConnectors.length > 0 ? (
      <div className="space-y-4">
        <div className="relative max-w-xl">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-soft pointer-events-none" />
          <input
            value={connectorSearch}
            onChange={(e) => setConnectorSearch(e.target.value)}
            placeholder="Search connectors and tools..."
            className="w-full rounded-2xl border border-default bg-surface py-3 pl-10 pr-4 text-sm text-default placeholder-soft outline-none transition-all focus:border-soft"
          />
        </div>

        {filteredConnectors.length === 0 ? (
          <DetailCard>
            <p className="text-sm text-muted">No connectors match your search.</p>
          </DetailCard>
        ) : (
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredConnectors.map((c) => {
          const meta = connectorMeta?.[c.key];
          const status = meta?.status;
          return (
          <button key={c.key} onClick={() => { cancelMicrosoftAuthAttempt(); setSelectedConnector(c.key); setTestResult(null); setCliTestResult(null); }}
            className="text-left w-full p-5 rounded-2xl border border-default bg-surface hover:bg-canvas transition-colors cursor-pointer group">
            <div className="flex items-start justify-between mb-3">
              <div className="p-2.5 rounded-xl bg-surface border border-default">
                <ConnectorLogo connectorKey={c.key} />
              </div>
              <ChevronRight className="w-4 h-4 text-soft group-hover:text-default transition-colors" />
            </div>
            <h4 className="font-bold text-sm text-default truncate">{c.name}</h4>
            <p className="text-xs text-muted truncate mb-3">{c.subtitle}</p>
            <StatusBadge
              status={status}
              fallback={connectorStatusError ? "Status Unavailable" : "Checking..."}
              hasError={Boolean(connectorStatusError && !status)}
            />
          </button>
        );})}
      </div>
        )}
      </div>
      ) : null}

      {platformToolsError ? (
        <DetailCard>
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted">{platformToolsError}</p>
            <GlassButton size="sm" onClick={fetchPlatformTools}>
              <RefreshCw className="w-3.5 h-3.5" /> Retry
            </GlassButton>
          </div>
        </DetailCard>
      ) : null}

      {platformTools && filteredPlatformTools.length > 0 ? (
        <div className="space-y-4">
          <div>
            <h3 className="font-bold text-base text-default">Platform Tools</h3>
            <p className="text-sm text-muted mt-1">Built-in capabilities registered by the backend.</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredPlatformTools.map((tool) => (
              <div key={tool.id} className="text-left w-full p-5 rounded-2xl border border-default bg-surface">
                <div className="flex items-start justify-between mb-3">
                  <div className="p-2.5 rounded-xl bg-surface border border-default">
                    <ToolLogo toolName={tool.name} />
                  </div>
                  <StatusBadge status={tool.status} fallback="Unavailable" />
                </div>
                <h4 className="font-bold text-sm text-default truncate">{tool.display_name}</h4>
                <p className="text-xs text-muted truncate mb-3">{tool.description || tool.target_system}</p>
                <dl className="grid grid-cols-[72px_1fr] gap-x-3 gap-y-1.5 text-xs">
                  <dt className="text-muted">Tool</dt>
                  <dd className="text-default truncate">{tool.name}</dd>
                  <dt className="text-muted">System</dt>
                  <dd className="text-default truncate">{tool.target_system}</dd>
                  <dt className="text-muted">Approval</dt>
                  <dd className="text-default">{tool.requires_approval === "true" ? "Required" : "Not required"}</dd>
                </dl>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* Detail Drawer */}
      {activeConnector && (
        <div className="fixed inset-0 bg-canvas/80 backdrop-blur-sm z-50 flex justify-end animate-fade-in">
          <div className="w-full max-w-lg bg-surface border-l border-default overflow-y-auto">
            <div className="p-4 sm:p-6 border-b border-default flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-xl bg-canvas border border-default shrink-0">
                  <ConnectorLogo connectorKey={activeConnector} />
                </div>
                <h2 className="font-bold text-lg text-default truncate">
                  {availableConnectors.find(c => c.key === activeConnector)?.name || activeConnector}
                </h2>
              </div>
              <button onClick={() => { cancelMicrosoftAuthAttempt(); setSelectedConnector(null); setTestResult(null); setCliTestResult(null); }}
                className="p-2 rounded-lg hover:bg-canvas text-muted hover:text-default">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 sm:p-6">
              {connectorDetail(activeConnector)}

              {/* Test/Error results */}
              {testResult && (
                <div className={`mt-4 p-3 rounded-xl text-sm border ${testResult.success ? 'border-[var(--color-success)]/25 bg-[var(--color-success)]/5' : 'border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5'}`}>
                  <p className={`font-semibold ${testResult.success ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'}`}>
                    {testResult.success ? "Success" : testResult.isKeyVaultError ? "Key Vault Permission Error" : "Connection Failed"}
                  </p>
                  <p className="text-muted mt-1">{testResult.message}</p>
                  {!testResult.success && testResult.technicalDetail && (
                    <>
                      <button onClick={() => setShowTechDetails(!showTechDetails)}
                        className="text-xs text-muted hover:text-default underline mt-2">{showTechDetails ? "Hide" : "Show"} technical details</button>
                      {showTechDetails && (
                        <pre className="text-xs text-muted bg-surface p-2 rounded-lg mt-2 overflow-x-auto font-mono border border-default whitespace-pre-wrap">
                          {testResult.technicalDetail}
                          {testResult.requestId && `\n\nRequest ID: ${testResult.requestId}`}
                          {testResult.trace?.trace_id && `\nTrace ID: ${testResult.trace.trace_id}`}
                        </pre>
                      )}
                    </>
                  )}
                </div>
              )}

              {cliTestResult && (
                <div className={`mt-4 p-3 rounded-xl text-sm space-y-2 border ${panelToneClass(getStatusTone(cliTestResult.status, cliTestResult.success === false))}`}>
                  <p className={`font-semibold ${panelTitleClass(getStatusTone(cliTestResult.status, cliTestResult.success === false))}`}>
                    {connectorDisplayNameForKey(cliTestResult.connector, connectorMeta)} — {cliResultHeading(cliTestResult)}
                  </p>
                  {cliTestResult.request_id && (
                    <p className="text-[10px] text-muted font-mono">Request ID: {cliTestResult.request_id}</p>
                  )}
                  {(cliTestResult.commands?.length ?? 0) > 0 ? (
                    <div className="space-y-2">
                      {(cliTestResult.commands ?? []).map((cmd, i) => (
                        <details key={i} className="border border-default rounded-lg p-2 bg-surface/50">
                          <summary className={`text-xs cursor-pointer font-mono ${cmd.exit_code === 0 ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'}`}>
                            {cmd.exit_code === 0 ? "✓" : "✗"} {cmd.command?.substring(0, 60)}...
                          </summary>
                          <div className="mt-1 text-[10px] font-mono text-muted space-y-1">
                            {cmd.stdout && <pre className="whitespace-pre-wrap">{cmd.stdout}</pre>}
                            {cmd.stderr && <pre className="text-[var(--color-danger)] whitespace-pre-wrap">{cmd.stderr}</pre>}
                            {cmd.error_message && <p className="text-[var(--color-danger)]">{cmd.error_message}</p>}
                            <p>Exit code: {cmd.exit_code}</p>
                          </div>
                        </details>
                      ))}
                    </div>
                  ) : (
                    <pre className="text-xs text-muted font-mono whitespace-pre-wrap overflow-x-auto max-h-60">
                      {cliTestResult.stdout || cliTestResult.stderr || cliTestResult.message || "No output"}
                    </pre>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
