import { useState, useEffect, useCallback, useRef } from "react";
import type { ReactNode } from "react";
import {
  Plug, RefreshCw, CheckCircle2,
  AlertTriangle, Trash2, GitBranch,
  ChevronRight, Search, X, FileText, Wrench,
} from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassInput } from "../components/ui/GlassInput";
import { APIM_BASE_URL, fetchWithTimeout, isAbortError } from "../hooks/useApi";

const KV_ERROR_PHRASES = [
  "forbiddenbyrbac", "setsecret/action", "key vault secrets officer",
  "rbac", "authorization failed", "authorizationfailed",
];

interface ConnectorDef {
  key: string;
  name: string;
  subtitle: string;
}

interface ConnectorMeta {
  connector_key?: string;
  display_name?: string;
  subtitle?: string;
  status?: string;
  auth_method?: string;
  last_verified_at?: string | null;
  state?: {
    configured?: boolean;
    account_status?: string;
    token_status?: string;
    diagnostics_status?: string;
    cli_status?: string;
    readiness_status?: string;
    source?: string;
  };
  metadata?: {
    provider_username?: string | null;
    permission_summary?: string | null;
    overall_status?: string | null;
    tooling?: string[];
    auth_app_name?: string | null;
    native_connector?: boolean;
    odoo_url?: string | null;
    odoo_db?: string | null;
  };
}

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

interface OdooStatus {
  status: string;
  odoo_url?: string;
  odoo_db?: string;
  provider_username?: string;
  target_environment?: string;
  last_verified_at?: string;
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

interface MicrosoftNativeDeviceCode {
  status: string;
  connector?: string;
  auth_session_id?: string;
  device_code: string;
  user_code: string;
  verification_url: string;
  site_url?: string | null;
  scope_label?: string;
  scope_summary?: string;
  auth_app_name?: string;
  client_id?: string;
  interval?: number;
  expires_in?: number;
  expires_at?: number;
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

function formatStatusLabel(status: string) {
  return status
    .split("_")
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatOptionalStatus(status?: string | null) {
  return status ? formatStatusLabel(status) : "—";
}

function formatDateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function currentTimeMs() {
  return new Date().getTime();
}

type StatusTone = "success" | "danger" | "warning" | "neutral";

function getStatusTone(status?: string, hasError = false): StatusTone {
  if (hasError) return "danger";
  if (status === "connected" || status === "active" || status === "authorized" || status === "available" || status === "ready") return "success";
  if (status === "error" || status === "failed") return "danger";
  if (
    status === "partial"
    || status === "limited"
    || status === "warning"
    || status === "read_only"
    || status === "needs_token"
    || status === "needs_setup"
    || status === "setup_required"
    || status === "not_connected"
    || status === "expired"
    || status === "missing"
    || status === "missing_consent"
    || status === "missing_permission"
    || status === "not_checked"
  ) return "warning";
  return "neutral";
}

function panelToneClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "border-[var(--color-success)]/25 bg-[var(--color-success)]/5";
    case "danger":
      return "border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5";
    case "warning":
      return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/5";
    default:
      return "border-default bg-surface/50";
  }
}

function panelTitleClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "text-[var(--color-success)]";
    case "danger":
      return "text-[var(--color-danger)]";
    case "warning":
      return "text-[var(--color-warning)]";
    default:
      return "text-default";
  }
}

function cliResultHeading(result: CliTestResult) {
  const tone = getStatusTone(result.status, result.success === false);
  if (tone === "success") return "Ready";
  if (tone === "warning") return "Attention needed";
  return "Issues found";
}

function statusBadgeClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "text-[var(--color-success)] bg-[var(--color-success)]/10";
    case "danger":
      return "text-[var(--color-danger)] bg-[var(--color-danger)]/10";
    case "warning":
      return "text-[var(--color-warning)] bg-[var(--color-warning)]/10";
    default:
      return "text-muted bg-surface";
  }
}

function StatusBadge({
  status,
  fallback,
  hasError,
}: {
  status?: string;
  fallback: string;
  hasError?: boolean;
}) {
  const tone = getStatusTone(status, hasError);
  const label = status ? formatStatusLabel(status) : fallback;

  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1 rounded-full ${statusBadgeClass(tone)}`}>
      {tone === "success" ? <CheckCircle2 className="w-3 h-3" /> : null}
      {tone === "danger" ? <AlertTriangle className="w-3 h-3" /> : null}
      {label}
    </span>
  );
}

function ActionGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {children}
    </div>
  );
}

function FormField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] font-bold uppercase tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}

function DetailCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-default bg-canvas p-4">
      {children}
    </div>
  );
}

function InfoGrid({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <DetailCard>
      <dl className="grid grid-cols-[128px_1fr] gap-x-4 gap-y-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted">{row.label}</dt>
            <dd className="text-default min-w-0 break-words">{row.value}</dd>
          </div>
        ))}
      </dl>
    </DetailCard>
  );
}

function ConnectorLogo({ connectorKey, className = "w-5 h-5" }: { connectorKey: string; className?: string }) {
  if (connectorKey === "odoo") {
    return (
      <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Odoo logo">
        <path
          fill="#714B67"
          d="M21.1002 15.7957c-1.6015 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2979 2.8998-2.8998 2.8998zm0-1.2c.9388.0006 1.7003-.7601 1.7008-1.6989.0004-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zm-6.0655 1.2c-1.6014 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2999 2.8998-2.8998 2.8998zm0-1.2c.9389.0006 1.7003-.7601 1.7008-1.6989.0005-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zM11.865 12.858c0 1.6199-1.2979 2.9378-2.8977 2.9378s-2.8998-1.314-2.8998-2.9358 1.1799-2.8597 2.8998-2.8597c.6359 0 1.2239.134 1.6998.484v-1.68a.6.6 0 0 1 1.2 0v4.0537h-.002zm-2.8977 1.7399c.9388.0005 1.7002-.7602 1.7007-1.699.0005-.9388-.7602-1.7003-1.699-1.7007h-.0017c-.9389.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699zm-6.0675 1.1979C1.2983 15.7957 0 14.4974 0 12.8959s1.2983-2.8997 2.8998-2.8997 2.8997 1.2982 2.8997 2.8997c0 1.5999-1.2999 2.8998-2.8997 2.8998zm0-1.2c.9388.0006 1.7002-.7601 1.7007-1.699.0005-.9387-.7602-1.7002-1.699-1.7006h-.0017c-.9388.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699z"
        />
      </svg>
    );
  }

  if (isMicrosoftNativeConnector(connectorKey)) {
    return (
      <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Microsoft connector logo">
        <rect x="2.5" y="2.5" width="8.5" height="8.5" rx="1" fill="#F25022" />
        <rect x="13" y="2.5" width="8.5" height="8.5" rx="1" fill="#7FBA00" />
        <rect x="2.5" y="13" width="8.5" height="8.5" rx="1" fill="#00A4EF" />
        <rect x="13" y="13" width="8.5" height="8.5" rx="1" fill="#FFB900" />
      </svg>
    );
  }

  if (connectorKey === "github") {
    return (
      <svg className={`${className} text-default`} viewBox="0 0 24 24" role="img" aria-label="GitHub logo">
        <path
          fill="currentColor"
          d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
        />
      </svg>
    );
  }

  return <div className={`${className} rounded-full bg-muted`} />;
}

function ToolLogo({ toolName, className = "w-5 h-5" }: { toolName: string; className?: string }) {
  if (toolName === "document_reader") {
    return <FileText className={`${className} text-[var(--color-info)]`} />;
  }
  return <Wrench className={`${className} text-default`} />;
}

function ConnectorDetailShell({
  connector,
  status,
  fallback,
  hasStatusError,
  children,
}: {
  connector: ConnectorDef;
  status?: string;
  fallback: string;
  hasStatusError?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="p-2.5 rounded-xl bg-canvas border border-default shrink-0">
          <ConnectorLogo connectorKey={connector.key} />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-bold text-base text-default">{connector.name}</h3>
          <p className="text-xs text-muted mt-0.5">{connector.subtitle}</p>
        </div>
        <StatusBadge status={status} fallback={fallback} hasError={hasStatusError} />
      </div>
      {children}
    </div>
  );
}

const MICROSOFT_NATIVE_CONNECTOR_KEYS = [
  "azure_cli",
  "microsoft_graph",
  "exchange_online",
  "teams_admin",
  "sharepoint_pnp",
];
const MICROSOFT_NATIVE_CONNECTOR_KEY_SET = new Set(MICROSOFT_NATIVE_CONNECTOR_KEYS);
const CONNECTOR_TOOL_TARGET_SYSTEMS = new Set([
  "odoo",
  "github",
  ...MICROSOFT_NATIVE_CONNECTOR_KEYS,
]);

function isMicrosoftNativeConnector(key: string) {
  return MICROSOFT_NATIVE_CONNECTOR_KEY_SET.has(key);
}

const CONNECTOR_FALLBACKS: ConnectorDef[] = [
  { key: "odoo", name: "Odoo", subtitle: "ERP connector" },
  { key: "azure_cli", name: "Azure CLI", subtitle: "Native Azure CLI, Azure PowerShell, and Bicep" },
  { key: "microsoft_graph", name: "Microsoft Graph", subtitle: "Microsoft Graph and Graph PowerShell" },
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

  const handleConnectOdoo = async (e: React.FormEvent) => {
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
        setCliTestResult({ status: "failed", connector: connectorKey, message: "SharePoint / PnP sign-in requires a SharePoint site or admin URL." });
        return;
      }
      connectPayload.site_url = siteUrl.trim();
    }
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
        window.open(data.verification_url, "_blank");
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
        setMicrosoftStartingConnector(null);
        setMicrosoftDeviceCode(null);
        setMicrosoftPollingConnector(null);
        setCliTestResult({ status: "failed", connector: connectorKey, message: data.message || data.error || "Failed to start device code flow", request_id: data.request_id });
      }
    } catch (err) {
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
    const metaStatus = connectorMeta?.[key]?.status;
    const statusFallback = connectorStatusError ? "Status Unavailable" : "Checking...";
    const hasStatusError = Boolean(connectorStatusError && !metaStatus);

    if (key === "odoo") {
      const isOdooStatusLoaded = odooStatus !== null;
      const isOdooDisconnected = odooStatus?.status === "not_connected";
      const detailStatus = metaStatus || odooStatus?.status;

      return (
        <ConnectorDetailShell connector={c} status={detailStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
          {!isOdooStatusLoaded ? (
            <DetailCard>
              <p className="text-sm text-muted">Loading connection details...</p>
            </DetailCard>
          ) : !isOdooDisconnected ? (
            <InfoGrid
              rows={[
                { label: "Status", value: formatStatusLabel(odooStatus.status) },
                { label: "Instance URL", value: <span className="break-all">{odooStatus.odoo_url || "—"}</span> },
                { label: "Database", value: odooStatus.odoo_db || "—" },
                { label: "Username", value: odooStatus.provider_username || "—" },
                { label: "Environment", value: odooStatus.target_environment || "—" },
                {
                  label: "Last Verified",
                  value: odooStatus.last_verified_at ? new Date(odooStatus.last_verified_at).toLocaleString() : "—",
                },
              ]}
            />
          ) : (
            <DetailCard>
              <p className="text-sm text-muted">Not connected.</p>
            </DetailCard>
          )}

          {isOdooStatusLoaded && !isOdooDisconnected ? (
            <ActionGroup>
              <GlassButton size="sm" onClick={handleTestOdoo} disabled={isTesting}>
                <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? "animate-spin" : ""}`} /> Test
              </GlassButton>
              <GlassButton size="sm" variant="danger" onClick={handleDisconnectOdoo}>
                <Trash2 className="w-3.5 h-3.5" /> Disconnect
              </GlassButton>
            </ActionGroup>
          ) : null}

          {isOdooStatusLoaded && isOdooDisconnected && (
            <form onSubmit={handleConnectOdoo} className="space-y-4">
              <div className="grid gap-3">
                <FormField label="Instance URL">
                  <GlassInput type="url" required placeholder="https://your-odoo-instance.com" value={odooUrl} onChange={e => setOdooUrl(e.target.value)} />
                </FormField>
                <FormField label="Database">
                  <GlassInput type="text" required placeholder="Database name" value={odooDb} onChange={e => setOdooDb(e.target.value)} />
                </FormField>
                <FormField label="Username">
                  <GlassInput type="email" required placeholder="user@example.com" value={odooUsername} onChange={e => setOdooUsername(e.target.value)} />
                </FormField>
                <FormField label="API Key">
                  <GlassInput type="password" required placeholder="Odoo API key" value={odooApiKey} onChange={e => setOdooApiKey(e.target.value)} />
                </FormField>
              </div>
              <ActionGroup>
                <GlassButton type="submit" disabled={isConnecting}>
                  {isConnecting ? "Connecting..." : "Verify & Save"}
                </GlassButton>
              </ActionGroup>
            </form>
          )}
        </ConnectorDetailShell>
      );
    }

    if (isMicrosoftNativeConnector(key)) {
      const meta = connectorMeta?.[key];
      const readinessStatus = meta?.state?.readiness_status
        || meta?.metadata?.overall_status
        || metaStatus;
      const tooling = meta?.metadata?.tooling || [];
      const authAppName = meta?.metadata?.auth_app_name;
      const isPolling = microsoftPollingConnector === key;
      const isStarting = microsoftStartingConnector === key;
      const activeDeviceCode = microsoftDeviceCode?.connectorKey === key ? microsoftDeviceCode : null;
      return (
        <ConnectorDetailShell connector={c} status={readinessStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
          <DetailCard>
            <div className="space-y-2 text-sm text-muted">
              <p>
                Separate native Microsoft connector. This stores only the {c.name} token; access is still limited by the signed-in user's Microsoft roles, Azure RBAC, workload permissions, and tenant consent.
              </p>
              {authAppName ? <p className="text-xs">Microsoft sign-in app: {authAppName}</p> : null}
              {tooling.length > 0 ? <p className="text-xs">Tools: {tooling.join(", ")}</p> : null}
            </div>
          </DetailCard>

          <InfoGrid
            rows={[
              { label: "Account", value: formatOptionalStatus(meta?.state?.account_status || metaStatus) },
              { label: "Readiness", value: formatOptionalStatus(readinessStatus) },
              { label: "Token", value: formatOptionalStatus(meta?.state?.token_status) },
              { label: "Diagnostics", value: formatOptionalStatus(meta?.state?.diagnostics_status) },
              { label: "Shell", value: formatOptionalStatus(meta?.state?.cli_status) },
              { label: "User", value: meta?.metadata?.provider_username || "—" },
              { label: "Last Verified", value: formatDateTime(meta?.last_verified_at) },
            ]}
          />

          <ActionGroup>
            <GlassButton size="sm" onClick={() => handleConnectMicrosoftNative(key)} disabled={Boolean(microsoftStartingConnector || microsoftPollingConnector)}>
              {isStarting ? "Starting sign-in..." : isPolling ? "Waiting for authentication..." : metaStatus === "connected" ? "Refresh Sign-In" : `Connect ${c.name}`}
            </GlassButton>
            <GlassButton size="sm" onClick={() => handleMicrosoftNativeStatus(key)}>
              <CheckCircle2 className="w-3.5 h-3.5" /> Check Status
            </GlassButton>
            <GlassButton size="sm" variant="danger" onClick={() => handleMicrosoftNativeDisconnect(key)}>
              <Trash2 className="w-3.5 h-3.5" /> Disconnect
            </GlassButton>
          </ActionGroup>

          {activeDeviceCode && (
            <DetailCard>
              <div className="text-sm space-y-3">
                <div className="space-y-1">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted">{c.name} sign-in</p>
                  <p className="font-semibold text-default">Device Code: <span className="font-mono text-lg">{activeDeviceCode.user_code}</span></p>
                  <p className="text-muted text-xs">
                    {activeDeviceCode.scope_label || c.name} via {activeDeviceCode.auth_app_name || "Microsoft"}.
                  </p>
                  <p className="text-muted text-xs">
                    Open <a href={activeDeviceCode.verification_url} target="_blank" rel="noopener noreferrer" className="underline">{activeDeviceCode.verification_url}</a> and enter the code above.
                  </p>
                </div>
                <ActionGroup>
                  <GlassButton size="sm" onClick={() => window.open(activeDeviceCode.verification_url, "_blank", "noopener,noreferrer")}>
                    Open Microsoft Sign-In
                  </GlassButton>
                </ActionGroup>
              </div>
            </DetailCard>
          )}
        </ConnectorDetailShell>
      );
    }

    if (key === "github") return (
      <ConnectorDetailShell connector={c} status={metaStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
        <DetailCard>
          <p className="text-sm text-muted">Connect with GitHub OAuth.</p>
        </DetailCard>

        <InfoGrid
          rows={[
            { label: "Account", value: formatOptionalStatus(connectorMeta?.github?.state?.account_status || metaStatus) },
            { label: "Token", value: formatOptionalStatus(connectorMeta?.github?.state?.token_status) },
            { label: "Diagnostics", value: formatOptionalStatus(connectorMeta?.github?.state?.diagnostics_status) },
            { label: "CLI", value: formatOptionalStatus(connectorMeta?.github?.state?.cli_status) },
            { label: "User", value: connectorMeta?.github?.metadata?.provider_username || "—" },
            { label: "Last Verified", value: formatDateTime(connectorMeta?.github?.last_verified_at) },
          ]}
        />

        <ActionGroup>
          <GlassButton size="sm" onClick={handleGithubOAuth}>
            <GitBranch className="w-4 h-4" /> Connect with GitHub
          </GlassButton>
          <GlassButton size="sm" onClick={handleGithubStatus}>
            <CheckCircle2 className="w-3.5 h-3.5" /> Check Status
          </GlassButton>
        </ActionGroup>
      </ConnectorDetailShell>
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
