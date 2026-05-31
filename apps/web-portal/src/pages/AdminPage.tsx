import { useState, useEffect } from "react";
import { Shield } from "lucide-react";

interface AIMemory {
  id: string;
  type: string;
  title: string;
  body: string | null;
  summary: string | null;
  scope_type: string | null;
  scope_value: string | null;
  confidence: string;
  risk_level: string;
  status: string;
  priority: number;
  created_at: string;
  updated_at: string;
}

interface AIRule {
  id: string;
  title: string;
  body: string;
  status: string;
  priority: number;
  scope_type: string | null;
  scope_value: string | null;
}

const APIM_BASE_URL = import.meta.env.VITE_APIM_BASE_URL || "https://apim-ai-platform-prod-san-001.azure-api.net";

type Tab = "memories" | "rules";

export function AdminPage({ accessToken }: { accessToken: string }) {
  const [tab, setTab] = useState<Tab>("memories");
  const [memories, setMemories] = useState<AIMemory[]>([]);
  const [rules, setRules] = useState<AIRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterType, setFilterType] = useState("");

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchMemories(),
      fetchRules(),
    ]).finally(() => setLoading(false));
  }, [filterStatus, filterType]);

  const headers = {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };

  const fetchMemories = async () => {
    try {
      const q = new URLSearchParams({ limit: "100" });
      if (filterStatus) q.set("status", filterStatus);
      if (filterType) q.set("type", filterType);
      const res = await fetch(`${APIM_BASE_URL}/memories?${q}`, { headers });
      if (res.ok) setMemories(await res.json());
    } catch {}
  };

  const fetchRules = async () => {
    try {
      const res = await fetch(`${APIM_BASE_URL}/context/rules`, { headers });
      if (res.ok) setRules(await res.json());
    } catch {}
  };

  const doApprove = async (id: string) => {
    try {
      const res = await fetch(`${APIM_BASE_URL}/memories/${id}/approve`, {
        method: "POST",
        headers,
      });
      if (res.ok) {
        setMemories((prev) =>
          prev.map((m) => (m.id === id ? { ...m, status: "active" } : m))
        );
      }
    } catch {}
  };

  const doArchive = async (id: string) => {
    try {
      const res = await fetch(`${APIM_BASE_URL}/memories/${id}`, {
        method: "DELETE",
        headers,
      });
      if (res.ok) {
        setMemories((prev) => prev.filter((m) => m.id !== id));
      }
    } catch {}
  };

  const statusColor: Record<string, string> = {
    active: "text-[var(--color-success)] bg-[var(--color-success)]/10",
    draft: "text-[var(--color-warning)] bg-[var(--color-warning)]/10",
    needs_review: "text-[var(--color-danger)] bg-[var(--color-danger)]/10",
    archived: "text-muted bg-surface",
    rejected: "text-[var(--color-danger)] bg-[var(--color-danger)]/10",
  };

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-6">
        <Shield className="w-5 h-5 text-muted" />
        <h1 className="text-lg font-extrabold">Admin Dashboard</h1>
      </div>

      <div className="flex gap-4 border-b border-default mb-6">
        {(["memories", "rules"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-all ${
              tab === t
                ? "border-[var(--color-accent)] text-default"
                : "border-transparent text-muted hover:text-default"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-xs text-muted">Loading...</p>
      ) : tab === "memories" ? (
        <div>
          <div className="flex gap-2 mb-4">
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="text-xs bg-canvas border border-default rounded-lg px-3 py-1.5 outline-none"
            >
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="needs_review">Needs Review</option>
              <option value="archived">Archived</option>
            </select>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="text-xs bg-canvas border border-default rounded-lg px-3 py-1.5 outline-none"
            >
              <option value="">All types</option>
              <option value="user_preference">User Preference</option>
              <option value="resolved_case">Resolved Case</option>
              <option value="correction">Correction</option>
              <option value="procedure">Procedure</option>
              <option value="customer_note">Customer Note</option>
              <option value="system_behavior">System Behavior</option>
              <option value="general_note">General Note</option>
            </select>
          </div>

          {memories.length === 0 ? (
            <p className="text-xs text-muted">No memories found.</p>
          ) : (
            <div className="space-y-2">
              {memories.map((m) => (
                <div
                  key={m.id}
                  className="bg-surface border border-default rounded-xl px-4 py-3 flex items-start justify-between gap-4"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-muted">
                        {m.type.replace(/_/g, " ")}
                      </span>
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                          statusColor[m.status] || "text-muted bg-surface"
                        }`}
                      >
                        {m.status}
                      </span>
                      <span className="text-[10px] text-muted">
                        {m.risk_level}
                      </span>
                      <span className="text-[10px] text-muted">
                        conf: {m.confidence}
                      </span>
                    </div>
                    <p className="text-xs font-semibold truncate">{m.title}</p>
                    {m.body && (
                      <p className="text-[11px] text-muted mt-0.5 line-clamp-2">
                        {m.body}
                      </p>
                    )}
                    <div className="flex items-center gap-3 mt-1.5 text-[10px] text-muted">
                      {m.scope_type && (
                        <span>
                          scope: {m.scope_type}
                          {m.scope_value ? `=${m.scope_value}` : ""}
                        </span>
                      )}
                      <span>priority: {m.priority}</span>
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    {(m.status === "draft" || m.status === "needs_review") && (
                      <button
                        onClick={() => doApprove(m.id)}
                        className="text-[11px] px-2.5 py-1 bg-[var(--color-success)]/20 text-[var(--color-success)] hover:bg-[var(--color-success)]/30 rounded-lg transition-all"
                      >
                        Approve
                      </button>
                    )}
                    <button
                      onClick={() => doArchive(m.id)}
                      className="text-[11px] px-2.5 py-1 bg-surface border border-default hover-bg-surface rounded-lg transition-all"
                    >
                      Archive
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div>
          <p className="text-xs text-muted mb-4">
            Active business rules are injected into every chat conversation.
          </p>
          {rules.length === 0 ? (
            <p className="text-xs text-muted">No rules loaded.</p>
          ) : (
            <div className="space-y-2">
              {rules.map((r) => (
                <div
                  key={r.id}
                  className="bg-surface border border-default rounded-xl px-4 py-3"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-semibold">{r.title}</span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                        r.status === "active"
                          ? "text-[var(--color-success)] bg-[var(--color-success)]/10"
                          : "text-muted bg-surface"
                      }`}
                    >
                      {r.status}
                    </span>
                    <span className="text-[10px] text-muted">
                      p{r.priority}
                    </span>
                    {r.scope_type && (
                      <span className="text-[10px] text-muted">
                        {r.scope_type}
                        {r.scope_value ? `: ${r.scope_value}` : ""}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted">{r.body}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
