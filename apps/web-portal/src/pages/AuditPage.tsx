import { useState, useEffect } from "react";
import { Shield, Search, Eye, RefreshCw } from "lucide-react";

const APIM_BASE_URL =
  import.meta.env.VITE_APIM_BASE_URL ||
  "https://apim-ai-platform-prod-san-001.azure-api.net";

interface AuditPageProps {
  accessToken: string;
}

export function AuditPage({ accessToken }: AuditPageProps) {
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [inspectLog, setInspectLog] = useState<any | null>(null);

  const fetchLogs = async () => {
    if (!accessToken) return;
    setIsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/audit`, {
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      });
      if (res.ok) setAuditLogs(await res.json());
    } catch (err) {
      console.error("Failed to fetch audit logs:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (accessToken) fetchLogs();
  }, [accessToken]);

  return (
    <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-default">Audit Log Viewer</h2>
          <p className="text-sm text-muted mt-1">
            Comprehensive log of proxy requests, target models, and risk levels.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="w-4 h-4 text-soft absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Filter logs..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="pl-9 pr-4 py-2 bg-surface border border-default rounded-lg text-xs placeholder-soft focus:outline-none focus:border-soft w-48 text-default"
            />
          </div>
          <button
            onClick={fetchLogs}
            disabled={isLoading}
            className="p-2 bg-surface border border-default hover-bg-surface rounded-xl transition-all"
          >
            <RefreshCw className={`w-4 h-4 text-muted ${isLoading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-20 text-muted">Loading logs...</div>
      ) : auditLogs.length === 0 ? (
        <div className="border border-dashed border-default rounded-2xl text-center py-16 text-muted animate-fade-in">
          <Shield className="w-10 h-10 text-soft mb-3 mx-auto" />
          <p className="font-semibold text-default">No audit events generated</p>
          <p className="text-xs text-soft max-w-sm mx-auto mt-1">
            Audit events are captured automatically for Odoo connections and proxy endpoints.
          </p>
        </div>
      ) : (
        <div className="grid lg:grid-cols-3 gap-6 items-start">
          <div className="lg:col-span-2 border border-default rounded-2xl bg-subtle overflow-hidden select-text">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-surface border-b border-default text-muted font-bold uppercase tracking-wider">
                  <th className="p-3">Action</th>
                  <th className="p-3">Model</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Risk</th>
                  <th className="p-3">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-default">
                {auditLogs
                  .filter(
                    (log) =>
                      !filter ||
                      log.action_type.includes(filter) ||
                      (log.target_model && log.target_model.includes(filter))
                  )
                  .map((log) => (
                    <tr
                      key={log.id}
                      onClick={() => setInspectLog(log)}
                      className={`cursor-pointer hover-bg-surface transition-all ${
                        inspectLog?.id === log.id ? "bg-surface" : ""
                      }`}
                    >
                      <td className="p-3 font-semibold text-default uppercase font-mono">
                        {log.action_type}
                      </td>
                      <td className="p-3 font-mono text-default">{log.target_model || "—"}</td>
                      <td className="p-3">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded text-[10px] font-bold ${
                            log.status === "success"
                              ? "bg-[var(--color-success)]/10 text-[var(--color-success)] border border-[var(--color-success)]/20"
                              : "bg-[var(--color-danger)]/10 text-[var(--color-danger)] border border-[var(--color-danger)]/20"
                          }`}
                        >
                          {log.status}
                        </span>
                      </td>
                      <td className="p-3 text-muted font-mono capitalize">{log.risk_level}</td>
                      <td className="p-3 text-muted font-mono">
                        {new Date(log.timestamp).toLocaleTimeString()}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          <div className="border border-default rounded-2xl bg-subtle p-5 space-y-4 select-text">
            <div className="flex justify-between items-center border-b border-default pb-3">
              <h3 className="font-bold text-sm text-default">Event Inspector</h3>
              <span className="text-xs text-muted font-mono">Detail View</span>
            </div>
            {inspectLog ? (
              <div className="space-y-4 text-xs font-medium">
                {[
                  ["Action", inspectLog.action_type],
                  ["Target Model", inspectLog.target_model || "—"],
                  ["Risk Level", inspectLog.risk_level],
                  ["Actor ID", inspectLog.actor_user_id || "System"],
                  ["Identity Mode", inspectLog.identity_mode],
                ].map(([label, value]) => (
                  <div key={label as string} className="grid grid-cols-3 gap-2">
                    <span className="text-muted">{label as string}:</span>
                    <span className="col-span-2 text-default font-mono uppercase">
                      {value as string}
                    </span>
                  </div>
                ))}
                <div className="pt-2 border-t border-default">
                  <span className="text-muted block mb-1">Raw Payload:</span>
                  <pre className="p-3 bg-subtle border border-default rounded-lg overflow-x-auto text-[10px] font-mono text-muted max-h-48 overflow-y-auto">
                    {JSON.stringify(inspectLog, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="text-center py-12 text-muted">
                <Eye className="w-8 h-8 text-soft mx-auto mb-2" />
                Select an event to inspect.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
