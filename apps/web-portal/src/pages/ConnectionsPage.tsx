import { useState, useEffect } from "react";
import {
  Database,
  BookOpen,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Plus,
  Trash2,
  Key,
} from "lucide-react";
import { GlassPanel } from "../components/ui/GlassPanel";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassInput } from "../components/ui/GlassInput";

const APIM_BASE_URL =
  import.meta.env.VITE_APIM_BASE_URL ||
  "https://apim-ai-platform-prod-san-001.azure-api.net";

interface ConnectionsPageProps {
  accessToken: string;
}

export function ConnectionsPage({ accessToken }: ConnectionsPageProps) {
  const [odooStatus, setOdooStatus] = useState<any>({ status: "not_connected" });
  const [isStatusLoading, setIsStatusLoading] = useState(false);
  const [isConnectOpen, setIsConnectOpen] = useState(false);
  const [isRotateOpen, setIsRotateOpen] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [showTechDetails, setShowTechDetails] = useState(false);

  const [odooUrl, setOdooUrl] = useState("");
  const [odooDb, setOdooDb] = useState("");
  const [odooUsername, setOdooUsername] = useState("alden@lotslotsmore.com");
  const [odooApiKey, setOdooApiKey] = useState("");

  const headers = () => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  });

  const fetchOdooStatus = async () => {
    if (!accessToken) return;
    setIsStatusLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/status`, {
        headers: headers(),
      });
      if (res.ok) {
        const data = await res.json();
        setOdooStatus(data);
        // Update form fields from status, or clear if disconnected
        if (data.status === "connected" || data.status === "error") {
          if (data.odoo_url) setOdooUrl(data.odoo_url);
          if (data.odoo_db) setOdooDb(data.odoo_db);
          if (data.provider_username) setOdooUsername(data.provider_username);
        } else {
          // Clear form when not connected
          setOdooUrl("");
          setOdooDb("");
          setOdooUsername("alden@lotslotsmore.com");
        }
      } else {
        // Handle API errors
        setOdooStatus({ status: "not_connected" });
        setOdooUrl("");
        setOdooDb("");
      }
    } catch (err) {
      console.error("Failed to fetch Odoo status:", err);
      setOdooStatus({ status: "not_connected" });
    } finally {
      setIsStatusLoading(false);
    }
  };

  useEffect(() => {
    if (accessToken) fetchOdooStatus();
  }, [accessToken]);

  const KV_ERROR_PHRASES = [
    "forbiddenbyrbac",
    "setsecret/action",
    "key vault secrets officer",
    "rbac",
    "authorization failed",
    "authorizationfailed",
  ];

  const isKeyVaultError = (msg: string) =>
    KV_ERROR_PHRASES.some((p) => msg.toLowerCase().includes(p));

  const getConnectorFriendlyMessage = (errorType: string, message: string) => {
    if (errorType === "odoo_connector_dns_failed") {
      return "AI Platform could not reach the Odoo Connector service. Please contact an administrator.";
    }
    if (errorType === "odoo_connector_unreachable") {
      return "AI Platform could not connect to the Odoo Connector service. Please contact an administrator.";
    }
    if (errorType === "odoo_connector_auth_failed") {
      return "Internal connector API key mismatch. Please contact an administrator.";
    }
    return message;
  };

  const handleConnectOdoo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    setIsConnecting(true);
    setTestResult(null);
    setShowTechDetails(false);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/connect`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ odoo_url: odooUrl, odoo_db: odooDb, odoo_username: odooUsername, odoo_api_key: odooApiKey }),
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult({ success: true, message: "Odoo connection established successfully!" });
        setIsConnectOpen(false);
        setOdooApiKey("");
        fetchOdooStatus();
      } else {
        // Try structured error detail first (error_type, stage, message, technical_detail)
        const detail = data.detail || {};
        const errorType = detail.error_type || "";
        const stage = detail.stage || "";
        const friendlyMessage = detail.message || data.detail || "Connection failed.";
        const techDetail = detail.technical_detail || "";
        const requestId = detail.request_id || "";
        setTestResult({
          success: false,
          message: friendlyMessage,
          isKeyVaultError: isKeyVaultError(friendlyMessage),
          errorType,
          stage,
          technicalDetail: techDetail,
          requestId,
        });
        // Refresh status even on failure — account may have been saved with status="error"
        fetchOdooStatus();
      }
    } catch (err: any) {
      setTestResult({
        success: false,
        message: `Could not reach backend: ${err.message}`,
      });
    } finally {
      setIsConnecting(false);
    }
  };

  const handleTestOdoo = async () => {
    if (!accessToken) return;
    setIsTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/test`, {
        method: "POST",
        headers: headers(),
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult({
          success: data.status === "connected",
          message: `Connection state: ${data.status.toUpperCase()}`,
        });
        fetchOdooStatus();
      } else {
        setTestResult({ success: false, message: data.detail || "Verification failed." });
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Test failed: ${err.message}` });
    } finally {
      setIsTesting(false);
    }
  };

  const handleRotateOdoo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    setIsConnecting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/rotate`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ odoo_api_key: odooApiKey }),
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult({ success: true, message: "Odoo credential rotated successfully!" });
        setIsRotateOpen(false);
        setOdooApiKey("");
        fetchOdooStatus();
      } else {
        setTestResult({ success: false, message: data.detail || "Rotation failed." });
      }
    } catch (err: any) {
      setTestResult({ success: false, message: `Rotation failed: ${err.message}` });
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken) return;
    if (!confirm("Disconnect Odoo? Credentials will be permanently deleted.")) return;
    setIsStatusLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/connected-accounts/odoo/disconnect`, {
        method: "POST",
        headers: headers(),
      });
      if (res.ok) {
        // Clear all form fields and stale status on successful disconnect
        setOdooUrl("");
        setOdooDb("");
        setOdooUsername("");
        setOdooApiKey("");
        setTestResult(null);
        setOdooStatus({ status: "not_connected" });
      }
    } catch (err) {
      console.error("Disconnect failed:", err);
    } finally {
      setIsStatusLoading(false);
      fetchOdooStatus();
    }
  };

  const statusBadge = () => {
    if (isStatusLoading)
      return (
        <span className="text-xs bg-surface text-muted px-3 py-1 rounded-full font-medium flex items-center gap-1.5">
          <RefreshCw className="w-3 h-3 animate-spin" /> Checking
        </span>
      );
    if (odooStatus.status === "connected")
      return (
        <span className="text-xs bg-[var(--color-success)]/10 text-[var(--color-success)] border border-[var(--color-success)]/25 px-3 py-1 rounded-full font-medium flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5" /> Connected
        </span>
      );
    if (odooStatus.status === "error")
      return (
        <span className="text-xs bg-[var(--color-danger)]/10 text-[var(--color-danger)] border border-[var(--color-danger)]/25 px-3 py-1 rounded-full font-medium flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" /> Credentials Error
        </span>
      );
    return (
      <span className="text-xs bg-surface text-muted border border-default px-3 py-1 rounded-full font-medium">
        Not Connected
      </span>
    );
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8 animate-fade-in">
      <GlassPanel className="p-8 rounded-3xl flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-default mb-2">Connected Accounts</h2>
          <p className="text-sm text-muted max-w-2xl">
            Connect third-party integrations. Credentials are stored securely in Azure Key Vault.
          </p>
        </div>
        <BookOpen className="w-12 h-12 text-soft shrink-0" />
      </GlassPanel>

      <div className="grid md:grid-cols-2 gap-6">
        <GlassPanel className="p-6 rounded-2xl flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-surface border border-default rounded-xl">
                  <Database className="w-6 h-6 text-muted" />
                </div>
                <div>
                  <h3 className="font-bold text-default leading-tight">Odoo Enterprise</h3>
                  <span className="text-xs text-muted font-mono">ERP Proxy Connector</span>
                </div>
              </div>
              {statusBadge()}
            </div>

            {(() => {
              const shouldShowOdooDetails =
                ["connected", "error", "needs_verification"].includes(odooStatus.status);
              const fmtLastVerified = odooStatus.last_verified_at
                ? new Date(odooStatus.last_verified_at).toLocaleString()
                : null;
              return (
                <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-3 py-4 border-t border-b border-default text-sm select-text">
                  <span className="text-muted font-medium">Instance URL:</span>
                  <span className="text-default min-w-0 break-words overflow-wrap-anywhere">
                    {shouldShowOdooDetails ? odooStatus.odoo_url : "—"}
                  </span>
                  <span className="text-muted font-medium">Database:</span>
                  <span className="text-default min-w-0 break-words overflow-wrap-anywhere">
                    {shouldShowOdooDetails ? odooStatus.odoo_db : "—"}
                  </span>
                  <span className="text-muted font-medium">Username:</span>
                  <span className="text-default min-w-0 break-words overflow-wrap-anywhere">
                    {shouldShowOdooDetails ? odooStatus.provider_username : "—"}
                  </span>
                  <span className="text-muted font-medium">Environment:</span>
                  <span className="text-default capitalize">
                    {shouldShowOdooDetails ? odooStatus.target_environment : "—"}
                  </span>
                  <span className="text-muted font-medium">Last Verified:</span>
                  <span className="text-default">
                    {shouldShowOdooDetails && fmtLastVerified ? fmtLastVerified : "—"}
                  </span>
                </div>
              );
            })()}
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            {odooStatus.status === "connected" || odooStatus.status === "error" ? (
              <>
                <GlassButton size="sm" onClick={handleTestOdoo} disabled={isTesting}>
                  <RefreshCw className={`w-3.5 h-3.5 ${isTesting ? "animate-spin" : ""}`} />
                  {isTesting ? "Testing..." : "Test Connection"}
                </GlassButton>
                <GlassButton size="sm" onClick={() => setIsRotateOpen(true)}>
                  <Key className="w-3.5 h-3.5" />
                  Rotate Key
                </GlassButton>
                <GlassButton
                  size="sm"
                  variant="danger"
                  onClick={handleDisconnectOdoo}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Disconnect
                </GlassButton>
              </>
            ) : (
              <GlassButton onClick={() => setIsConnectOpen(true)} className="w-full">
                <Plus className="w-4 h-4" />
                Connect Odoo Account
              </GlassButton>
            )}
          </div>
        </GlassPanel>

        <GlassPanel className="p-6 rounded-2xl flex flex-col justify-center items-center text-center border-dashed">
          <Database className="w-8 h-8 text-soft mb-3" />
          <h4 className="font-bold text-muted mb-1">Microsoft / Microsoft 365</h4>
          <p className="text-xs text-soft max-w-xs">
            SharePoint, Outlook and Microsoft Graph integration coming in a future iteration.
          </p>
        </GlassPanel>
      </div>

      {isConnectOpen && (
        <div className="fixed inset-0 bg-canvas/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="bg-surface border border-default rounded-2xl max-w-lg w-full overflow-hidden shadow-2xl">
            <div className="p-6 border-b border-default flex justify-between items-center">
              <h3 className="font-bold text-lg text-default">Connect Odoo Enterprise</h3>
              <button onClick={() => setIsConnectOpen(false)} className="text-muted hover:text-default">
                ✕
              </button>
            </div>
            <form onSubmit={handleConnectOdoo} className="p-6 space-y-4 text-left">
              <div>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  Odoo Instance URL
                </label>
                <GlassInput type="url" required value={odooUrl} onChange={(e) => setOdooUrl(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  Odoo Database Name
                </label>
                <GlassInput type="text" required value={odooDb} onChange={(e) => setOdooDb(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  Odoo Username / Email
                </label>
                <GlassInput type="email" required value={odooUsername} onChange={(e) => setOdooUsername(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  Odoo API Key / Password
                </label>
                <GlassInput
                  type="password"
                  required
                  value={odooApiKey}
                  onChange={(e) => setOdooApiKey(e.target.value)}
                  placeholder="Enter Odoo API Key..."
                />
              </div>

              {testResult && !testResult.success && (
                <div className="p-3 rounded-xl border border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5 text-sm space-y-2">
                  <div className="flex items-start gap-2">
                    <XCircle className="w-4 h-4 text-[var(--color-danger)] shrink-0 mt-0.5" />
                    <div>
                      <p className="font-semibold text-[var(--color-danger)]">Connection Failed</p>
                      {testResult.errorType && (
                        <p className="text-xs text-muted mt-0.5 font-mono">
                          {testResult.stage && <span>Stage: {testResult.stage}</span>}
                          {testResult.stage && testResult.errorType && <span> &middot; </span>}
                          {testResult.errorType && <span>Type: {testResult.errorType}</span>}
                        </p>
                      )}
                      <p className="text-muted mt-0.5">
                        {testResult.isKeyVaultError
                          ? "Could not save Odoo credentials securely. The AI Platform service does not currently have permission to write to Key Vault. Please contact an administrator."
                          : getConnectorFriendlyMessage(testResult.errorType, testResult.message)}
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowTechDetails(!showTechDetails)}
                    className="text-xs text-muted hover:text-default underline underline-offset-2"
                  >
                    {showTechDetails ? "Hide technical details" : "Show technical details"}
                  </button>
                  {showTechDetails && (
                    <pre className="text-xs text-muted bg-surface p-2 rounded-lg overflow-x-auto whitespace-pre-wrap font-mono border border-default">
                      {testResult.technicalDetail || testResult.message || "No technical details available."}
                      {testResult.requestId && `\n\nRequest ID: ${testResult.requestId}`}
                    </pre>
                  )}
                </div>
              )}

              {testResult && testResult.success && (
                <div className="p-3 rounded-xl border border-[var(--color-success)]/25 bg-[var(--color-success)]/5 text-sm flex items-start gap-2">
                  <CheckCircle2 className="w-4 h-4 text-[var(--color-success)] shrink-0 mt-0.5" />
                  <div>
                    <p className="font-semibold text-[var(--color-success)]">Success</p>
                    <p className="text-default mt-0.5">{testResult.message}</p>
                  </div>
                </div>
              )}

              <div className="pt-4 flex gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setIsConnectOpen(false);
                    setTestResult(null);
                    setShowTechDetails(false);
                  }}
                  className="flex-1 py-3 bg-surface border border-default text-muted rounded-xl text-sm font-semibold tracking-wide transition-all hover-text-default"
                >
                  Cancel
                </button>
                <GlassButton type="submit" disabled={isConnecting} className="flex-1">
                  {isConnecting ? "Connecting..." : "Verify & Save"}
                </GlassButton>
              </div>
            </form>
          </div>
        </div>
      )}

      {isRotateOpen && (
        <div className="fixed inset-0 bg-canvas/80 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="bg-surface border border-default rounded-2xl max-w-md w-full overflow-hidden shadow-2xl">
            <div className="p-6 border-b border-default flex justify-between items-center">
              <h3 className="font-bold text-lg text-default">Rotate API Key</h3>
              <button onClick={() => setIsRotateOpen(false)} className="text-muted hover:text-default">
                ✕
              </button>
            </div>
            <form onSubmit={handleRotateOdoo} className="p-6 space-y-4 text-left">
              <div>
                <label className="text-xs text-muted font-bold block mb-1.5 uppercase">
                  New Odoo API Key
                </label>
                <GlassInput
                  type="password"
                  required
                  value={odooApiKey}
                  onChange={(e) => setOdooApiKey(e.target.value)}
                  placeholder="Enter new API Key..."
                />
              </div>
              <div className="pt-4 flex gap-3">
                <button
                  type="button"
                  onClick={() => setIsRotateOpen(false)}
                  className="flex-1 py-3 bg-surface border border-default text-muted rounded-xl text-sm font-semibold tracking-wide transition-all hover-text-default"
                >
                  Cancel
                </button>
                <GlassButton type="submit" disabled={isConnecting} className="flex-1">
                  {isConnecting ? "Updating..." : "Rotate Key"}
                </GlassButton>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
