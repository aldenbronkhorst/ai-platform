import { useCallback, useEffect, useMemo, useState } from "react";
import type { ButtonHTMLAttributes, FormEvent, ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  SlidersHorizontal,
  TestTube2,
  X,
} from "lucide-react";
import { GlassButton } from "../components/ui/GlassButton";
import { GlassInput } from "../components/ui/GlassInput";
import { GlassPanel } from "../components/ui/GlassPanel";
import { API_BASE_URL, fetchWithTimeout, isAbortError } from "../hooks/useApi";

interface ProviderModel {
  id: string;
  display_name: string;
  model_name: string;
  deployment_name: string;
  supports_tools: string;
  supports_json_schema: string;
  context_window?: number | null;
  enabled: string;
}

interface Provider {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  enabled: string;
  api_key_status: string;
  secret_reference?: string | null;
  models: ProviderModel[];
}

interface Route {
  task_type: string;
  primary_model_id?: string | null;
  fallback_model_id?: string | null;
}

interface SyncInfo {
  success: boolean;
  message: string;
  model_count: number;
}

interface ProviderListResponse {
  providers: Provider[];
  route?: Route | null;
  sync?: SyncInfo | null;
}

interface ProviderTestResponse {
  success: boolean;
  message: string;
  provider?: string | null;
  model?: string | null;
}

interface ProviderFormState {
  providerId: string | null;
  name: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
}

interface Notice {
  tone: "success" | "danger";
  text: string;
}

interface EnabledModelRow {
  provider: Provider;
  model: ProviderModel;
}

interface PickerOption {
  value: string;
  label: string;
}

const emptyProviderForm = (): ProviderFormState => ({
  providerId: null,
  name: "",
  baseUrl: "",
  apiKey: "",
  enabled: true,
});

function boolValue(value: string | undefined) {
  return value === "true";
}

function modelDisplayName(model: ProviderModel) {
  return model.display_name || model.model_name;
}

function modelOptionLabel(row: EnabledModelRow) {
  return `${row.provider.name} - ${modelDisplayName(row.model)}`;
}

function apiKeyStatusLabel(status: string) {
  switch (status) {
    case "saved":
      return "API key saved";
    case "missing":
      return "API key missing";
    case "vault_not_configured":
      return "Key storage unavailable";
    case "error":
      return "Key status unavailable";
    default:
      return status || "Unknown";
  }
}

function apiKeyStatusClass(status: string) {
  switch (status) {
    case "saved":
      return "text-default";
    case "missing":
    case "vault_not_configured":
      return "text-[var(--color-warning)]";
    case "error":
      return "text-[var(--color-danger)]";
    default:
      return "text-muted";
  }
}

function enabledModelCount(provider: Provider) {
  return provider.models.filter(model => boolValue(model.enabled)).length;
}

function errorMessageFromBody(body: unknown) {
  if (typeof body === "object" && body !== null) {
    const record = body as Record<string, unknown>;
    const detail = record.detail;
    const message = record.message;
    if (typeof detail === "string")
      return detail;
    if (typeof message === "string")
      return message;
  }
  return "";
}

async function readApiError(response: Response) {
  const text = await response.text();
  if (!text)
    return `Request failed with HTTP ${response.status}.`;
  try {
    return errorMessageFromBody(JSON.parse(text)) || text;
  } catch {
    return text;
  }
}

function authHeaders(accessToken: string, includeJson = false) {
  const headers: Record<string, string> = {};
  if (includeJson)
    headers["Content-Type"] = "application/json";
  if (accessToken)
    headers.Authorization = `Bearer ${accessToken}`;
  return headers;
}

function FieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] font-bold uppercase tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}

function StatusPill({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "active" | "warning" }) {
  const toneClass = tone === "active"
    ? "border border-default bg-surface text-default"
    : tone === "warning"
      ? "bg-[var(--color-warning)]/10 text-[var(--color-warning)]"
      : "bg-raised text-muted";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-bold ${toneClass}`}>
      {children}
    </span>
  );
}

function IconButton({
  label,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border border-default bg-surface text-muted outline-none transition-colors hover-bg-subtle hover-text-default focus:border-soft disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

function SwitchControl({
  checked,
  label,
  ariaLabel,
  disabled,
  onChange,
}: {
  checked: boolean;
  label?: string;
  ariaLabel?: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={ariaLabel || label || "Toggle"}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="relative h-6 w-11 rounded-full border transition-all disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          backgroundColor: checked ? "#6d6d6d" : "var(--color-surface-raised)",
          borderColor: checked ? "#6d6d6d" : "var(--color-border)",
        }}
      >
        <span
          className="absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-white shadow transition-all"
          style={{ left: checked ? "24px" : "4px" }}
        />
      </button>
      {label ? <span className="text-xs font-bold text-muted">{label}</span> : null}
    </div>
  );
}

function ProviderFormModal({
  form,
  isSaving,
  onChange,
  onClose,
  onSubmit,
}: {
  form: ProviderFormState;
  isSaving: boolean;
  onChange: (patch: Partial<ProviderFormState>) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const isEdit = Boolean(form.providerId);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <GlassPanel className="w-full max-w-xl rounded-2xl p-0">
        <form onSubmit={onSubmit}>
          <div className="flex items-start justify-between gap-4 border-b border-default p-5">
            <div>
              <h3 className="text-base font-extrabold text-default">
                {isEdit ? "Edit provider" : "Add provider"}
              </h3>
              <p className="mt-1 text-xs font-semibold text-muted">
                Save the API connection. Models are loaded automatically after save.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-2 text-muted transition-colors hover-bg-surface hover-text-default"
              aria-label="Close provider form"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="space-y-4 p-5">
            <div className="flex items-center justify-between rounded-xl border border-default bg-surface px-4 py-3">
              <div>
                <p className="text-xs font-extrabold text-default">Provider enabled</p>
                <p className="text-[11px] font-semibold text-muted">Turn this connection on or off.</p>
              </div>
              <SwitchControl
                checked={form.enabled}
                ariaLabel="Toggle provider enabled"
                label={form.enabled ? "On" : "Off"}
                onChange={(enabled) => onChange({ enabled })}
              />
            </div>

            <FieldLabel label="Provider name">
              <GlassInput
                value={form.name}
                onChange={(event) => onChange({ name: event.target.value })}
                placeholder="Provider name"
                required
              />
            </FieldLabel>

            <FieldLabel label="API endpoint">
              <GlassInput
                value={form.baseUrl}
                onChange={(event) => onChange({ baseUrl: event.target.value })}
                placeholder="https://api.provider.com/v1"
                required
              />
            </FieldLabel>

            <FieldLabel label="API key">
              <GlassInput
                type="password"
                value={form.apiKey}
                onChange={(event) => onChange({ apiKey: event.target.value })}
                placeholder={isEdit ? "Leave blank to keep saved key" : "Paste API key"}
              />
            </FieldLabel>
          </div>

          <div className="flex justify-end gap-2 border-t border-default p-5">
            <GlassButton type="button" onClick={onClose}>
              Cancel
            </GlassButton>
            <GlassButton type="submit" variant="primary" disabled={isSaving}>
              {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {isEdit ? "Save changes" : "Save provider"}
            </GlassButton>
          </div>
        </form>
      </GlassPanel>
    </div>
  );
}

function ModelPicker({
  label,
  hint,
  value,
  placeholder,
  options,
  isOpen,
  disabled,
  onOpenChange,
  onChange,
}: {
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  options: PickerOption[];
  isOpen: boolean;
  disabled?: boolean;
  onOpenChange: (open: boolean) => void;
  onChange: (value: string) => void;
}) {
  const selected = options.find(option => option.value === value);
  const displayValue = selected?.label || placeholder;

  return (
    <div className="relative space-y-1.5">
      <p className="text-[11px] font-bold uppercase tracking-wide text-muted">{label}</p>
      <button
        type="button"
        aria-expanded={isOpen}
        disabled={disabled}
        onClick={() => onOpenChange(!isOpen)}
        className="flex h-12 w-full items-center justify-between gap-3 rounded-xl border border-default bg-transparent px-4 text-left text-xs font-semibold text-default outline-none transition-all focus:border-soft disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="min-w-0 truncate">{displayValue}</span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-muted transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen && !disabled ? (
        <div className="absolute left-0 right-0 top-[72px] z-50 overflow-hidden rounded-xl border border-default bg-surface shadow-2xl">
          <div className="max-h-72 overflow-y-auto p-1.5">
            {options.map(option => {
              const isSelected = option.value === value;
              return (
                <button
                  key={option.value || "__empty"}
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    onOpenChange(false);
                  }}
                  className={`flex w-full items-center rounded-lg px-3 py-2.5 text-left text-xs font-semibold transition-colors hover-bg-subtle ${
                    isSelected ? "bg-raised text-default" : "text-muted"
                  }`}
                >
                  <span className="min-w-0 truncate">{option.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      <p className="text-[11px] font-semibold text-muted">{hint}</p>
    </div>
  );
}

export function AIProvidersPage({ accessToken }: { accessToken: string }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [route, setRoute] = useState<Route | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isProviderFormOpen, setIsProviderFormOpen] = useState(false);
  const [providerForm, setProviderForm] = useState<ProviderFormState>(emptyProviderForm);
  const [isSavingProvider, setIsSavingProvider] = useState(false);
  const [savingProviderId, setSavingProviderId] = useState<string | null>(null);
  const [savingModelId, setSavingModelId] = useState<string | null>(null);
  const [isRouteSaving, setIsRouteSaving] = useState(false);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);
  const [openModelPicker, setOpenModelPicker] = useState<"primary" | "fallback" | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [testResult, setTestResult] = useState<ProviderTestResponse | null>(null);

  const enabledRows = useMemo(() => {
    return providers.flatMap(provider => provider.models
      .filter(model => boolValue(provider.enabled) && boolValue(model.enabled))
      .map(model => ({ provider, model })));
  }, [providers]);

  const chatModelOptions = useMemo(() => {
    return enabledRows.map(row => ({
      value: row.model.id,
      label: modelOptionLabel(row),
    }));
  }, [enabledRows]);

  const primaryModelId = route?.primary_model_id || "";
  const fallbackModelId = route?.fallback_model_id || "";
  const primaryModelExists = enabledRows.some(row => row.model.id === primaryModelId);
  const fallbackModelExists = enabledRows.some(row => row.model.id === fallbackModelId);

  const backupModelOptions = useMemo(() => {
    return [
      { value: "", label: "No backup" },
      ...enabledRows
        .filter(row => row.model.id !== primaryModelId)
        .map(row => ({
          value: row.model.id,
          label: modelOptionLabel(row),
        })),
    ];
  }, [enabledRows, primaryModelId]);

  const applyPayload = useCallback((payload: ProviderListResponse) => {
    setProviders(payload.providers);
    setRoute(payload.route || null);
    if (payload.sync) {
      setNotice({
        tone: payload.sync.success ? "success" : "danger",
        text: payload.sync.message,
      });
    }
  }, []);

  const loadProviders = useCallback(async () => {
    setIsLoading(true);
    setNotice(null);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        headers: authHeaders(accessToken),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
    } catch (err) {
      if (!isAbortError(err)) {
        setNotice({
          tone: "danger",
          text: err instanceof Error ? err.message : "Could not load AI providers.",
        });
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, applyPayload]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadProviders();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadProviders]);

  const saveProviderPayload = useCallback(async (form: ProviderFormState) => {
    const body: {
      provider_id?: string;
      name: string;
      base_url: string;
      api_key?: string;
      enabled: boolean;
    } = {
      name: form.name.trim(),
      base_url: form.baseUrl.trim(),
      enabled: form.enabled,
    };
    if (form.providerId)
      body.provider_id = form.providerId;
    if (form.apiKey.trim())
      body.api_key = form.apiKey.trim();

    const response = await fetch(`${API_BASE_URL}/model-providers`, {
      method: "POST",
      headers: authHeaders(accessToken, true),
      body: JSON.stringify(body),
    });
    if (!response.ok)
      throw new Error(await readApiError(response));
    return await response.json() as ProviderListResponse;
  }, [accessToken]);

  const openNewProviderForm = useCallback(() => {
    setProviderForm(emptyProviderForm());
    setIsProviderFormOpen(true);
    setTestResult(null);
  }, []);

  const openEditProviderForm = useCallback((provider: Provider) => {
    setProviderForm({
      providerId: provider.id,
      name: provider.name,
      baseUrl: provider.base_url,
      apiKey: "",
      enabled: boolValue(provider.enabled),
    });
    setIsProviderFormOpen(true);
    setTestResult(null);
  }, []);

  const saveProvider = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    setIsSavingProvider(true);
    setNotice(null);
    try {
      const savingId = providerForm.providerId;
      const savingName = providerForm.name.trim();
      const payload = await saveProviderPayload(providerForm);
      applyPayload(payload);
      const savedProvider = payload.providers.find(provider => provider.id === savingId)
        || payload.providers.find(provider => provider.name === savingName);
      setExpandedProviderId(savedProvider?.id || null);
      setProviderForm(emptyProviderForm());
      setIsProviderFormOpen(false);
      if (!payload.sync) {
        setNotice({
          tone: "success",
          text: "Provider saved.",
        });
      }
    } catch (err) {
      setNotice({
        tone: "danger",
        text: err instanceof Error ? err.message : "Provider could not be saved.",
      });
    } finally {
      setIsSavingProvider(false);
    }
  }, [applyPayload, providerForm, saveProviderPayload]);

  const toggleProvider = useCallback(async (provider: Provider, enabled: boolean) => {
    setSavingProviderId(provider.id);
    setNotice(null);
    try {
      const payload = await saveProviderPayload({
        providerId: provider.id,
        name: provider.name,
        baseUrl: provider.base_url,
        apiKey: "",
        enabled,
      });
      applyPayload(payload);
      if (!payload.sync) {
        setNotice({
          tone: "success",
          text: enabled ? "Provider enabled." : "Provider disabled.",
        });
      }
    } catch (err) {
      setNotice({
        tone: "danger",
        text: err instanceof Error ? err.message : "Provider could not be updated.",
      });
    } finally {
      setSavingProviderId(null);
    }
  }, [applyPayload, saveProviderPayload]);

  const toggleModel = useCallback(async (provider: Provider, model: ProviderModel, enabled: boolean) => {
    setSavingModelId(model.id);
    setNotice(null);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}/models/${model.id}`, {
        method: "PATCH",
        headers: authHeaders(accessToken, true),
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
      setNotice({
        tone: "success",
        text: enabled ? "Model enabled." : "Model disabled.",
      });
    } catch (err) {
      setNotice({
        tone: "danger",
        text: err instanceof Error ? err.message : "Model could not be updated.",
      });
    } finally {
      setSavingModelId(null);
    }
  }, [accessToken, applyPayload]);

  const updateChatRoute = useCallback(async (primaryId: string, fallbackId: string | null) => {
    if (!primaryId)
      return;
    setIsRouteSaving(true);
    setNotice(null);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/route`, {
        method: "PATCH",
        headers: authHeaders(accessToken, true),
        body: JSON.stringify({
          primary_model_id: primaryId,
          fallback_model_id: fallbackId && fallbackId !== primaryId ? fallbackId : null,
        }),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
      setNotice({
        tone: "success",
        text: "Chat model updated.",
      });
    } catch (err) {
      setNotice({
        tone: "danger",
        text: err instanceof Error ? err.message : "Chat model could not be updated.",
      });
    } finally {
      setIsRouteSaving(false);
    }
  }, [accessToken, applyPayload]);

  const testChatModel = useCallback(async () => {
    const selected = enabledRows.find(row => row.model.id === primaryModelId);
    if (!selected) {
      setTestResult({ success: false, message: "Choose a default chat model first." });
      return;
    }

    setTestingKey(selected.model.id);
    setTestResult(null);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/test`, {
        method: "POST",
        headers: authHeaders(accessToken, true),
        body: JSON.stringify({
          provider_id: selected.provider.id,
          model_id: selected.model.id,
        }),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      setTestResult(await response.json() as ProviderTestResponse);
    } catch (err) {
      setTestResult({
        success: false,
        message: err instanceof Error ? err.message : "Test failed.",
      });
    } finally {
      setTestingKey(null);
    }
  }, [accessToken, enabledRows, primaryModelId]);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 pb-8">
      <GlassPanel className="rounded-2xl p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-xl font-bold text-default">AI Providers</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              Connect model providers once. Available models are loaded automatically and can then be used by chat.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <GlassButton size="sm" onClick={openNewProviderForm}>
              <Plus className="h-3.5 w-3.5" />
              Add provider
            </GlassButton>
            <GlassButton size="sm" onClick={() => void loadProviders()} disabled={isLoading}>
              <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
              Refresh
            </GlassButton>
          </div>
        </div>
      </GlassPanel>

      {notice ? (
        <div className={`flex items-center gap-2 rounded-xl border border-default bg-surface px-4 py-3 text-xs font-semibold ${
          notice.tone === "success" ? "text-default" : "text-[var(--color-danger)]"
        }`}>
          {notice.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{notice.text}</span>
        </div>
      ) : null}

      {testResult ? (
        <div className={`flex items-center gap-2 rounded-xl border border-default bg-surface px-4 py-3 text-xs font-semibold ${
          testResult.success ? "text-default" : "text-[var(--color-danger)]"
        }`}>
          {testResult.success ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{testResult.message}</span>
        </div>
      ) : null}

      <GlassPanel className="relative z-30 rounded-2xl p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h3 className="text-sm font-extrabold text-default">Chat defaults</h3>
            <p className="mt-1 text-xs font-semibold text-muted">
              Pick which enabled model chat should use first, plus an optional backup.
            </p>
          </div>
          <GlassButton size="sm" onClick={() => void testChatModel()} disabled={!primaryModelExists || Boolean(testingKey)}>
            {testingKey ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
            Test chat model
          </GlassButton>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <ModelPicker
            label="Default chat model"
            hint="Used first for normal chat."
            value={primaryModelExists ? primaryModelId : ""}
            placeholder="No enabled models"
            options={chatModelOptions}
            isOpen={openModelPicker === "primary"}
            disabled={enabledRows.length === 0 || isRouteSaving}
            onOpenChange={(open) => setOpenModelPicker(open ? "primary" : null)}
            onChange={(nextPrimaryId) => void updateChatRoute(nextPrimaryId, fallbackModelExists ? fallbackModelId : null)}
          />

          <ModelPicker
            label="Backup model"
            hint="Used only if the default model fails."
            value={fallbackModelExists ? fallbackModelId : ""}
            placeholder="No backup"
            options={backupModelOptions}
            isOpen={openModelPicker === "fallback"}
            disabled={!primaryModelExists || enabledRows.length < 2 || isRouteSaving}
            onOpenChange={(open) => setOpenModelPicker(open ? "fallback" : null)}
            onChange={(nextFallbackId) => void updateChatRoute(primaryModelId, nextFallbackId || null)}
          />
        </div>
      </GlassPanel>

      <GlassPanel className="relative z-10 rounded-2xl p-4 sm:p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-extrabold text-default">Providers</h3>
            <p className="mt-1 text-xs font-semibold text-muted">
              Connections are listed here. Open a provider to see its synced models.
            </p>
          </div>
          {isLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted" /> : null}
        </div>

        {!isLoading && providers.length === 0 ? (
          <div className="rounded-xl border border-default bg-surface px-4 py-8 text-center">
            <p className="text-sm font-bold text-default">No providers configured.</p>
            <p className="mt-1 text-xs font-semibold text-muted">Add a provider and its models will appear automatically.</p>
            <GlassButton className="mx-auto mt-4" onClick={openNewProviderForm}>
              <Plus className="h-3.5 w-3.5" />
              Add provider
            </GlassButton>
          </div>
        ) : null}

        <div className="overflow-hidden rounded-xl border border-default bg-canvas">
          {providers.map(provider => {
            const isExpanded = expandedProviderId === provider.id;
            const isProviderEnabled = boolValue(provider.enabled);
            const routeUsesProvider = provider.models.some(model =>
              model.id === route?.primary_model_id || model.id === route?.fallback_model_id);
            const providerEnabledModelCount = enabledModelCount(provider);

            return (
              <div key={provider.id} className="border-b border-default last:border-b-0">
                <div className="ai-provider-list-row flex flex-col gap-3 bg-canvas px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="truncate text-sm font-extrabold text-default">{provider.name}</h4>
                      <StatusPill tone={isProviderEnabled ? "active" : "default"}>
                        {isProviderEnabled ? "On" : "Off"}
                      </StatusPill>
                      {routeUsesProvider ? <StatusPill tone="active">Used by chat</StatusPill> : null}
                      {provider.models.length === 0 ? <StatusPill tone="warning">No models</StatusPill> : null}
                    </div>
                    <p className="mt-1 truncate text-xs font-semibold text-muted">{provider.base_url}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] font-bold">
                      <span className={`inline-flex items-center gap-1 ${apiKeyStatusClass(provider.api_key_status)}`}>
                        <KeyRound className="h-3 w-3" />
                        {apiKeyStatusLabel(provider.api_key_status)}
                      </span>
                      <span className="text-muted">
                        {providerEnabledModelCount} of {provider.models.length} models on
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <SwitchControl
                      checked={isProviderEnabled}
                      ariaLabel={`Toggle ${provider.name}`}
                      disabled={savingProviderId === provider.id}
                      onChange={(enabled) => void toggleProvider(provider, enabled)}
                    />
                    <IconButton label={`Edit ${provider.name}`} onClick={() => openEditProviderForm(provider)}>
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                    </IconButton>
                    <IconButton
                      label={`${isExpanded ? "Hide" : "View"} ${provider.name} models`}
                      onClick={() => setExpandedProviderId(current => current === provider.id ? null : provider.id)}
                    >
                      {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </IconButton>
                  </div>
                </div>

                {isExpanded ? (
                  <div className="border-t border-default">
                    {provider.models.length === 0 ? (
                      <div className="bg-canvas px-4 py-5 text-center text-xs font-semibold text-muted">
                        No models found. Edit the provider and save again with a valid API key.
                      </div>
                    ) : (
                      <div className="divide-y divide-[var(--color-border)]">
                        {provider.models.map(model => {
                          const isPrimary = model.id === route?.primary_model_id;
                          const isFallback = model.id === route?.fallback_model_id;
                          const modelEnabled = boolValue(model.enabled);
                          return (
                            <div key={model.id} className="ai-provider-model-row flex flex-col gap-3 bg-surface px-4 py-3">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="truncate text-xs font-extrabold text-default">{modelDisplayName(model)}</p>
                                  {isPrimary ? <StatusPill tone="active">Default</StatusPill> : null}
                                  {isFallback ? <StatusPill>Backup</StatusPill> : null}
                                  {!modelEnabled ? <StatusPill>Off</StatusPill> : null}
                                </div>
                                {model.model_name !== model.display_name ? (
                                  <p className="mt-1 truncate text-[11px] font-semibold text-muted">{model.model_name}</p>
                                ) : null}
                              </div>
                              <SwitchControl
                                checked={modelEnabled}
                                ariaLabel={`Toggle ${modelDisplayName(model)}`}
                                disabled={savingModelId === model.id}
                                onChange={(enabled) => void toggleModel(provider, model, enabled)}
                              />
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </GlassPanel>

      {isProviderFormOpen ? (
        <ProviderFormModal
          form={providerForm}
          isSaving={isSavingProvider}
          onChange={(patch) => setProviderForm(current => ({ ...current, ...patch }))}
          onClose={() => {
            setIsProviderFormOpen(false);
            setProviderForm(emptyProviderForm());
          }}
          onSubmit={saveProvider}
        />
      ) : null}
    </div>
  );
}
