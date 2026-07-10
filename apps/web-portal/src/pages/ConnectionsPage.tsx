import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ChevronRight, RefreshCw, Search, X } from "lucide-react";
import { Button } from "../components/ui/Button";
import { API_BASE_URL, fetchWithTimeout, isAbortError, type AccessTokenGetter } from "../hooks/useApi";
import {
  ConnectorLogo,
  OdooConnectorSection,
  StatusBadge,
} from "../components/connections/ConnectorSections";
import {
  formatStatusLabel,
  getStatusTone,
  panelTitleClass,
  panelToneClass,
  type ConnectorDef,
  type ConnectorMeta,
  type OdooStatus,
} from "../components/connections/connectionShared";

const KV_ERROR_PHRASES = [
  "forbiddenbyrbac", "setsecret/action", "key vault secrets officer",
  "rbac", "authorization failed", "authorizationfailed",
];

interface ConnectorNotice {
  status?: string;
  connector?: string;
  title?: string;
  message?: string;
  request_id?: string;
}

type ApiRecord = Record<string, unknown>;

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function connectorDefinitions(meta: Record<string, ConnectorMeta> | null): ConnectorDef[] {
  const odoo = meta?.odoo;
  if (!odoo) return [];
  return [{
    key: "odoo",
    name: odoo.display_name || "Odoo",
    subtitle: odoo.subtitle || odoo.auth_method || "ERP connector",
  }];
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
  const [connectorMeta, setConnectorMeta] = useState<Record<string, ConnectorMeta> | null>(null);
  const [connectorStatusError, setConnectorStatusError] = useState<string | null>(null);
  const [connectorSearch, setConnectorSearch] = useState("");

  const headers = useCallback(async () => {
    const token = await getAccessToken({ redirectOnFailure: true });
    if (!token) throw new Error("Microsoft session expired. Please sign in again.");
    return {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  }, [getAccessToken]);

  const fetchConnectors = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/connected-accounts`, { headers: await headers() });
      if (!res.ok) {
        setConnectorStatusError(`Could not load connector statuses (${res.status}).`);
        return;
      }

      const data = await res.json() as { connectors?: ConnectorMeta[] } | ConnectorMeta[];
      const connectors = Array.isArray(data) ? data : data.connectors || [];
      const meta: Record<string, ConnectorMeta> = {};
      connectors.forEach((connector) => {
        if (connector.connector_key) meta[connector.connector_key] = connector;
      });
      setConnectorMeta(meta);
      setConnectorStatusError(null);
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
      if (!res.ok) return;

      const data = await res.json() as OdooStatus;
      setOdooStatus(data);
      if (data.status === "connected" || data.status === "error") {
        if (data.odoo_url) setOdooUrl(data.odoo_url);
        if (data.odoo_db) setOdooDb(data.odoo_db);
        if (data.provider_username) setOdooUsername(data.provider_username);
      }
    } catch {
      // Leave the form empty until the backend status can be read.
    }
  }, [accessToken, headers]);

  useEffect(() => {
    if (!accessToken) return;
    void Promise.resolve().then(() => Promise.all([fetchOdooStatus(), fetchConnectors()]));
  }, [accessToken, fetchOdooStatus, fetchConnectors]);

  const isKeyVaultError = (msg: string) =>
    KV_ERROR_PHRASES.some((phrase) => msg.toLowerCase().includes(phrase));

  const handleConnectOdoo = async (event: FormEvent) => {
    event.preventDefault();
    if (!accessToken) return;

    setIsConnecting(true);
    setConnectorNotice(null);
    try {
      const res = await fetch(`${API_BASE_URL}/connected-accounts/odoo/connect`, {
        method: "POST",
        headers: await headers(),
        body: JSON.stringify({
          odoo_url: odooUrl,
          odoo_db: odooDb,
          odoo_username: odooUsername,
          odoo_api_key: odooApiKey,
        }),
      });
      const data = await res.json() as ApiRecord;
      if (res.ok) {
        setSelectedConnector(null);
        setOdooApiKey("");
        await Promise.all([fetchOdooStatus(), fetchConnectors()]);
        return;
      }

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
      await Promise.all([fetchOdooStatus(), fetchConnectors()]);
    } catch (err) {
      setConnectorNotice({
        status: "failed",
        connector: "odoo",
        title: "Connection failed",
        message: `Could not reach backend: ${errorMessage(err)}`,
      });
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnectOdoo = async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(`${API_BASE_URL}/connected-accounts/odoo/disconnect`, {
        method: "POST",
        headers: await headers(),
      });
      if (res.ok) {
        setOdooUrl("");
        setOdooDb("");
        setOdooUsername("");
        setOdooApiKey("");
        setConnectorNotice(null);
      }
      await Promise.all([fetchOdooStatus(), fetchConnectors()]);
    } catch {
      // Ignore transient disconnect errors; the next status refresh will show the current state.
    }
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
      ].filter(Boolean).join(" ").toLowerCase();
      return searchable.includes(normalizedConnectorSearch);
    })
    : availableConnectors;
  const activeConnector = selectedConnector === "odoo" && availableConnectors.some((connector) => connector.key === "odoo")
    ? "odoo"
    : null;

  return (
    <div className="settings-page mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8 animate-fade-in">
      <div className="settings-page-header">
        <div className="min-w-0">
          <h2 className="settings-title text-xl">Connectors</h2>
          <p className="settings-copy mt-1 max-w-2xl text-sm">
            Connect external systems and expose account-specific context to the AI platform.
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

      {availableConnectors.length > 0 ? (
        <>
          <div className="settings-toolbar settings-toolbar-search-only">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-soft" />
              <input
                value={connectorSearch}
                onChange={(event) => setConnectorSearch(event.target.value)}
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

              {filteredConnectors.map((connector) => {
                const status = connectorMeta?.[connector.key]?.status;
                return (
                  <button
                    key={connector.key}
                    onClick={() => { setSelectedConnector(connector.key); setConnectorNotice(null); }}
                    className="settings-list-row connector-grid group w-full text-left"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-default bg-transparent">
                        <ConnectorLogo connectorKey={connector.key} />
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-extrabold text-default">{connector.name}</span>
                        <span className="mt-1 block truncate text-xs font-semibold text-muted">{connector.subtitle}</span>
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

      {activeConnector ? (
        <div className="fixed inset-0 z-50 flex justify-end bg-[color-mix(in_srgb,var(--ui-chat-surface-background)_84%,transparent)] animate-fade-in">
          <div className="w-full max-w-lg bg-raised border-l border-default overflow-y-auto">
            <div className="p-4 sm:p-6 border-b border-default flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-transparent border border-default shrink-0">
                  <ConnectorLogo connectorKey="odoo" />
                </div>
                <h2 className="font-bold text-lg text-default truncate">Odoo</h2>
              </div>
              <button
                onClick={() => { setSelectedConnector(null); setConnectorNotice(null); }}
                className="p-2 rounded-lg hover-bg-subtle text-muted hover-text-default"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 sm:p-6">
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

              {connectorNotice ? (
                <div className={`mt-4 p-3 rounded-lg text-sm border ${panelToneClass(getStatusTone(connectorNotice.status, connectorNotice.status === "failed"))}`}>
                  <p className={`font-semibold ${panelTitleClass(getStatusTone(connectorNotice.status, connectorNotice.status === "failed"))}`}>
                    {connectorNotice.title || formatStatusLabel(connectorNotice.connector || "connector")}
                  </p>
                  {connectorNotice.message ? <p className="text-muted mt-1">{connectorNotice.message}</p> : null}
                  {connectorNotice.request_id ? <p className="mt-2 text-xs text-muted">Support reference: {connectorNotice.request_id}</p> : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
