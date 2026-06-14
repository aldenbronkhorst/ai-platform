import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  AlertCircle,
  CheckCircle2,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Star,
  TestTube2,
} from "lucide-react";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassPanel } from "../components/ui/GlassPanel";
import { API_BASE_URL, fetchWithTimeout } from "../hooks/useApi";

interface ProviderModel {
  id: string;
  display_name: string;
  model_name: string;
  deployment_name: string;
  supports_tools: string;
  supports_json_schema: string;
  context_window: number | null;
  enabled: string;
}

interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  enabled: string;
  api_key_status: "saved" | "missing" | "vault_not_configured" | "error";
  secret_reference?: string | null;
  models: ProviderModel[];
}

interface ProviderRoute {
  task_type: string;
  primary_model_id: string | null;
  fallback_model_id: string | null;
}

interface ProviderListResponse {
  providers: ModelProvider[];
  route: ProviderRoute | null;
}

interface TestResponse {
  success: boolean;
  message: string;
  provider?: string | null;
  model?: string | null;
}

interface ProviderPreset {
  key: string;
  label: string;
  name: string;
  baseUrl: string;
  modelName: string;
  displayName: string;
  supportsTools: boolean;
  supportsJsonSchema: boolean;
  contextWindow: number | "";
}

interface ProviderFormState {
  providerId: string | null;
  modelId: string | null;
  preset: string;
  name: string;
  baseUrl: string;
  modelName: string;
  displayName: string;
  apiKey: string;
  enabled: boolean;
  supportsTools: boolean;
  supportsJsonSchema: boolean;
  contextWindow: number | "";
}

const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    key: "kimi",
    label: "Kimi K2.6",
    name: "Kimi",
    baseUrl: "https://api.moonshot.ai/v1",
    modelName: "kimi-k2.6",
    displayName: "Kimi K2.6",
    supportsTools: true,
    supportsJsonSchema: false,
    contextWindow: 262144,
  },
  {
    key: "deepseek",
    label: "DeepSeek V4 Flash",
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    modelName: "deepseek-v4-flash",
    displayName: "DeepSeek V4 Flash",
    supportsTools: true,
    supportsJsonSchema: true,
    contextWindow: 1000000,
  },
  {
    key: "custom",
    label: "Custom",
    name: "",
    baseUrl: "",
    modelName: "",
    displayName: "",
    supportsTools: true,
    supportsJsonSchema: false,
    contextWindow: "",
  },
];

function emptyForm(): ProviderFormState {
  const preset = PROVIDER_PRESETS[0];
  return {
    providerId: null,
    modelId: null,
    preset: preset.key,
    name: preset.name,
    baseUrl: preset.baseUrl,
    modelName: preset.modelName,
    displayName: preset.displayName,
    apiKey: "",
    enabled: true,
    supportsTools: preset.supportsTools,
    supportsJsonSchema: preset.supportsJsonSchema,
    contextWindow: preset.contextWindow,
  };
}

function modelRows(providers: ModelProvider[]) {
  return providers.flatMap(provider =>
    provider.models.map(model => ({ provider, model })),
  );
}

function statusTone(status: ModelProvider["api_key_status"]) {
  if (status === "saved") return "text-[var(--color-success)]";
  if (status === "missing") return "text-[var(--color-warning)]";
  return "text-[var(--color-danger)]";
}

function statusLabel(status: ModelProvider["api_key_status"]) {
  if (status === "saved") return "Key saved";
  if (status === "missing") return "Key missing";
  if (status === "vault_not_configured") return "Vault missing";
  return "Key check failed";
}

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

interface AIProvidersPageProps {
  accessToken: string;
}

export function AIProvidersPage({ accessToken }: AIProvidersPageProps) {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [route, setRoute] = useState<ProviderRoute | null>(null);
  const [form, setForm] = useState<ProviderFormState>(() => emptyForm());
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [routeSavingKey, setRouteSavingKey] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [testResult, setTestResult] = useState<TestResponse | null>(null);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

  const rows = useMemo(() => modelRows(providers), [providers]);

  const loadProviders = useCallback(async () => {
    setIsLoading(true);
    setNotice(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        headers: headers(),
      }, 30_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not load AI providers.");
      }
      const data = payload as ProviderListResponse;
      setProviders(data.providers || []);
      setRoute(data.route || null);
    } catch (err) {
      setNotice({ tone: "error", text: errorText(err) });
    } finally {
      setIsLoading(false);
    }
  }, [headers]);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      void loadProviders();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [loadProviders]);

  const applyPayload = (payload: ProviderListResponse) => {
    setProviders(payload.providers || []);
    setRoute(payload.route || null);
  };

  const applyPreset = (presetKey: string) => {
    const preset = PROVIDER_PRESETS.find(item => item.key === presetKey) || PROVIDER_PRESETS[0];
    setShowDetails(preset.key === "custom");
    setForm(current => ({
      ...current,
      preset: preset.key,
      name: preset.name,
      baseUrl: preset.baseUrl,
      modelName: preset.modelName,
      displayName: preset.displayName,
      supportsTools: preset.supportsTools,
      supportsJsonSchema: preset.supportsJsonSchema,
      contextWindow: preset.contextWindow,
    }));
  };

  const editProvider = (provider: ModelProvider, model: ProviderModel) => {
    setForm({
      providerId: provider.id,
      modelId: model.id,
      preset: "custom",
      name: provider.name,
      baseUrl: provider.base_url,
      modelName: model.model_name,
      displayName: model.display_name,
      apiKey: "",
      enabled: provider.enabled === "true" && model.enabled === "true",
      supportsTools: model.supports_tools === "true",
      supportsJsonSchema: model.supports_json_schema === "true",
      contextWindow: model.context_window || "",
    });
    setTestResult(null);
    setNotice(null);
    setShowDetails(false);
  };

  const saveProvider = async (event: FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setNotice(null);
    setTestResult(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          provider_id: form.providerId,
          model_id: form.modelId,
          name: form.name,
          base_url: form.baseUrl,
          model_name: form.modelName,
          display_name: form.displayName,
          api_key: form.apiKey || null,
          enabled: form.enabled,
          supports_tools: form.supportsTools,
          supports_json_schema: form.supportsJsonSchema,
          context_window: form.contextWindow === "" ? null : Number(form.contextWindow),
        }),
      }, 45_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not save provider.");
      }
      applyPayload(payload as ProviderListResponse);
      setForm(current => ({ ...current, apiKey: "" }));
      setNotice({ tone: "success", text: "Provider saved." });
    } catch (err) {
      setNotice({ tone: "error", text: errorText(err) });
    } finally {
      setIsSaving(false);
    }
  };

  const testProvider = async (provider?: ModelProvider, model?: ProviderModel) => {
    const key = model?.id || "form";
    setTestingKey(key);
    setNotice(null);
    setTestResult(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers/test`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(provider && model ? {
          provider_id: provider.id,
          model_id: model.id,
        } : {
          name: form.name,
          base_url: form.baseUrl,
          model_name: form.modelName,
          api_key: form.apiKey || null,
        }),
      }, 120_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Connection test failed.");
      }
      setTestResult(payload as TestResponse);
    } catch (err) {
      setTestResult({ success: false, message: errorText(err) });
    } finally {
      setTestingKey(null);
    }
  };

  const setRouteModel = async (modelId: string, slot: "primary" | "fallback") => {
    const primaryModelId = slot === "primary" ? modelId : route?.primary_model_id;
    const fallbackModelId = slot === "fallback" ? modelId : route?.fallback_model_id;
    if (!primaryModelId) {
      setNotice({ tone: "error", text: "Choose a primary model first." });
      return;
    }
    setRouteSavingKey(`${slot}:${modelId}`);
    setNotice(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers/route`, {
        method: "PATCH",
        headers: headers(),
        body: JSON.stringify({
          primary_model_id: primaryModelId,
          fallback_model_id: fallbackModelId || null,
        }),
      }, 30_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not update model route.");
      }
      applyPayload(payload as ProviderListResponse);
      setNotice({ tone: "success", text: slot === "primary" ? "Primary model updated." : "Fallback model updated." });
    } catch (err) {
      setNotice({ tone: "error", text: errorText(err) });
    } finally {
      setRouteSavingKey(null);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-extrabold text-default">AI Providers</h2>
          <p className="mt-1 text-xs font-medium text-muted">Kimi primary, DeepSeek fallback, or any OpenAI-compatible endpoint.</p>
        </div>
        <div className="flex gap-2">
          <GlassButton size="sm" onClick={() => setForm(emptyForm())}>
            <Plus className="h-3.5 w-3.5" />
            Add
          </GlassButton>
          <GlassButton size="sm" onClick={() => void loadProviders()} disabled={isLoading}>
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
            Refresh
          </GlassButton>
        </div>
      </div>

      {notice && (
        <div className={`flex items-center gap-2 rounded-xl border border-default bg-surface px-4 py-3 text-xs font-semibold ${
          notice.tone === "success" ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"
        }`}>
          {notice.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{notice.text}</span>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <GlassPanel className="rounded-2xl p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-extrabold text-default">Models</h3>
            {isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted" />}
          </div>
          <div className="space-y-2">
            {!isLoading && rows.length === 0 && (
              <div className="rounded-xl border border-default bg-surface px-4 py-8 text-center text-xs font-semibold text-muted">
                No providers configured.
              </div>
            )}
            {rows.map(({ provider, model }) => {
              const isPrimary = route?.primary_model_id === model.id;
              const isFallback = route?.fallback_model_id === model.id;
              return (
                <div key={model.id} className="rounded-xl border border-default bg-surface p-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <button
                      type="button"
                      onClick={() => editProvider(provider, model)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-extrabold text-default">{model.display_name}</span>
                        {isPrimary && <span className="rounded-full bg-[var(--color-accent-soft)] px-2 py-0.5 text-[10px] font-bold text-[var(--color-success)]">Primary</span>}
                        {isFallback && <span className="rounded-full bg-raised px-2 py-0.5 text-[10px] font-bold text-muted">Fallback</span>}
                      </div>
                      <p className="mt-1 truncate text-xs font-semibold text-muted">{provider.name} · {model.model_name}</p>
                      <p className={`mt-1 flex items-center gap-1 text-[11px] font-bold ${statusTone(provider.api_key_status)}`}>
                        <KeyRound className="h-3 w-3" />
                        {statusLabel(provider.api_key_status)}
                      </p>
                    </button>
                    <div className="flex flex-wrap gap-2">
                      <GlassButton size="sm" onClick={() => void testProvider(provider, model)} disabled={testingKey === model.id}>
                        {testingKey === model.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
                        Test
                      </GlassButton>
                      <GlassButton size="sm" onClick={() => void setRouteModel(model.id, "primary")} disabled={isPrimary || !!routeSavingKey}>
                        <Star className="h-3.5 w-3.5" />
                        Primary
                      </GlassButton>
                      <GlassButton size="sm" onClick={() => void setRouteModel(model.id, "fallback")} disabled={isFallback || !!routeSavingKey}>
                        <ShieldCheck className="h-3.5 w-3.5" />
                        Fallback
                      </GlassButton>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </GlassPanel>

        <GlassPanel className="rounded-2xl p-4">
          <form onSubmit={saveProvider} className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-extrabold text-default">{form.providerId ? "Edit Provider" : "Add Provider"}</h3>
              <label className="flex items-center gap-2 text-xs font-bold text-muted">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => setForm(current => ({ ...current, enabled: event.target.checked }))}
                  className="h-4 w-4 accent-[var(--color-accent)]"
                />
                Enabled
              </label>
            </div>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Provider</span>
              <select
                value={form.preset}
                onChange={(event) => applyPreset(event.target.value)}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
              >
                {PROVIDER_PRESETS.map(preset => (
                  <option key={preset.key} value={preset.key}>{preset.label}</option>
                ))}
              </select>
            </label>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Name</span>
              <input
                value={form.name}
                onChange={(event) => setForm(current => ({ ...current, name: event.target.value }))}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                required
              />
            </label>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">API Key</span>
              <input
                type="password"
                value={form.apiKey}
                onChange={(event) => setForm(current => ({ ...current, apiKey: event.target.value }))}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                placeholder={form.providerId ? "Leave blank to keep saved key" : ""}
              />
            </label>

            <button
              type="button"
              onClick={() => setShowDetails(value => !value)}
              className="flex items-center gap-2 rounded-xl border border-default px-3 py-2 text-xs font-bold text-muted hover-text-default hover-bg-surface"
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Details
            </button>

            {(showDetails || form.preset === "custom") && (
              <div className="space-y-3 rounded-xl border border-default bg-surface p-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Model</span>
                    <input
                      value={form.modelName}
                      onChange={(event) => setForm(current => ({ ...current, modelName: event.target.value, displayName: current.displayName || event.target.value }))}
                      className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                      required
                    />
                  </label>
                  <label className="block space-y-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Display Name</span>
                    <input
                      value={form.displayName}
                      onChange={(event) => setForm(current => ({ ...current, displayName: event.target.value }))}
                      className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                      required
                    />
                  </label>
                </div>

                <label className="block space-y-1.5">
                  <span className="text-[11px] font-bold uppercase tracking-wide text-muted">API Endpoint</span>
                  <input
                    value={form.baseUrl}
                    onChange={(event) => setForm(current => ({ ...current, baseUrl: event.target.value }))}
                    className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                    required
                  />
                </label>

                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block space-y-1.5">
                    <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Context Size</span>
                    <input
                      type="number"
                      min="1"
                      value={form.contextWindow}
                      onChange={(event) => setForm(current => ({ ...current, contextWindow: event.target.value ? Number(event.target.value) : "" }))}
                      className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                    />
                  </label>
                  <div className="flex items-end gap-4 pb-3">
                    <label className="flex items-center gap-2 text-xs font-bold text-muted">
                      <input
                        type="checkbox"
                        checked={form.supportsTools}
                        onChange={(event) => setForm(current => ({ ...current, supportsTools: event.target.checked }))}
                        className="h-4 w-4 accent-[var(--color-accent)]"
                      />
                      Connectors
                    </label>
                    <label className="flex items-center gap-2 text-xs font-bold text-muted">
                      <input
                        type="checkbox"
                        checked={form.supportsJsonSchema}
                        onChange={(event) => setForm(current => ({ ...current, supportsJsonSchema: event.target.checked }))}
                        className="h-4 w-4 accent-[var(--color-accent)]"
                      />
                      JSON
                    </label>
                  </div>
                </div>
              </div>
            )}

            {testResult && (
              <div className={`flex items-center gap-2 rounded-xl border border-default bg-surface px-3 py-2 text-xs font-semibold ${
                testResult.success ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"
              }`}>
                {testResult.success ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                <span>{testResult.message}</span>
              </div>
            )}

            <div className="flex flex-wrap justify-end gap-2">
              <GlassButton type="button" onClick={() => void testProvider()} disabled={testingKey === "form" || !form.apiKey}>
                {testingKey === "form" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
                Test
              </GlassButton>
              <GlassButton type="submit" variant="primary" disabled={isSaving}>
                {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Save
              </GlassButton>
            </div>
          </form>
        </GlassPanel>
      </div>
    </div>
  );
}
