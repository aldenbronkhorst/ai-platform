import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { ChevronRight, RefreshCw, Search, Trash2, X } from "lucide-react";
import { ConnectorLogo, StatusBadge } from "../components/connections/ConnectorSections";
import {
  formatDateTime,
  panelTitleClass,
  panelToneClass,
  getStatusTone,
  type ConnectorField,
  type ConnectorMeta,
} from "../components/connections/connectionShared";
import { Button } from "../components/ui/Button";
import { TextField } from "../components/ui/TextField";
import { API_BASE_URL, fetchWithTimeout, isAbortError, type AccessTokenGetter } from "../hooks/useApi";

interface ConnectionsPageProps {
  accessToken: string;
  getAccessToken: AccessTokenGetter;
}

interface Notice {
  title: string;
  message: string;
  status: "failed" | "success";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function fieldInputType(field: ConnectorField) {
  return field.secret ? "password" : field.type === "url" || field.type === "email" ? field.type : "text";
}

export function ConnectionsPage({ accessToken, getAccessToken }: ConnectionsPageProps) {
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const headers = useCallback(async () => {
    const token = await getAccessToken({ redirectOnFailure: true });
    if (!token) throw new Error("Microsoft session expired. Please sign in again.");
    return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  }, [getAccessToken]);

  const refresh = useCallback(async () => {
    if (!accessToken) return;
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/connected-accounts`, { headers: await headers() });
      if (!response.ok) throw new Error(`Request failed (${response.status}).`);
      const payload = await response.json() as { connectors?: ConnectorMeta[] };
      setConnectors(payload.connectors || []);
      setLoadError(null);
    } catch (error) {
      setLoadError(isAbortError(error) ? "Connector statuses are taking too long to load." : errorMessage(error));
    }
  }, [accessToken, headers]);

  useEffect(() => { void Promise.resolve().then(refresh); }, [refresh]);

  const selected = connectors?.find(connector => connector.connector_key === selectedId) || null;
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return connectors || [];
    return (connectors || []).filter(connector => [
      connector.display_name,
      connector.subtitle,
      connector.connector_key,
      connector.status,
    ].some(value => String(value || "").toLowerCase().includes(query)));
  }, [connectors, search]);

  const openConnector = (connector: ConnectorMeta) => {
    setSelectedId(connector.connector_key);
    const initial: Record<string, string> = {};
    for (const field of connector.manifest?.connection_fields || []) {
      if (!field.secret) initial[field.name] = displayValue(connector.configuration?.[field.name]).replace(/^-$|^null$/, "");
    }
    setValues(initial);
    setNotice(null);
  };

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API_BASE_URL}/connected-accounts/${selected.connector_key}/connect`, {
        method: "POST",
        headers: await headers(),
        body: JSON.stringify({ values }),
      });
      const payload = await response.json() as { detail?: string | { message?: string } };
      if (!response.ok) {
        const message = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
        throw new Error(message || `Connection failed (${response.status}).`);
      }
      await refresh();
      setSelectedId(null);
      setValues({});
    } catch (error) {
      setNotice({ status: "failed", title: "Connection failed", message: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API_BASE_URL}/connected-accounts/${selected.connector_key}`, {
        method: "DELETE",
        headers: await headers(),
      });
      if (!response.ok) throw new Error(`Disconnect failed (${response.status}).`);
      await refresh();
      setSelectedId(null);
    } catch (error) {
      setNotice({ status: "failed", title: "Disconnect failed", message: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-page mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8 animate-fade-in">
      <div className="settings-page-header">
        <div className="min-w-0">
          <h2 className="settings-title text-xl">Connectors</h2>
          <p className="settings-copy mt-1 max-w-2xl text-sm">Connect external systems for use in chat and Workspace.</p>
        </div>
        {loadError ? <Button size="sm" onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /> Retry</Button> : null}
      </div>

      {loadError ? <div className="settings-inline-alert"><p className="text-sm text-muted">{loadError}</p></div> : null}
      {connectors === null && !loadError ? <div className="settings-empty"><p className="text-sm text-muted">Loading connectors...</p></div> : null}

      {connectors && connectors.length > 0 ? (
        <>
          <div className="settings-toolbar settings-toolbar-search-only">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-soft" />
              <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search connectors..." className="settings-search pl-9" />
            </div>
          </div>
          <div className="settings-list">
            {filtered.map(connector => (
              <button key={connector.connector_key} type="button" className="settings-list-row" onClick={() => openConnector(connector)}>
                <span className="settings-list-icon"><ConnectorLogo connectorKey={connector.connector_key} /></span>
                <span className="min-w-0 flex-1 text-left">
                  <span className="block truncate text-sm font-semibold text-default">{connector.display_name || connector.connector_key}</span>
                  <span className="block truncate text-xs text-muted">{connector.subtitle}</span>
                </span>
                <StatusBadge status={connector.status} fallback="Unknown" />
                <ChevronRight className="h-4 w-4 text-soft" />
              </button>
            ))}
          </div>
        </>
      ) : null}

      {connectors?.length === 0 ? <div className="settings-empty"><p className="text-sm text-muted">No connector packages are registered.</p></div> : null}

      {selected ? (
        <div className="settings-detail">
          <div className="settings-detail-header">
            <div className="flex min-w-0 items-center gap-3">
              <ConnectorLogo connectorKey={selected.connector_key} className="h-6 w-6" />
              <div className="min-w-0">
                <h3 className="truncate text-base font-semibold text-default">{selected.display_name}</h3>
                <p className="truncate text-xs text-muted">{selected.subtitle}</p>
              </div>
            </div>
            <button type="button" className="icon-button" aria-label="Close connector" onClick={() => setSelectedId(null)}><X className="h-4 w-4" /></button>
          </div>

          <div className="settings-detail-body space-y-5">
            {selected.status === "connected" || selected.status === "active" ? (
              <>
                <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-sm">
                  <dt className="text-muted">Status</dt><dd className="text-default"><StatusBadge status={selected.status} fallback="Connected" /></dd>
                  <dt className="text-muted">Last verified</dt><dd className="text-default">{formatDateTime(selected.last_verified_at)}</dd>
                  {Object.entries(selected.configuration || {}).map(([key, value]) => (
                    <div key={key} className="contents"><dt className="text-muted">{key.replaceAll("_", " ")}</dt><dd className="min-w-0 break-words text-default">{displayValue(value)}</dd></div>
                  ))}
                  {Object.entries(selected.metadata || {}).map(([key, value]) => (
                    <div key={key} className="contents"><dt className="text-muted">{key.replaceAll("_", " ")}</dt><dd className="min-w-0 break-words text-default">{displayValue(value)}</dd></div>
                  ))}
                </dl>
                <Button size="sm" variant="danger" disabled={busy} onClick={disconnect}><Trash2 className="h-3.5 w-3.5" /> Disconnect</Button>
              </>
            ) : selected.manifest ? (
              <form onSubmit={connect} className="space-y-4">
                {(selected.manifest.connection_fields || []).map(field => (
                  <label key={field.name} className="block space-y-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wide text-muted">{field.label || field.name}</span>
                    <TextField
                      type={fieldInputType(field)}
                      required={field.required}
                      placeholder={field.placeholder}
                      value={values[field.name] || ""}
                      onChange={event => setValues(current => ({ ...current, [field.name]: event.target.value }))}
                    />
                  </label>
                ))}
                <Button type="submit" disabled={busy}>{busy ? "Connecting..." : "Verify & Save"}</Button>
              </form>
            ) : <p className="text-sm text-muted">{selected.error || "Connector package is unavailable."}</p>}

            {notice ? (
              <div className={`rounded-lg border p-3 text-sm ${panelToneClass(getStatusTone(notice.status, notice.status === "failed"))}`}>
                <p className={`font-semibold ${panelTitleClass(getStatusTone(notice.status, notice.status === "failed"))}`}>{notice.title}</p>
                <p className="mt-1 text-muted">{notice.message}</p>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
