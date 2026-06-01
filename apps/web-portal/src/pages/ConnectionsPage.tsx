import { useState, useEffect } from "react";
import {
  Database, BookOpen, RefreshCw, CheckCircle2,
  AlertTriangle, Trash2, Terminal, GitBranch,
  ChevronRight, X,
} from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassInput } from "../components/ui/GlassInput";

const APIM_BASE_URL =
  import.meta.env.VITE_APIM_BASE_URL ||
  "https://apim-ai-platform-prod-san-001.azure-api.net";

const APP_COMMIT_SHA = import.meta.env.VITE_APP_COMMIT_SHA || "dev";
console.debug("[App Version] commit_sha:", APP_COMMIT_SHA);

const KV_ERROR_PHRASES = [
  "forbiddenbyrbac", "setsecret/action", "key vault secrets officer",
  "rbac", "authorization failed", "authorizationfailed",
];

interface ConnectorDef {
  key: string;
  name: string;
  subtitle: string;
  icon: any;
  status: string;
  statusLabel: string;
  statusColor: string;
  primaryAction: string;
  authMethod?: string;
  lastVerified?: string;
}

const CONNECTORS: ConnectorDef[] = [
  { key: "odoo", name: "Odoo Enterprise", subtitle: "ERP Proxy Connector", icon: Database,
    status: "not_connected", statusLabel: "Not Connected", statusColor: "text-muted", primaryAction: "Connect" },
  { key: "azure_cli", name: "Azure CLI", subtitle: "Native Azure CLI", icon: Terminal,
    status: "active", statusLabel: "Active", statusColor: "text-[var(--color-success)]", primaryAction: "Open", authMethod: "Managed Identity" },
  { key: "github_cli", name: "GitHub CLI", subtitle: "Native GitHub CLI", icon: GitBranch,
    status: "needs_token", statusLabel: "Needs Token", statusColor: "text-[var(--color-warning)]", primaryAction: "Connect", authMethod: "Token Auth" },
  { key: "ms365", name: "Microsoft 365", subtitle: "SharePoint / Outlook / Graph", icon: BookOpen,
    status: "coming_soon", statusLabel: "Coming Soon", statusColor: "text-soft", primaryAction: "Coming Soon" },
];

interface ConnectionsPageProps { accessToken: string; }

export function ConnectionsPage({ accessToken }: ConnectionsPageProps) {
  const [odooStatus, setOdooStatus] = useState<any>({ status: "not_connected" });
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [showTechDetails, setShowTechDetails] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState<string | null>(null);
  const [odooUrl, setOdooUrl] = useState("");
  const [odooDb, setOdooDb] = useState("");
  const [odooUsername, setOdooUsername] = useState("alden@lotslotsmore.com");
  const [odooApiKey, setOdooApiKey] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [githubOrg, setGithubOrg] = useState("aldenbronkhorst");
  const [cliTestResult, setCliTestResult] = useState<any>(null);
  const [cliTesting, setCliTesting] = useState(false);

  const headers = () => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  });

  const fetchOdooStatus = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/status`, { headers: headers() });
      if (res.ok) {
        const data = await res.json();
        setOdooStatus(data);
        if (data.status === "connected" || data.status === "error") {
          if (data.odoo_url) setOdooUrl(data.odoo_url);
          if (data.odoo_db) setOdooDb(data.odoo_db);
          if (data.provider_username) setOdooUsername(data.provider_username);
        }
      } else {
        setOdooStatus({ status: "not_connected" });
      }
    } catch { setOdooStatus({ status: "not_connected" }); }
  };

  useEffect(() => { if (accessToken) fetchOdooStatus(); }, [accessToken]);

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
      const data = await res.json();
      if (res.ok) {
        setTestResult({ success: true, message: "Odoo connection established!" });
        setSelectedConnector(null); setOdooApiKey(""); fetchOdooStatus();
      } else {
        const detail = data.detail || {};
        setTestResult({
          success: false, message: detail.message || data.detail || "Connection failed.",
          isKeyVaultError: isKeyVaultError(detail.message || ""),
          errorType: detail.error_type || "", stage: detail.stage || "",
          technicalDetail: detail.technical_detail || "", requestId: detail.request_id || "",
          connectionAttemptId: detail.connection_attempt_id || "", trace: detail.trace || null,
        });
        fetchOdooStatus();
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Could not reach backend: ${err.message}` });
    } finally { setIsConnecting(false); }
  };

  const handleTestOdoo = async () => {
    if (!accessToken) return; setIsTesting(true); setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/test`, { method: "POST", headers: headers() });
      const data = await res.json();
      if (res.ok) setTestResult({ success: data.status === "connected", message: `Connection state: ${data.status.toUpperCase()}` });
      else setTestResult({ success: false, message: data.detail || "Verification failed." });
      fetchOdooStatus();
    } catch (err: any) { setTestResult({ success: false, message: `Test failed: ${err.message}` }); }
    finally { setIsTesting(false); }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken || !confirm("Disconnect Odoo? Credentials will be permanently deleted.")) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/disconnect`, { method: "POST", headers: headers() });
      if (res.ok) { setOdooUrl(""); setOdooDb(""); setOdooUsername("alden@lotslotsmore.com"); setOdooApiKey(""); setTestResult(null); }
      fetchOdooStatus();
    } catch { /* ignore */ }
  };

  const handleTestAzure = async () => {
    if (!accessToken) return; setCliTesting(true); setCliTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/azure/diagnose`, { method: "POST", headers: headers() });
      const data = await res.json();
      setCliTestResult({ ...data, connector: "azure_cli" });
    } catch (err: any) { setCliTestResult({ success: false, message: err.message }); }
    finally { setCliTesting(false); }
  };

  const handleTestGithub = async () => {
    if (!accessToken) return; setCliTesting(true); setCliTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/github/diagnose`, { method: "POST", headers: headers() });
      const data = await res.json();
      setCliTestResult({ ...data, connector: "github_cli" });
    } catch (err: any) { setCliTestResult({ success: false, message: err.message }); }
    finally { setCliTesting(false); }
  };

  const handleGithubOAuth = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${APIM_BASE_URL}/connector/github/auth-url`, { method: "GET", headers: headers() });
      const data = await res.json();
      if (data.auth_url) window.location.href = data.auth_url;
      else setCliTestResult({ success: false, message: data.message || "GitHub OAuth not configured." });
    } catch (err: any) { setCliTestResult({ success: false, message: err.message }); }
  };

  const handleConnectGithubToken = async () => {
    if (!accessToken || !githubToken) return; setIsConnecting(true); setCliTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/github_cli/connect`, {
        method: "POST", headers: headers(),
        body: JSON.stringify({ token: githubToken, org: githubOrg }),
      });
      const data = await res.json();
      setCliTestResult({ success: res.ok, ...data });
    } catch (err: any) { setCliTestResult({ success: false, message: err.message }); }
    finally { setIsConnecting(false); }
  };

  const connectorDetail = (key: string) => {
    const c = CONNECTORS.find(x => x.key === key);
    if (!c) return null;

    if (key === "odoo") return (
      <div className="space-y-4">
        <h3 className="font-bold text-lg text-default mb-2">{c.name}</h3>
        {odooStatus.status !== "not_connected" ? (
          <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-sm p-3 bg-canvas rounded-xl">
            <span className="text-muted">Status</span>
            <span className="text-default">{odooStatus.status}</span>
            <span className="text-muted">Instance URL</span>
            <span className="text-default break-all">{odooStatus.odoo_url || "—"}</span>
            <span className="text-muted">Database</span>
            <span className="text-default">{odooStatus.odoo_db || "—"}</span>
            <span className="text-muted">Username</span>
            <span className="text-default">{odooStatus.provider_username || "—"}</span>
            <span className="text-muted">Environment</span>
            <span className="text-default">{odooStatus.target_environment || "—"}</span>
            <span className="text-muted">Last Verified</span>
            <span className="text-default">{odooStatus.last_verified_at ? new Date(odooStatus.last_verified_at).toLocaleString() : "—"}</span>
          </div>
        ) : (
          <p className="text-sm text-muted">Not connected.</p>
        )}
        <div className="flex flex-wrap gap-2">
          {odooStatus.status !== "not_connected" ? (
            <>
              <GlassButton size="sm" onClick={handleTestOdoo} disabled={isTesting}>
                <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? "animate-spin" : ""}`} /> Test
              </GlassButton>
              <GlassButton size="sm" variant="danger" onClick={handleDisconnectOdoo}><Trash2 className="w-3.5 h-3.5" /> Disconnect</GlassButton>
            </>
          ) : null}
        </div>
        {key === "odoo" && odooStatus.status === "not_connected" && (
          <form onSubmit={handleConnectOdoo} className="space-y-3 pt-2">
            <GlassInput type="url" required placeholder="Odoo Instance URL" value={odooUrl} onChange={e => setOdooUrl(e.target.value)} />
            <GlassInput type="text" required placeholder="Odoo Database Name" value={odooDb} onChange={e => setOdooDb(e.target.value)} />
            <GlassInput type="email" required placeholder="Odoo Username / Email" value={odooUsername} onChange={e => setOdooUsername(e.target.value)} />
            <GlassInput type="password" required placeholder="Odoo API Key" value={odooApiKey} onChange={e => setOdooApiKey(e.target.value)} />
            <GlassButton type="submit" disabled={isConnecting}>{isConnecting ? "Connecting..." : "Verify & Save"}</GlassButton>
          </form>
        )}
      </div>
    );

    if (key === "azure_cli") return (
      <div className="space-y-4">
        <h3 className="font-bold text-lg text-default mb-2">{c.name}</h3>
        <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-sm p-3 bg-canvas rounded-xl">
          <span className="text-muted">Status</span><span className="text-default">Needs Verification</span>
          <span className="text-muted">Auth</span><span className="text-default">Managed Identity</span>
        </div>
        <GlassButton size="sm" onClick={handleTestAzure} disabled={cliTesting}>
          <RefreshCw className={`w-3.5 h-3.5 ${cliTesting ? "animate-spin" : ""}`} /> Run Azure Diagnostics
        </GlassButton>
      </div>
    );

    if (key === "github_cli") return (
      <div className="space-y-4">
        <h3 className="font-bold text-lg text-default mb-2">{c.name}</h3>
        <p className="text-sm text-muted mb-2">Connect your GitHub account to enable CLI access.</p>

        {/* Primary OAuth flow */}
        <GlassButton size="sm" onClick={handleGithubOAuth} className="w-full">
          <GitBranch className="w-4 h-4" /> Sign in with GitHub (OAuth)
        </GlassButton>

        {/* Manual token — collapsed by default */}
        <details className="text-sm">
          <summary className="cursor-pointer text-muted hover:text-default">Use token manually (advanced)</summary>
          <div className="mt-3 space-y-2">
            <GlassInput type="password" placeholder="ghp_..." value={githubToken} onChange={e => setGithubToken(e.target.value)} />
            <GlassInput type="text" placeholder="Organization / Owner" value={githubOrg} onChange={e => setGithubOrg(e.target.value)} />
            <GlassButton size="sm" onClick={handleConnectGithubToken} disabled={isConnecting || !githubToken}>
              {isConnecting ? "Connecting..." : "Connect with Token"}
            </GlassButton>
          </div>
        </details>

        <GlassButton size="sm" onClick={handleTestGithub} disabled={cliTesting}>
          <RefreshCw className={`w-3.5 h-3.5 ${cliTesting ? "animate-spin" : ""}`} /> Test GitHub CLI
        </GlassButton>
      </div>
    );

    return null;
  };

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

      <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
        {CONNECTORS.map((c) => (
          <button key={c.key} onClick={() => { setSelectedConnector(c.key); setTestResult(null); setCliTestResult(null); }}
            className="text-left w-full p-5 rounded-2xl border border-default bg-surface hover:bg-canvas transition-colors cursor-pointer group">
            <div className="flex items-start justify-between mb-3">
              <div className="p-2.5 rounded-xl bg-surface border border-default">
                <c.icon className="w-5 h-5 text-muted" />
              </div>
              <ChevronRight className="w-4 h-4 text-soft group-hover:text-default transition-colors" />
            </div>
            <h4 className="font-bold text-sm text-default truncate">{c.name}</h4>
            <p className="text-xs text-muted truncate mb-3">{c.subtitle}</p>
            <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${c.statusColor} bg-${c.statusColor.replace('text-', '')}/10 px-2.5 py-1 rounded-full`}>
              {c.status === "connected" || c.status === "active" ? <CheckCircle2 className="w-3 h-3" /> : null}
              {c.status === "error" ? <AlertTriangle className="w-3 h-3" /> : null}
              {c.statusLabel}
            </span>
          </button>
        ))}
      </div>

      {/* Detail Drawer */}
      {selectedConnector && (
        <div className="fixed inset-0 bg-canvas/80 backdrop-blur-sm z-50 flex justify-end animate-fade-in">
          <div className="w-full max-w-lg bg-surface border-l border-default overflow-y-auto">
            <div className="p-6 border-b border-default flex items-center justify-between">
              <h2 className="font-bold text-lg text-default">
                {CONNECTORS.find(c => c.key === selectedConnector)?.name || selectedConnector}
              </h2>
              <button onClick={() => { setSelectedConnector(null); setTestResult(null); setCliTestResult(null); }}
                className="p-2 rounded-lg hover:bg-canvas text-muted hover:text-default">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6">
              {connectorDetail(selectedConnector)}

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
                    {cliTestResult.connector === "azure_cli" ? "Azure CLI" : "GitHub CLI"} — {cliTestResult.status === "success" ? "All checks passed" : "Issues found"}
                  </p>
                  {cliTestResult.request_id && (
                    <p className="text-[10px] text-muted font-mono">Request ID: {cliTestResult.request_id}</p>
                  )}
                  {cliTestResult.commands?.length > 0 ? (
                    <div className="space-y-2">
                      {cliTestResult.commands.map((cmd: any, i: number) => (
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
