import { useState, useEffect, useCallback } from "react";
import type { ReactNode } from "react";
import {
  Database, BookOpen, RefreshCw, CheckCircle2,
  AlertTriangle, Trash2, Terminal, GitBranch,
  ChevronRight, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
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
  icon: LucideIcon;
}

interface ConnectorMeta {
  connector_key?: string;
  status?: string;
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

interface AzureDeviceCode {
  status: string;
  device_code: string;
  user_code: string;
  verification_url: string;
  interval?: number;
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

type StatusTone = "success" | "danger" | "warning" | "neutral";

function getStatusTone(status?: string, hasError = false): StatusTone {
  if (hasError) return "danger";
  if (status === "connected" || status === "active") return "success";
  if (status === "error") return "danger";
  if (status === "needs_token" || status === "needs_setup" || status === "not_connected" || status === "expired") return "warning";
  return "neutral";
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
  const Icon = connector.icon;

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="p-2.5 rounded-xl bg-canvas border border-default shrink-0">
          <Icon className="w-5 h-5 text-muted" />
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

const CONNECTORS: ConnectorDef[] = [
  { key: "odoo", name: "Odoo Enterprise", subtitle: "ERP Proxy Connector", icon: Database },
  { key: "azure", name: "Azure CLI", subtitle: "Native Azure CLI", icon: Terminal },
  { key: "github", name: "GitHub CLI", subtitle: "Native GitHub CLI", icon: GitBranch },
];

interface ConnectionsPageProps { accessToken: string; }

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
  const [azureDeviceCode, setAzureDeviceCode] = useState<AzureDeviceCode | null>(null);
  const [azurePolling, setAzurePolling] = useState(false);
  const [connectorMeta, setConnectorMeta] = useState<Record<string, ConnectorMeta> | null>(null);
  const [connectorStatusError, setConnectorStatusError] = useState<string | null>(null);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

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

  const handleConnectAzure = async () => {
    if (!accessToken) return; setCliTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/azure/device-code`, { method: "POST", headers: headers() });
      const data = await res.json() as AzureDeviceCode & { error?: string };
      if (data.status === "device_code_ready") {
        setAzureDeviceCode(data);
        setAzurePolling(true);
        window.open(data.verification_url, "_blank");
        // Start polling
        const poll = async () => {
          try {
            const pr = await fetch(`${APIM_BASE_URL}/connector/azure/token-callback`, {
              method: "POST", headers: headers(),
              body: JSON.stringify({ device_code: data.device_code }),
            });
            const pd = await pr.json() as { status: string; error?: string; message?: string; interval?: number };
            if (pd.status === "connected") {
              setAzurePolling(false);
              setAzureDeviceCode(null);
              setCliTestResult({ status: "success", connector: "azure", message: "Azure connected!" });
              void fetchConnectors();
            } else if (pd.status === "pending") {
              setTimeout(poll, (pd.interval || data.interval || 5) * 1000);
            } else {
              setAzurePolling(false);
              setCliTestResult({ status: "failed", connector: "azure", message: pd.message || pd.error || "Auth failed" });
              void fetchConnectors();
            }
          } catch { setAzurePolling(false); }
        };
        setTimeout(poll, (data.interval || 5) * 1000);
      } else {
        setCliTestResult({ status: "failed", connector: "azure", message: data.error || "Failed to start device code flow" });
      }
    } catch (err) { setCliTestResult({ status: "failed", connector: "azure", message: errorMessage(err) }); }
  };

  const handleAzureStatus = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/azure/diagnose`, { method: "POST", headers: headers() });
      const data = await res.json() as { status?: string; message?: string; stderr?: string; request_id?: string };
      if (data.status === "success") {
        setCliTestResult({ status: "success", connector: "azure", message: data.message || "Azure connected", request_id: data.request_id });
        await fetchConnectors();
      } else {
        setCliTestResult({ status: "failed", connector: "azure", message: data.message || `Azure status: ${formatStatusLabel(data.status || "not_connected")}`, stderr: data.stderr, request_id: data.request_id });
        await fetchConnectors();
      }
    } catch { /* ignore transient Azure status errors */ }
  };

  const handleAzureDisconnect = async () => {
    if (!accessToken) return;
    await fetch(`${APIM_BASE_URL}/connector/azure/disconnect`, { method: "POST", headers: headers() });
    await fetchConnectors();
    setCliTestResult({ status: "success", connector: "azure", message: "Disconnected" });
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
    const c = CONNECTORS.find(x => x.key === key);
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

    if (key === "azure") return (
      <ConnectorDetailShell connector={c} status={metaStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
        <DetailCard>
          <p className="text-sm text-muted">Connect with Microsoft device authentication.</p>
        </DetailCard>

        <ActionGroup>
          <GlassButton size="sm" onClick={handleConnectAzure} disabled={azurePolling}>
            {azurePolling ? "Waiting for authentication..." : "Connect with Microsoft"}
          </GlassButton>
          <GlassButton size="sm" onClick={handleAzureStatus}>
            <CheckCircle2 className="w-3.5 h-3.5" /> Check Status
          </GlassButton>
          <GlassButton size="sm" variant="danger" onClick={handleAzureDisconnect}>
            <Trash2 className="w-3.5 h-3.5" /> Disconnect
          </GlassButton>
        </ActionGroup>

        {azureDeviceCode && (
          <DetailCard>
            <div className="text-sm space-y-2">
              <p className="font-semibold text-default">Device Code: <span className="font-mono text-lg">{azureDeviceCode.user_code}</span></p>
              <p className="text-muted text-xs">
                Open <a href={azureDeviceCode.verification_url} target="_blank" rel="noopener noreferrer" className="underline">{azureDeviceCode.verification_url}</a> and enter the code above.
              </p>
            </div>
          </DetailCard>
        )}
      </ConnectorDetailShell>
    );

    if (key === "github") return (
      <ConnectorDetailShell connector={c} status={metaStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
        <DetailCard>
          <p className="text-sm text-muted">Connect with GitHub OAuth.</p>
        </DetailCard>

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

  const availableConnectors = connectorMeta
    ? CONNECTORS.filter((connector) => connectorMeta[connector.key])
    : [];
  const activeConnector = selectedConnector && (!connectorMeta || connectorMeta[selectedConnector])
    ? selectedConnector
    : null;

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-fade-in">
      <GlassPanel className="p-8 rounded-3xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-default mb-2">Connected Accounts</h2>
          <p className="text-sm text-muted max-w-2xl">
            Connect third-party integrations. Credentials are stored securely in Azure Key Vault.
          </p>
        </div>
        <BookOpen className="w-12 h-12 text-soft shrink-0" />
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
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {availableConnectors.map((c) => {
          const meta = connectorMeta?.[c.key];
          const status = meta?.status;
          return (
          <button key={c.key} onClick={() => { setSelectedConnector(c.key); setTestResult(null); setCliTestResult(null); setAzureDeviceCode(null); setAzurePolling(false); }}
            className="text-left w-full p-5 rounded-2xl border border-default bg-surface hover:bg-canvas transition-colors cursor-pointer group">
            <div className="flex items-start justify-between mb-3">
              <div className="p-2.5 rounded-xl bg-surface border border-default">
                <c.icon className="w-5 h-5 text-muted" />
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
      ) : null}

      {/* Detail Drawer */}
      {activeConnector && (
        <div className="fixed inset-0 bg-canvas/80 backdrop-blur-sm z-50 flex justify-end animate-fade-in">
          <div className="w-full max-w-lg bg-surface border-l border-default overflow-y-auto">
            <div className="p-6 border-b border-default flex items-center justify-between">
              <h2 className="font-bold text-lg text-default">
                {CONNECTORS.find(c => c.key === activeConnector)?.name || activeConnector}
              </h2>
              <button onClick={() => { setSelectedConnector(null); setTestResult(null); setCliTestResult(null); setAzureDeviceCode(null); setAzurePolling(false); }}
                className="p-2 rounded-lg hover:bg-canvas text-muted hover:text-default">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6">
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
                <div className={`mt-4 p-3 rounded-xl text-sm space-y-2 border ${cliTestResult.status === 'success' ? 'border-[var(--color-success)]/25 bg-[var(--color-success)]/5' : 'border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5'}`}>
                  <p className={`font-semibold ${cliTestResult.status === 'success' ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'}`}>
                    {cliTestResult.connector === "azure" ? "Azure CLI" : "GitHub CLI"} — {cliTestResult.status === "success" ? "All checks passed" : "Issues found"}
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
