import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Trash2,
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
  config_json?: {
    task_type?: string;
  } | null;
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

const CHAT_ROUTE_TASK = "general_chat";
const CHAT_MODEL_TASK = "chat";
const VOICE_TRANSCRIPTION_MODEL_TASK = "voice_transcription";

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

function boolString(value: boolean) {
  return value ? "true" : "false";
}

function modelDisplayName(model: ProviderModel) {
  return model.display_name || model.model_name;
}

function modelTaskType(model: ProviderModel) {
  return model.config_json?.task_type || CHAT_MODEL_TASK;
}

function isChatModel(model: ProviderModel) {
  return modelTaskType(model) === CHAT_MODEL_TASK;
}

function modelTaskLabel(model: ProviderModel) {
  return modelTaskType(model) === VOICE_TRANSCRIPTION_MODEL_TASK ? "Voice" : "Chat";
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

function modelCountByTask(provider: Provider, taskType: string) {
  return provider.models.filter(model => modelTaskType(model) === taskType).length;
}

function enabledModelCountByTask(provider: Provider, taskType: string) {
  return provider.models.filter(model => boolValue(model.enabled) && modelTaskType(model) === taskType).length;
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

function friendlyErrorMessage(err: unknown, fallback: string) {
  if (isAbortError(err))
    return "";
  const message = err instanceof Error ? err.message : "";
  if (!message || message === "Failed to fetch")
    return fallback;
  return message;
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

function createPreviewModels(providerId: string, modelPrefix: string): ProviderModel[] {
  const normalized = modelPrefix.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "model";
  return [
    {
      id: `${providerId}-chat`,
      display_name: "Chat model",
      model_name: `${normalized}-chat`,
      deployment_name: `${normalized}-chat`,
      supports_tools: "true",
      supports_json_schema: "true",
      context_window: 128000,
      enabled: "true",
      config_json: { task_type: CHAT_MODEL_TASK },
    },
    {
      id: `${providerId}-fast`,
      display_name: "Fast model",
      model_name: `${normalized}-fast`,
      deployment_name: `${normalized}-fast`,
      supports_tools: "true",
      supports_json_schema: "false",
      context_window: 64000,
      enabled: "true",
      config_json: { task_type: CHAT_MODEL_TASK },
    },
    {
      id: `${providerId}-reasoning`,
      display_name: "Reasoning model",
      model_name: `${normalized}-reasoning`,
      deployment_name: `${normalized}-reasoning`,
      supports_tools: "false",
      supports_json_schema: "true",
      context_window: 256000,
      enabled: "false",
      config_json: { task_type: CHAT_MODEL_TASK },
    },
    {
      id: `${providerId}-voice`,
      display_name: "Voice transcription",
      model_name: `${normalized}-asr`,
      deployment_name: `${normalized}-asr`,
      supports_tools: "false",
      supports_json_schema: "false",
      context_window: null,
      enabled: "true",
      config_json: { task_type: VOICE_TRANSCRIPTION_MODEL_TASK },
    },
  ];
}

function previewProviderPayload(): ProviderListResponse {
  const firstProvider: Provider = {
    id: "preview-provider-main",
    name: "Example Provider",
    provider_type: "openai_compatible",
    base_url: "https://api.example.com/v1",
    enabled: "true",
    api_key_status: "saved",
    secret_reference: "preview-secret",
    models: createPreviewModels("preview-provider-main", "example"),
  };
  const secondProvider: Provider = {
    id: "preview-provider-research",
    name: "Research Provider",
    provider_type: "openai_compatible",
    base_url: "https://models.example.net/v1",
    enabled: "false",
    api_key_status: "saved",
    secret_reference: "preview-secret-research",
    models: createPreviewModels("preview-provider-research", "research"),
  };
  return {
    providers: [firstProvider, secondProvider],
    route: {
      task_type: CHAT_ROUTE_TASK,
      primary_model_id: firstProvider.models[0]?.id,
    },
  };
}

function routeForProviders(providers: Provider[], currentRoute: Route | null): Route | null {
  const enabledRows = providers.flatMap(provider => provider.models
    .filter(model => boolValue(provider.enabled) && boolValue(model.enabled) && isChatModel(model))
    .map(model => ({ provider, model })));
  if (enabledRows.length === 0)
    return null;
  const currentExists = enabledRows.some(row => row.model.id === currentRoute?.primary_model_id);
  return {
    task_type: CHAT_ROUTE_TASK,
    primary_model_id: currentExists ? currentRoute?.primary_model_id : enabledRows[0].model.id,
  };
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
      <div className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-default bg-raised shadow-2xl">
        <form onSubmit={onSubmit}>
          <div className="flex items-start justify-between gap-4 border-b border-default bg-raised p-5">
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

          <div className="space-y-4 bg-raised p-5">
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

          <div className="flex justify-end gap-2 border-t border-default bg-subtle p-5">
            <GlassButton type="button" onClick={onClose}>
              Cancel
            </GlassButton>
            <GlassButton type="submit" variant="primary" disabled={isSaving}>
              {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {isEdit ? "Save changes" : "Save provider"}
            </GlassButton>
          </div>
        </form>
      </div>
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
  const pickerRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find(option => option.value === value);
  const displayValue = selected?.label || placeholder;

  useEffect(() => {
    if (!isOpen)
      return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!pickerRef.current || pickerRef.current.contains(event.target as Node))
        return;
      onOpenChange(false);
    };

    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [isOpen, onOpenChange]);

  return (
    <div ref={pickerRef} className="relative space-y-1.5">
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
        <div className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-default bg-surface shadow-2xl">
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

export function AIProvidersPage({
  accessToken,
  previewMode = false,
}: {
  accessToken: string;
  previewMode?: boolean;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [route, setRoute] = useState<Route | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isProviderFormOpen, setIsProviderFormOpen] = useState(false);
  const [providerForm, setProviderForm] = useState<ProviderFormState>(emptyProviderForm);
  const [isSavingProvider, setIsSavingProvider] = useState(false);
  const [savingProviderId, setSavingProviderId] = useState<string | null>(null);
  const [deletingProviderId, setDeletingProviderId] = useState<string | null>(null);
  const [confirmingDeleteProviderId, setConfirmingDeleteProviderId] = useState<string | null>(null);
  const [savingModelId, setSavingModelId] = useState<string | null>(null);
  const [isRouteSaving, setIsRouteSaving] = useState(false);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);
  const [openModelPicker, setOpenModelPicker] = useState<"primary" | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [testResult, setTestResult] = useState<ProviderTestResponse | null>(null);
  const isPreviewMode = previewMode;

  const enabledRows = useMemo(() => {
    return providers.flatMap(provider => provider.models
      .filter(model => boolValue(provider.enabled) && boolValue(model.enabled) && isChatModel(model))
      .map(model => ({ provider, model })));
  }, [providers]);

  const chatModelOptions = useMemo(() => {
    return enabledRows.map(row => ({
      value: row.model.id,
      label: modelOptionLabel(row),
    }));
  }, [enabledRows]);

  const primaryModelId = route?.primary_model_id || "";
  const primaryModelExists = enabledRows.some(row => row.model.id === primaryModelId);

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
    if (isPreviewMode) {
      applyPayload(previewProviderPayload());
      setIsLoading(false);
      return;
    }
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        headers: authHeaders(accessToken),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
    } catch (err) {
      const message = friendlyErrorMessage(err, "AI provider settings could not be reached.");
      if (message) {
        setNotice({
          tone: "danger",
          text: message,
        });
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, applyPayload, isPreviewMode]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadProviders();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadProviders]);

  const saveProviderPayload = useCallback(async (form: ProviderFormState) => {
    if (isPreviewMode) {
      const providerId = form.providerId || `preview-provider-${Date.now()}`;
      const existing = providers.find(provider => provider.id === providerId);
      const name = form.name.trim();
      const hasSavedKey = Boolean(form.apiKey.trim()) || existing?.api_key_status === "saved";
      const nextProvider: Provider = {
        id: providerId,
        name,
        provider_type: "openai_compatible",
        base_url: form.baseUrl.trim(),
        enabled: boolString(form.enabled),
        api_key_status: hasSavedKey ? "saved" : "missing",
        secret_reference: "preview-secret",
        models: existing?.models.length ? existing.models : createPreviewModels(providerId, name),
      };
      const nextProviders = existing
        ? providers.map(provider => provider.id === providerId ? nextProvider : provider)
        : [...providers, nextProvider];
      return {
        providers: nextProviders,
        route: routeForProviders(nextProviders, route),
        sync: {
          success: true,
          message: `Synced ${nextProvider.models.length} models.`,
          model_count: nextProvider.models.length,
        },
      } satisfies ProviderListResponse;
    }

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
  }, [accessToken, isPreviewMode, providers, route]);

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

  const deleteProvider = useCallback(async (provider: Provider) => {
    setDeletingProviderId(provider.id);
    setNotice(null);
    try {
      if (isPreviewMode) {
        const nextProviders = providers.filter(item => item.id !== provider.id);
        applyPayload({
          providers: nextProviders,
          route: routeForProviders(nextProviders, route),
        });
        setExpandedProviderId(current => current === provider.id ? null : current);
        setOpenModelPicker(null);
        setConfirmingDeleteProviderId(null);
        setNotice({
          tone: "success",
          text: "Provider deleted.",
        });
        return;
      }
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}`, {
        method: "DELETE",
        headers: authHeaders(accessToken),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
      setExpandedProviderId(current => current === provider.id ? null : current);
      setOpenModelPicker(null);
      setConfirmingDeleteProviderId(null);
      setNotice({
        tone: "success",
        text: "Provider deleted.",
      });
    } catch (err) {
      setNotice({
        tone: "danger",
        text: err instanceof Error ? err.message : "Provider could not be deleted.",
      });
    } finally {
      setDeletingProviderId(null);
    }
  }, [accessToken, applyPayload, isPreviewMode, providers, route]);

  const toggleModel = useCallback(async (provider: Provider, model: ProviderModel, enabled: boolean) => {
    setSavingModelId(model.id);
    setNotice(null);
    try {
      if (isPreviewMode) {
        const nextProviders = providers.map(item => item.id === provider.id
          ? {
              ...item,
              models: item.models.map(itemModel => itemModel.id === model.id
                ? { ...itemModel, enabled: boolString(enabled) }
                : itemModel),
            }
          : item);
        applyPayload({
          providers: nextProviders,
          route: routeForProviders(nextProviders, route),
        });
        setNotice({
          tone: "success",
          text: enabled ? "Model enabled." : "Model disabled.",
        });
        return;
      }
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
  }, [accessToken, applyPayload, isPreviewMode, providers, route]);

  const updateChatRoute = useCallback(async (primaryId: string) => {
    if (!primaryId)
      return;
    setIsRouteSaving(true);
    setNotice(null);
    try {
      if (isPreviewMode) {
        applyPayload({
          providers,
          route: {
            task_type: CHAT_ROUTE_TASK,
            primary_model_id: primaryId,
          },
        });
        setNotice({
          tone: "success",
          text: "Chat model updated.",
        });
        return;
      }
      const response = await fetch(`${API_BASE_URL}/model-providers/route`, {
        method: "PATCH",
        headers: authHeaders(accessToken, true),
        body: JSON.stringify({
          primary_model_id: primaryId,
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
  }, [accessToken, applyPayload, isPreviewMode, providers]);

  const testChatModel = useCallback(async () => {
    const selected = enabledRows.find(row => row.model.id === primaryModelId);
    if (!selected) {
      setTestResult({ success: false, message: "Choose a default chat model first." });
      return;
    }

    setTestingKey(selected.model.id);
    setTestResult(null);
    try {
      if (isPreviewMode) {
        setTestResult({
          success: true,
          message: `Preview test passed for ${modelDisplayName(selected.model)}.`,
          provider: selected.provider.name,
          model: selected.model.model_name,
        });
        return;
      }
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
  }, [accessToken, enabledRows, isPreviewMode, primaryModelId]);

  const activeProviderCount = providers.filter(provider => boolValue(provider.enabled)).length;
  const totalModelCount = providers.reduce((count, provider) => count + provider.models.length, 0);
  const chatModelCount = providers.reduce((count, provider) => count + provider.models.filter(isChatModel).length, 0);
  const voiceModelCount = providers.reduce(
    (count, provider) => count + provider.models.filter(model => modelTaskType(model) === VOICE_TRANSCRIPTION_MODEL_TASK).length,
    0,
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8">
      <GlassPanel className="relative z-20 overflow-visible rounded-2xl p-0">
        <div className="flex flex-col gap-4 border-b border-default p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-default">AI Providers</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              Connect API providers, sync their available models, and choose the model chat should use.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusPill tone={activeProviderCount > 0 ? "active" : "default"}>
                {activeProviderCount} active
              </StatusPill>
              <StatusPill>{providers.length} providers</StatusPill>
              <StatusPill>{chatModelCount} chat</StatusPill>
              <StatusPill>{voiceModelCount} voice</StatusPill>
              <StatusPill>{totalModelCount} total</StatusPill>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
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

        {notice || testResult ? (
          <div className="space-y-2 border-b border-default px-5 py-3 sm:px-6">
            {notice ? (
              <div className={`flex items-start gap-2 text-xs font-semibold ${
                notice.tone === "success" ? "text-default" : "text-[var(--color-danger)]"
              }`}>
                {notice.tone === "success" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
                <span>{notice.text}</span>
              </div>
            ) : null}

            {testResult ? (
              <div className={`flex items-start gap-2 text-xs font-semibold ${
                testResult.success ? "text-default" : "text-[var(--color-danger)]"
              }`}>
                {testResult.success ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />}
                <span>{testResult.message}</span>
              </div>
            ) : null}
          </div>
        ) : null}

        <section className="relative z-30 border-b border-default p-5 sm:p-6">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,520px)_auto] lg:items-end">
            <div className="min-w-0">
              <h3 className="text-sm font-extrabold text-default">Chat model</h3>
              <p className="mt-1 text-xs font-semibold text-muted">
                The selected model is used for normal chat.
              </p>
            </div>
            <ModelPicker
              label="Default model"
              hint={enabledRows.length === 0 ? "Enable a provider model first." : "Models come from enabled providers."}
              value={primaryModelExists ? primaryModelId : ""}
              placeholder="No enabled models"
              options={chatModelOptions}
              isOpen={openModelPicker === "primary"}
              disabled={enabledRows.length === 0 || isRouteSaving}
              onOpenChange={(open) => setOpenModelPicker(open ? "primary" : null)}
              onChange={(nextPrimaryId) => void updateChatRoute(nextPrimaryId)}
            />
            <GlassButton size="sm" onClick={() => void testChatModel()} disabled={!primaryModelExists || Boolean(testingKey)}>
              {testingKey ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />}
              Test
            </GlassButton>
          </div>
        </section>

        <section className="relative z-10">
          <div className="flex items-center justify-between gap-3 border-b border-default px-5 py-4 sm:px-6">
            <div>
              <h3 className="text-sm font-extrabold text-default">Providers</h3>
              <p className="mt-1 text-xs font-semibold text-muted">
                Open a provider to review the synced models.
              </p>
            </div>
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted" /> : null}
          </div>

          {!isLoading && providers.length === 0 ? (
            <div className="px-5 py-10 text-center sm:px-6">
              <p className="text-sm font-bold text-default">No providers configured.</p>
              <p className="mt-1 text-xs font-semibold text-muted">Add a provider and the model list will sync automatically.</p>
              <GlassButton className="mx-auto mt-4" onClick={openNewProviderForm}>
                <Plus className="h-3.5 w-3.5" />
                Add provider
              </GlassButton>
            </div>
          ) : null}

          {providers.length > 0 ? (
            <div className="divide-y divide-[var(--color-border)]">
              <div className="hidden grid-cols-[minmax(0,2fr)_130px_170px_180px] gap-4 bg-subtle px-5 py-2 text-[10px] font-bold uppercase tracking-wide text-muted lg:grid sm:px-6">
                <span>Provider</span>
                <span>Models</span>
                <span>Key</span>
                <span className="text-right">Actions</span>
              </div>

              {providers.map(provider => {
                const isExpanded = expandedProviderId === provider.id;
                const isProviderEnabled = boolValue(provider.enabled);
                const routeUsesProvider = provider.models.some(model => model.id === route?.primary_model_id);
                const providerEnabledModelCount = enabledModelCount(provider);
                const providerChatModelCount = modelCountByTask(provider, CHAT_MODEL_TASK);
                const providerVoiceModelCount = modelCountByTask(provider, VOICE_TRANSCRIPTION_MODEL_TASK);
                const providerEnabledChatModelCount = enabledModelCountByTask(provider, CHAT_MODEL_TASK);
                const providerEnabledVoiceModelCount = enabledModelCountByTask(provider, VOICE_TRANSCRIPTION_MODEL_TASK);
                const isConfirmingDelete = confirmingDeleteProviderId === provider.id;

                return (
                  <div key={provider.id} className="bg-canvas">
                    <div className="grid gap-3 px-5 py-4 sm:px-6 lg:grid-cols-[minmax(0,2fr)_130px_170px_180px] lg:items-center">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="truncate text-sm font-extrabold text-default">{provider.name}</h4>
                          <StatusPill tone={isProviderEnabled ? "active" : "default"}>
                            {isProviderEnabled ? "On" : "Off"}
                          </StatusPill>
                          {routeUsesProvider ? <StatusPill tone="active">Default chat</StatusPill> : null}
                          {provider.models.length === 0 ? <StatusPill tone="warning">No models</StatusPill> : null}
                        </div>
                        <p className="mt-1 truncate text-xs font-semibold text-muted">{provider.base_url}</p>
                      </div>

                      <div className="text-xs font-bold text-muted">
                        <span className="lg:hidden">Models: </span>
                        {providerEnabledChatModelCount} of {providerChatModelCount} chat on
                        <span className="mx-1 text-muted">·</span>
                        {providerEnabledVoiceModelCount} of {providerVoiceModelCount} voice on
                        <span className="sr-only">. {providerEnabledModelCount} of {provider.models.length} total models on.</span>
                      </div>

                      <div className={`inline-flex min-w-0 items-center gap-1 text-xs font-bold ${apiKeyStatusClass(provider.api_key_status)}`}>
                        <KeyRound className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{apiKeyStatusLabel(provider.api_key_status)}</span>
                      </div>

                      <div className="flex items-center gap-2 lg:justify-end">
                        <SwitchControl
                          checked={isProviderEnabled}
                          ariaLabel={`Toggle ${provider.name}`}
                          disabled={savingProviderId === provider.id}
                          onChange={(enabled) => void toggleProvider(provider, enabled)}
                        />
                        <IconButton label={`Edit ${provider.name}`} onClick={() => openEditProviderForm(provider)}>
                          <SlidersHorizontal className="h-3.5 w-3.5" />
                        </IconButton>
                        {isConfirmingDelete ? (
                          <>
                            <IconButton label={`Cancel deleting ${provider.name}`} onClick={() => setConfirmingDeleteProviderId(null)}>
                              <X className="h-3.5 w-3.5" />
                            </IconButton>
                            <IconButton
                              label={`Confirm delete ${provider.name}`}
                              className="text-[var(--color-danger)] hover:text-[var(--color-danger)]"
                              disabled={deletingProviderId === provider.id}
                              onClick={() => void deleteProvider(provider)}
                            >
                              {deletingProviderId === provider.id
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Trash2 className="h-3.5 w-3.5" />}
                            </IconButton>
                          </>
                        ) : (
                          <IconButton
                            label={`Delete ${provider.name}`}
                            className="text-[var(--color-danger)] hover:text-[var(--color-danger)]"
                            disabled={deletingProviderId === provider.id}
                            onClick={() => setConfirmingDeleteProviderId(provider.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </IconButton>
                        )}
                        <IconButton
                          label={`${isExpanded ? "Hide" : "View"} ${provider.name} models`}
                          onClick={() => setExpandedProviderId(current => current === provider.id ? null : provider.id)}
                        >
                          {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                        </IconButton>
                      </div>
                    </div>

                    {isExpanded ? (
                      <div className="border-t border-default bg-subtle">
                        {provider.models.length === 0 ? (
                          <div className="px-5 py-5 text-center text-xs font-semibold text-muted sm:px-6">
                            No models found. Edit the provider and save again with a valid API key.
                          </div>
                        ) : (
                          <div className="divide-y divide-[var(--color-border)]">
                            {provider.models.map(model => {
                              const isPrimary = model.id === route?.primary_model_id;
                              const modelEnabled = boolValue(model.enabled);
                              return (
                                <div key={model.id} className="grid gap-3 px-5 py-3 sm:px-6 lg:grid-cols-[minmax(0,1fr)_140px_80px] lg:items-center">
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <p className="truncate text-xs font-extrabold text-default">{modelDisplayName(model)}</p>
                                      <StatusPill tone={isChatModel(model) ? "default" : "active"}>{modelTaskLabel(model)}</StatusPill>
                                      {isPrimary ? <StatusPill tone="active">Default</StatusPill> : null}
                                      {!modelEnabled ? <StatusPill>Off</StatusPill> : null}
                                    </div>
                                    {model.model_name !== model.display_name ? (
                                      <p className="mt-1 truncate text-[11px] font-semibold text-muted">{model.model_name}</p>
                                    ) : null}
                                  </div>
                                  <p className="text-[11px] font-semibold text-muted">
                                    {isChatModel(model)
                                      ? model.context_window ? `${model.context_window.toLocaleString()} tokens` : "Context unknown"
                                      : "Voice transcription"}
                                  </p>
                                  <div className="lg:justify-self-end">
                                    <SwitchControl
                                      checked={modelEnabled}
                                      ariaLabel={`Toggle ${modelDisplayName(model)}`}
                                      disabled={savingModelId === model.id}
                                      onChange={(enabled) => void toggleModel(provider, model, enabled)}
                                    />
                                  </div>
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
          ) : null}
        </section>
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
