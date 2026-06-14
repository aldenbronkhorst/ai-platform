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

interface DiscoveredModel {
  id: string;
  display_name?: string | null;
  context_window?: number | null;
  supports_tools?: boolean | null;
  supports_json_schema?: boolean | null;
}

interface DiscoveryState {
  isLoading: boolean;
  success?: boolean;
  message?: string;
  models: DiscoveredModel[];
}

interface ProviderFormState {
  providerId: string | null;
  name: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
}

interface ModelFormState {
  modelId: string | null;
  modelName: string;
  displayName: string;
  contextWindow: number | "";
  supportsTools: boolean;
  supportsJsonSchema: boolean;
  enabled: boolean;
}

function emptyProviderForm(): ProviderFormState {
  return {
    providerId: null,
    name: "",
    baseUrl: "",
    apiKey: "",
    enabled: true,
  };
}

function emptyModelForm(): ModelFormState {
  return {
    modelId: null,
    modelName: "",
    displayName: "",
    contextWindow: "",
    supportsTools: true,
    supportsJsonSchema: false,
    enabled: true,
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

function boolLabel(value: string) {
  return value === "true" ? "Enabled" : "Disabled";
}

function displayNameForModel(modelId: string, displayName?: string | null) {
  const clean = (displayName || "").trim();
  if (clean) return clean;
  const lastSegment = modelId.split("/").pop();
  return lastSegment || modelId;
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
  const [providerForm, setProviderForm] = useState<ProviderFormState>(() => emptyProviderForm());
  const [modelForms, setModelForms] = useState<Record<string, ModelFormState>>({});
  const [discovery, setDiscovery] = useState<Record<string, DiscoveryState>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [isSavingProvider, setIsSavingProvider] = useState(false);
  const [savingModelKey, setSavingModelKey] = useState<string | null>(null);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [routeSavingKey, setRouteSavingKey] = useState<string | null>(null);
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

  const updateModelForm = (providerId: string, patch: Partial<ModelFormState>) => {
    setModelForms(current => ({
      ...current,
      [providerId]: {
        ...(current[providerId] || emptyModelForm()),
        ...patch,
      },
    }));
  };

  const editProvider = (provider: ModelProvider) => {
    setProviderForm({
      providerId: provider.id,
      name: provider.name,
      baseUrl: provider.base_url,
      apiKey: "",
      enabled: provider.enabled === "true",
    });
    setNotice(null);
    setTestResult(null);
  };

  const editModel = (providerId: string, model: ProviderModel) => {
    updateModelForm(providerId, {
      modelId: model.id,
      modelName: model.model_name,
      displayName: model.display_name,
      contextWindow: model.context_window || "",
      supportsTools: model.supports_tools === "true",
      supportsJsonSchema: model.supports_json_schema === "true",
      enabled: model.enabled === "true",
    });
    setNotice(null);
    setTestResult(null);
  };

  const saveProvider = async (event: FormEvent) => {
    event.preventDefault();
    setIsSavingProvider(true);
    setNotice(null);
    setTestResult(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          provider_id: providerForm.providerId,
          name: providerForm.name,
          base_url: providerForm.baseUrl,
          api_key: providerForm.apiKey || null,
          enabled: providerForm.enabled,
        }),
      }, 45_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not save provider.");
      }
      applyPayload(payload as ProviderListResponse);
      setProviderForm(current => ({ ...current, apiKey: "" }));
      setNotice({ tone: "success", text: "Provider saved." });
    } catch (err) {
      setNotice({ tone: "error", text: errorText(err) });
    } finally {
      setIsSavingProvider(false);
    }
  };

  const discoverModels = async (provider: ModelProvider) => {
    setDiscovery(current => ({
      ...current,
      [provider.id]: { isLoading: true, models: current[provider.id]?.models || [] },
    }));
    setNotice(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers/discover`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ provider_id: provider.id }),
      }, 60_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not fetch models.");
      }
      setDiscovery(current => ({
        ...current,
        [provider.id]: {
          isLoading: false,
          success: Boolean(payload.success),
          message: payload.message || "",
          models: payload.models || [],
        },
      }));
    } catch (err) {
      setDiscovery(current => ({
        ...current,
        [provider.id]: {
          isLoading: false,
          success: false,
          message: errorText(err),
          models: [],
        },
      }));
    }
  };

  const saveModel = async (
    providerId: string,
    form: ModelFormState,
    successMessage = "Model saved.",
  ) => {
    if (!form.modelName.trim()) {
      setNotice({ tone: "error", text: "Model ID is required." });
      return;
    }

    setSavingModelKey(`${providerId}:${form.modelId || form.modelName}`);
    setNotice(null);
    setTestResult(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers/${providerId}/models`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          model_id: form.modelId,
          model_name: form.modelName,
          display_name: form.displayName || form.modelName,
          enabled: form.enabled,
          supports_tools: form.supportsTools,
          supports_json_schema: form.supportsJsonSchema,
          context_window: form.contextWindow === "" ? null : Number(form.contextWindow),
        }),
      }, 45_000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "Could not save model.");
      }
      applyPayload(payload as ProviderListResponse);
      setModelForms(current => ({ ...current, [providerId]: emptyModelForm() }));
      setNotice({ tone: "success", text: successMessage });
    } catch (err) {
      setNotice({ tone: "error", text: errorText(err) });
    } finally {
      setSavingModelKey(null);
    }
  };

  const enableDiscoveredModel = (provider: ModelProvider, model: DiscoveredModel) => {
    const modelName = model.id;
    void saveModel(provider.id, {
      modelId: null,
      modelName,
      displayName: displayNameForModel(modelName, model.display_name),
      contextWindow: model.context_window || "",
      supportsTools: model.supports_tools ?? true,
      supportsJsonSchema: model.supports_json_schema ?? false,
      enabled: true,
    }, "Model enabled.");
  };

  const testProviderModel = async (provider: ModelProvider, model: ProviderModel) => {
    setTestingKey(model.id);
    setNotice(null);
    setTestResult(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers/test`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          provider_id: provider.id,
          model_id: model.id,
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
          <p className="mt-1 text-xs font-medium text-muted">Providers are API connections. Models are selected underneath each provider.</p>
        </div>
        <div className="flex gap-2">
          <GlassButton size="sm" onClick={() => setProviderForm(emptyProviderForm())}>
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

      {testResult && (
        <div className={`flex items-center gap-2 rounded-xl border border-default bg-surface px-4 py-3 text-xs font-semibold ${
          testResult.success ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"
        }`}>
          {testResult.success ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{testResult.message}</span>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <GlassPanel className="rounded-2xl p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-extrabold text-default">Providers</h3>
            {isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted" />}
          </div>

          <div className="space-y-3">
            {!isLoading && providers.length === 0 && (
              <div className="rounded-xl border border-default bg-surface px-4 py-8 text-center text-xs font-semibold text-muted">
                No providers configured.
              </div>
            )}

            {providers.map(provider => {
              const providerDiscovery = discovery[provider.id];
              const form = modelForms[provider.id] || emptyModelForm();
              const enabledModelIds = new Set(provider.models.map(model => model.model_name));

              return (
                <div key={provider.id} className="rounded-xl border border-default bg-surface p-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <button type="button" onClick={() => editProvider(provider)} className="min-w-0 flex-1 text-left">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-extrabold text-default">{provider.name}</span>
                        <span className="rounded-full bg-raised px-2 py-0.5 text-[10px] font-bold text-muted">{boolLabel(provider.enabled)}</span>
                      </div>
                      <p className="mt-1 truncate text-xs font-semibold text-muted">{provider.base_url}</p>
                      <p className={`mt-1 flex items-center gap-1 text-[11px] font-bold ${statusTone(provider.api_key_status)}`}>
                        <KeyRound className="h-3 w-3" />
                        {statusLabel(provider.api_key_status)}
                      </p>
                    </button>
                    <div className="flex flex-wrap gap-2">
                      <GlassButton size="sm" onClick={() => void discoverModels(provider)} disabled={providerDiscovery?.isLoading}>
                        <RefreshCw className={`h-3.5 w-3.5 ${providerDiscovery?.isLoading ? "animate-spin" : ""}`} />
                        Fetch models
                      </GlassButton>
                      <GlassButton size="sm" onClick={() => editProvider(provider)}>
                        <SlidersHorizontal className="h-3.5 w-3.5" />
                        Edit
                      </GlassButton>
                    </div>
                  </div>

                  {providerDiscovery?.message && (
                    <div className={`mt-3 flex items-center gap-2 rounded-lg border border-default bg-base px-3 py-2 text-[11px] font-semibold ${
                      providerDiscovery.success ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"
                    }`}>
                      {providerDiscovery.success ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
                      <span>{providerDiscovery.message}</span>
                    </div>
                  )}

                  {providerDiscovery?.models.length ? (
                    <div className="mt-3 border-t border-default pt-3">
                      <h4 className="mb-2 text-[11px] font-extrabold uppercase tracking-wide text-muted">Available models</h4>
                      <div className="max-h-72 space-y-2 overflow-auto pr-1">
                        {providerDiscovery.models.map(model => {
                          const isEnabled = enabledModelIds.has(model.id);
                          return (
                            <div key={model.id} className="flex flex-col gap-2 rounded-lg border border-default bg-base px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0">
                                <p className="truncate text-xs font-extrabold text-default">{displayNameForModel(model.id, model.display_name)}</p>
                                <p className="truncate text-[11px] font-semibold text-muted">{model.id}</p>
                              </div>
                              <GlassButton size="sm" onClick={() => enableDiscoveredModel(provider, model)} disabled={isEnabled || !!savingModelKey}>
                                {savingModelKey === `${provider.id}:${model.id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                                {isEnabled ? "Enabled" : "Enable"}
                              </GlassButton>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-3 border-t border-default pt-3">
                    <h4 className="mb-2 text-[11px] font-extrabold uppercase tracking-wide text-muted">Enabled models</h4>
                    {provider.models.length === 0 ? (
                      <div className="rounded-lg border border-default bg-base px-3 py-4 text-center text-xs font-semibold text-muted">
                        No models enabled.
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {provider.models.map(model => {
                          const isPrimary = route?.primary_model_id === model.id;
                          const isFallback = route?.fallback_model_id === model.id;
                          return (
                            <div key={model.id} className="rounded-lg border border-default bg-base px-3 py-2">
                              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                <button type="button" onClick={() => editModel(provider.id, model)} className="min-w-0 flex-1 text-left">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="truncate text-xs font-extrabold text-default">{model.display_name}</span>
                                    {isPrimary && <span className="rounded-full bg-[var(--color-accent-soft)] px-2 py-0.5 text-[10px] font-bold text-[var(--color-success)]">Primary</span>}
                                    {isFallback && <span className="rounded-full bg-raised px-2 py-0.5 text-[10px] font-bold text-muted">Fallback</span>}
                                    {model.enabled !== "true" && <span className="rounded-full bg-raised px-2 py-0.5 text-[10px] font-bold text-muted">Disabled</span>}
                                  </div>
                                  <p className="mt-1 truncate text-[11px] font-semibold text-muted">{model.model_name}</p>
                                </button>
                                <div className="flex flex-wrap gap-2">
                                  <GlassButton size="sm" onClick={() => void testProviderModel(provider, model)} disabled={testingKey === model.id}>
                                    {testingKey === model.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
                                    Test
                                  </GlassButton>
                                  <GlassButton size="sm" onClick={() => void setRouteModel(model.id, "primary")} disabled={isPrimary || !!routeSavingKey || model.enabled !== "true"}>
                                    <Star className="h-3.5 w-3.5" />
                                    Primary
                                  </GlassButton>
                                  <GlassButton size="sm" onClick={() => void setRouteModel(model.id, "fallback")} disabled={isFallback || isPrimary || !!routeSavingKey || model.enabled !== "true"}>
                                    <ShieldCheck className="h-3.5 w-3.5" />
                                    Fallback
                                  </GlassButton>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveModel(provider.id, form);
                    }}
                    className="mt-3 border-t border-default pt-3"
                  >
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <h4 className="text-[11px] font-extrabold uppercase tracking-wide text-muted">
                        {form.modelId ? "Edit model" : "Manual model"}
                      </h4>
                      <label className="flex items-center gap-2 text-[11px] font-bold text-muted">
                        <input
                          type="checkbox"
                          checked={form.enabled}
                          onChange={(event) => updateModelForm(provider.id, { enabled: event.target.checked })}
                          className="h-4 w-4 accent-[var(--color-accent)]"
                        />
                        Enabled
                      </label>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <label className="block space-y-1.5">
                        <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Model ID</span>
                        <input
                          value={form.modelName}
                          onChange={(event) => updateModelForm(provider.id, {
                            modelName: event.target.value,
                            displayName: form.displayName || event.target.value,
                          })}
                          className="w-full rounded-xl border border-default bg-transparent px-3 py-2 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                          placeholder="provider-model-id"
                        />
                      </label>
                      <label className="block space-y-1.5">
                        <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Display name</span>
                        <input
                          value={form.displayName}
                          onChange={(event) => updateModelForm(provider.id, { displayName: event.target.value })}
                          className="w-full rounded-xl border border-default bg-transparent px-3 py-2 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                        />
                      </label>
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
                      <label className="block space-y-1.5">
                        <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Context size</span>
                        <input
                          type="number"
                          min="1"
                          value={form.contextWindow}
                          onChange={(event) => updateModelForm(provider.id, {
                            contextWindow: event.target.value ? Number(event.target.value) : "",
                          })}
                          className="w-full rounded-xl border border-default bg-transparent px-3 py-2 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                        />
                      </label>
                      <div className="flex flex-wrap gap-3 pb-2">
                        <label className="flex items-center gap-2 text-[11px] font-bold text-muted">
                          <input
                            type="checkbox"
                            checked={form.supportsTools}
                            onChange={(event) => updateModelForm(provider.id, { supportsTools: event.target.checked })}
                            className="h-4 w-4 accent-[var(--color-accent)]"
                          />
                          Tools
                        </label>
                        <label className="flex items-center gap-2 text-[11px] font-bold text-muted">
                          <input
                            type="checkbox"
                            checked={form.supportsJsonSchema}
                            onChange={(event) => updateModelForm(provider.id, { supportsJsonSchema: event.target.checked })}
                            className="h-4 w-4 accent-[var(--color-accent)]"
                          />
                          JSON
                        </label>
                      </div>
                    </div>
                    <div className="mt-3 flex justify-end gap-2">
                      {form.modelId && (
                        <GlassButton type="button" size="sm" onClick={() => updateModelForm(provider.id, emptyModelForm())}>
                          Cancel
                        </GlassButton>
                      )}
                      <GlassButton type="submit" size="sm" variant="primary" disabled={!!savingModelKey}>
                        {savingModelKey?.startsWith(`${provider.id}:`) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                        Save model
                      </GlassButton>
                    </div>
                  </form>
                </div>
              );
            })}
          </div>
        </GlassPanel>

        <GlassPanel className="rounded-2xl p-4">
          <form onSubmit={saveProvider} className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-extrabold text-default">{providerForm.providerId ? "Edit Provider" : "Add Provider"}</h3>
              <label className="flex items-center gap-2 text-xs font-bold text-muted">
                <input
                  type="checkbox"
                  checked={providerForm.enabled}
                  onChange={(event) => setProviderForm(current => ({ ...current, enabled: event.target.checked }))}
                  className="h-4 w-4 accent-[var(--color-accent)]"
                />
                Enabled
              </label>
            </div>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">Provider name</span>
              <input
                value={providerForm.name}
                onChange={(event) => setProviderForm(current => ({ ...current, name: event.target.value }))}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                placeholder="Provider"
                required
              />
            </label>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">API endpoint</span>
              <input
                value={providerForm.baseUrl}
                onChange={(event) => setProviderForm(current => ({ ...current, baseUrl: event.target.value }))}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                placeholder="https://provider.example/v1"
                required
              />
            </label>

            <label className="block space-y-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted">API key</span>
              <input
                type="password"
                value={providerForm.apiKey}
                onChange={(event) => setProviderForm(current => ({ ...current, apiKey: event.target.value }))}
                className="w-full rounded-xl border border-default bg-transparent px-4 py-3 text-xs font-semibold text-default outline-none focus:border-[var(--color-accent)]"
                placeholder={providerForm.providerId ? "Leave blank to keep saved key" : ""}
              />
            </label>

            <div className="flex flex-wrap justify-end gap-2">
              <GlassButton type="button" onClick={() => setProviderForm(emptyProviderForm())}>
                Reset
              </GlassButton>
              <GlassButton type="submit" variant="primary" disabled={isSavingProvider}>
                {isSavingProvider ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Save provider
              </GlassButton>
            </div>
          </form>

          <div className="mt-5 border-t border-default pt-4">
            <h3 className="text-sm font-extrabold text-default">Chat route</h3>
            <div className="mt-3 space-y-2">
              {rows.length === 0 ? (
                <div className="rounded-xl border border-default bg-surface px-4 py-4 text-xs font-semibold text-muted">
                  No enabled models.
                </div>
              ) : (
                rows.map(({ provider, model }) => {
                  const isPrimary = route?.primary_model_id === model.id;
                  const isFallback = route?.fallback_model_id === model.id;
                  return (
                    <div key={model.id} className="rounded-xl border border-default bg-surface px-3 py-2">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                          <p className="truncate text-xs font-extrabold text-default">{model.display_name}</p>
                          <p className="truncate text-[11px] font-semibold text-muted">{provider.name}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {isPrimary && <span className="rounded-full bg-[var(--color-accent-soft)] px-2 py-1 text-[10px] font-bold text-[var(--color-success)]">Primary</span>}
                          {isFallback && <span className="rounded-full bg-raised px-2 py-1 text-[10px] font-bold text-muted">Fallback</span>}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
