import { useState, useEffect } from "react";
import { User, RefreshCw, CheckCircle2, XCircle, Play, Database, Activity, Cpu } from "lucide-react";

const APIM_BASE_URL = import.meta.env.VITE_APIM_BASE_URL || "https://apim-ai-platform-prod-san-001.azure-api.net";

interface AiConfigViewProps {
  accessToken: string;
  activeUser: { displayName: string; email: string; roles: string[] };
}

function getHeaders(token: string) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

export function AiConfigView({ accessToken, activeUser }: AiConfigViewProps) {
  const [subTab, setSubTab] = useState<"profile" | "ai-config" | "test" | "usage">("profile");

  const [providers, setProviders] = useState<any[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [routes, setRoutes] = useState<any[]>([]);
  const [usageLogs, setUsageLogs] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const [testPrompt, setTestPrompt] = useState("Say hello and confirm the model route is working.");
  const [testResult, setTestResult] = useState<any>(null);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (subTab === "ai-config") fetchSummary();
    if (subTab === "usage") fetchUsage();
  }, [subTab]);

  const fetchSummary = async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/ai-config/summary`, { headers: getHeaders(accessToken) });
      if (res.ok) {
        const data = await res.json();
        setProviders(data.providers || []);
        setModels(data.models || []);
        setRoutes(data.routes || []);
      }
    } catch { /* ignore */ }
    setIsLoading(false);
  };

  const fetchUsage = async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/ai-config/usage?limit=20`, { headers: getHeaders(accessToken) });
      if (res.ok) setUsageLogs(await res.json());
    } catch { /* ignore */ }
    setIsLoading(false);
  };

  const handleTest = async () => {
    setIsTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${APIM_BASE_URL}/ai-config/test`, {
        method: "POST",
        headers: getHeaders(accessToken),
        body: JSON.stringify({ task_type: "general_chat", prompt: testPrompt }),
      });
      setTestResult(await res.json());
    } catch { setTestResult({ success: false, error: "Network error" }); }
    setIsTesting(false);
  };

  const tabs = [
    { key: "profile", label: "Profile" },
    { key: "ai-config", label: "AI Configuration" },
    { key: "test", label: "Test Console" },
    { key: "usage", label: "Usage" },
  ];

  return (
    <div className="max-w-5xl mx-auto space-y-6 animate-fade-in select-none">
      <div className="flex items-center gap-2 border-b border-default pb-3">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setSubTab(tab.key as any)}
            className={`px-4 py-2 text-xs font-bold rounded-lg transition-all cursor-pointer ${
              subTab === tab.key ? "bg-surface border border-default text-default" : "text-muted hover:text-default"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {subTab === "profile" && (
        <div className="space-y-6">
          <div className="p-6 border border-default rounded-2xl bg-subtle space-y-4">
            <h3 className="font-bold text-lg text-default">Active Profile</h3>
            <div className="grid grid-cols-4 gap-4 items-center p-4 border border-default rounded-xl bg-surface text-sm">
              <div className="w-12 h-12 rounded-lg bg-white/5 border border-default flex items-center justify-center">
                <User className="w-6 h-6 text-muted" />
              </div>
              <div className="col-span-3">
                <p className="font-semibold text-default">{activeUser.displayName}</p>
                <p className="text-xs text-muted font-mono mt-0.5">Email: {activeUser.email}</p>
                <p className="text-xs text-muted font-mono">Roles: {activeUser.roles.join(", ")}</p>
              </div>
            </div>
          </div>
          <div className="p-6 border border-default rounded-2xl bg-subtle space-y-4">
            <h3 className="font-bold text-lg text-default">Platform</h3>
            <div className="space-y-4 text-sm font-medium">
              {[
                ["Database", "PostgreSQL 16 (Azure Flexible Server)"],
                ["Secrets", "Azure Key Vault (RBAC-Gated)"],
                ["AI Provider", "Microsoft Foundry"],
                ["Model", "Kimi K2.6 (Moonshot AI)"],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between p-3 border-b border-default">
                  <span className="text-muted">{label}</span>
                  <span className="text-default font-mono text-xs">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {subTab === "ai-config" && (
        <div className="space-y-6">
          {isLoading ? (
            <div className="text-center py-12 text-muted">Loading configuration...</div>
          ) : (
            <>
              <div className="p-6 border border-default rounded-2xl bg-subtle space-y-3">
                <h3 className="font-bold text-sm text-default flex items-center gap-2"><Database className="w-4 h-4" /> Providers</h3>
                {providers.length === 0 ? (
                  <p className="text-xs text-muted">No providers configured.</p>
                ) : (
                  <div className="space-y-2">
                    {providers.map((p) => (
                      <div key={p.id} className="flex justify-between items-center p-3 bg-surface border border-default rounded-xl text-xs">
                        <div>
                          <span className="font-semibold text-default">{p.name}</span>
                          <span className="ml-2 text-muted">{p.provider_type}</span>
                        </div>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${p.enabled === "true" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-gray-500/10 text-gray-400"}`}>
                          {p.enabled === "true" ? "Enabled" : "Disabled"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="p-6 border border-default rounded-2xl bg-subtle space-y-3">
                <h3 className="font-bold text-sm text-default flex items-center gap-2"><Cpu className="w-4 h-4" /> Models</h3>
                {models.length === 0 ? (
                  <p className="text-xs text-muted">No models configured.</p>
                ) : (
                  <div className="space-y-2">
                    {models.map((m) => (
                      <div key={m.id} className="p-3 bg-surface border border-default rounded-xl text-xs space-y-1.5">
                        <div className="flex justify-between items-center">
                          <span className="font-semibold text-default">{m.display_name}</span>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${m.enabled === "true" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-gray-500/10 text-gray-400"}`}>
                            {m.enabled === "true" ? "Enabled" : "Disabled"}
                          </span>
                        </div>
                        <div className="text-muted">Deployment: {m.deployment_name}</div>
                        {m.model_family && <div className="text-muted">Family: {m.model_family} | Context: {m.context_window ?? "unknown"}</div>}
                        {m.supports_tools === "true" && <div className="text-muted">Tool calling: Yes</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="p-6 border border-default rounded-2xl bg-subtle space-y-3">
                <h3 className="font-bold text-sm text-default flex items-center gap-2"><Activity className="w-4 h-4" /> Routes</h3>
                {routes.length === 0 ? (
                  <p className="text-xs text-muted">No routes configured.</p>
                ) : (
                  <div className="space-y-2">
                    {routes.map((r) => (
                      <div key={r.id} className="flex justify-between items-center p-3 bg-surface border border-default rounded-xl text-xs">
                        <div>
                          <span className="font-semibold text-default">{r.task_type}</span>
                          <span className="ml-2 text-muted">temp: {r.temperature} | max: {r.max_tokens}</span>
                        </div>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${r.enabled === "true" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-gray-500/10 text-gray-400"}`}>
                          {r.enabled === "true" ? "Enabled" : "Disabled"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {subTab === "test" && (
        <div className="p-6 border border-default rounded-2xl bg-subtle space-y-4">
          <h3 className="font-bold text-lg text-default flex items-center gap-2"><Play className="w-4 h-4" /> Test Console</h3>
          <p className="text-xs text-muted">Test the <strong className="text-default">general_chat</strong> route to verify the model is responding correctly.</p>
          
          <div className="space-y-3">
            <label className="text-xs text-muted font-bold block">Prompt</label>
            <textarea
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              className="w-full px-4 py-3 bg-surface border border-default rounded-xl focus:outline-none focus:border-soft text-sm text-default placeholder-soft resize-none"
              rows={3}
            />
          </div>

          <button
            onClick={handleTest}
            disabled={isTesting}
            className="px-6 py-2.5 bg-surface hover-bg-subtle border border-default text-default rounded-xl text-xs font-bold transition-all flex items-center gap-2 cursor-pointer"
          >
            {isTesting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {isTesting ? "Running test..." : "Run Test"}
          </button>

          {testResult && (
            <div className={`p-4 border rounded-xl text-sm space-y-2 ${testResult.success ? "border-emerald-500/25 bg-emerald-500/5" : "border-rose-500/25 bg-rose-500/5"}`}>
              <div className="flex items-center gap-2 font-bold text-default">
                {testResult.success ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <XCircle className="w-4 h-4 text-rose-400" />}
                {testResult.success ? "Response received" : "Test failed"}
              </div>
              {testResult.response && (
                <div className="p-3 bg-surface border border-default rounded-xl text-xs text-default select-text whitespace-pre-wrap max-h-48 overflow-y-auto">
                  {testResult.response}
                </div>
              )}
              {testResult.error && <p className="text-xs text-rose-400">{testResult.error}</p>}
              <div className="flex gap-4 text-[10px] text-muted font-mono pt-2 border-t border-default">
                <span>Provider: {testResult.model_provider || "-"}</span>
                <span>Model: {testResult.model_name || "-"}</span>
                <span>Latency: {testResult.latency_ms ? `${testResult.latency_ms}ms` : "-"}</span>
                <span>Tokens: {testResult.total_tokens ? `${testResult.prompt_tokens}↑ ${testResult.completion_tokens}↓` : "-"}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {subTab === "usage" && (
        <div className="p-6 border border-default rounded-2xl bg-subtle space-y-4">
          <h3 className="font-bold text-lg text-default flex items-center gap-2"><Activity className="w-4 h-4" /> Usage & Cost Log</h3>
          {isLoading ? (
            <div className="text-center py-12 text-muted">Loading usage...</div>
          ) : usageLogs.length === 0 ? (
            <p className="text-xs text-muted">No usage logs yet. Send a chat message or run a test.</p>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {usageLogs.map((log) => (
                <div key={log.id} className="p-3 bg-surface border border-default rounded-xl text-[10px] font-mono">
                  <div className="flex justify-between text-muted mb-1">
                    <span>{new Date(log.timestamp).toLocaleString()}</span>
                    <span className={`px-1.5 py-0.5 rounded font-bold ${log.status === "success" ? "text-emerald-400" : "text-rose-400"}`}>
                      {log.status}
                    </span>
                  </div>
                  <div className="flex gap-3 text-muted">
                    <span>{log.task_type || "-"}</span>
                    <span>{log.prompt_tokens}↑ {log.completion_tokens}↓</span>
                    <span>{log.total_tokens} total</span>
                    {log.latency_ms && <span>{log.latency_ms}ms</span>}
                    {log.cost_estimate && <span>${log.cost_estimate}</span>}
                  </div>
                  {log.error_message && <div className="text-rose-400 mt-1 truncate">{log.error_message}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
