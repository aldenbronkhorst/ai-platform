import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ButtonHTMLAttributes, FormEvent, ReactNode } from "react";
import {
  ChevronDown,
  Loader2,
  Plus,
  Save,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "../components/ui/Button";
import { TextField } from "../components/ui/TextField";
import { SurfacePanel } from "../components/ui/SurfacePanel";
import { API_BASE_URL, fetchWithTimeout } from "../hooks/useApi";

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

interface ProviderListResponse {
  providers: Provider[];
  route?: Route | null;
}

interface ProviderFormState {
  providerId: string | null;
  name: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
}

interface EnabledModelRow {
  provider: Provider;
  model: ProviderModel;
}

interface PickerOption {
  value: string;
  label: string;
}

type ProviderSettingsSection = "providers" | "models";

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

function modelOptionLabel(row: EnabledModelRow) {
  return `${row.provider.name} - ${modelDisplayName(row.model)}`;
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
      className={`inline-flex h-9 w-9 items-center justify-center rounded-md border border-default bg-surface text-muted outline-none transition-colors hover-bg-subtle hover-text-default focus:border-soft disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
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
        className="relative h-6 w-11 rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          backgroundColor: checked ? "#6d6d6d" : "var(--color-surface-raised)",
          borderColor: checked ? "#6d6d6d" : "var(--color-border)",
        }}
      >
        <span
          className="absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-white transition-transform"
          style={{ left: checked ? "24px" : "4px" }}
        />
      </button>
      {label ? <span className="text-xs font-bold text-muted">{label}</span> : null}
    </div>
  );
}

function EmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="settings-empty">
      <p className="settings-title text-sm">{title}</p>
      <div className="mt-4 flex justify-center">{children}</div>
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
      <div className="relative w-full max-w-xl overflow-hidden rounded-lg border border-default bg-raised">
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
            <div className="flex items-center justify-between rounded-lg border border-default bg-surface px-4 py-3">
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
              <TextField
                value={form.name}
                onChange={(event) => onChange({ name: event.target.value })}
                placeholder="Provider name"
                required
              />
            </FieldLabel>

            <FieldLabel label="API endpoint">
              <TextField
                value={form.baseUrl}
                onChange={(event) => onChange({ baseUrl: event.target.value })}
                placeholder="https://api.provider.com/v1"
                required
              />
            </FieldLabel>

            <FieldLabel label="API key">
              <TextField
                type="password"
                value={form.apiKey}
                onChange={(event) => onChange({ apiKey: event.target.value })}
                placeholder={isEdit ? "Leave blank to keep saved key" : "Paste API key"}
              />
            </FieldLabel>
          </div>

          <div className="flex justify-end gap-2 border-t border-default bg-subtle p-5">
            <Button type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={isSaving}>
              {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {isEdit ? "Save changes" : "Save provider"}
            </Button>
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
        className="flex h-12 w-full items-center justify-between gap-3 rounded-lg border border-default bg-transparent px-4 text-left text-xs font-semibold text-default outline-none transition-colors focus:border-soft disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="min-w-0 truncate">{displayValue}</span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-muted transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen && !disabled ? (
        <div className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-lg border border-default bg-surface">
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
                  className={`flex w-full items-center rounded-md px-3 py-2.5 text-left text-xs font-semibold transition-colors hover-bg-subtle ${
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
  const [activeSection, setActiveSection] = useState<ProviderSettingsSection>("providers");
  const [openModelPicker, setOpenModelPicker] = useState<"primary" | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const isPreviewMode = previewMode;

  const modelRows = useMemo(() => {
    return providers.flatMap(provider => provider.models.map(model => ({ provider, model })));
  }, [providers]);

  const filteredProviders = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query)
      return providers;
    return providers.filter(provider => (
      provider.name.toLowerCase().includes(query)
      || provider.base_url.toLowerCase().includes(query)
    ));
  }, [providers, searchQuery]);

  const filteredModelRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query)
      return modelRows;
    return modelRows.filter(({ provider, model }) => (
      provider.name.toLowerCase().includes(query)
      || modelDisplayName(model).toLowerCase().includes(query)
      || model.model_name.toLowerCase().includes(query)
    ));
  }, [modelRows, searchQuery]);

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
  }, []);

  const loadProviders = useCallback(async () => {
    setIsLoading(true);
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
      console.error("AI provider settings could not be reached.", err);
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
  }, []);

  const saveProvider = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    setIsSavingProvider(true);
    try {
      const payload = await saveProviderPayload(providerForm);
      applyPayload(payload);
      setActiveSection("models");
      setProviderForm(emptyProviderForm());
      setIsProviderFormOpen(false);
    } catch (err) {
      console.error("Provider could not be saved.", err);
    } finally {
      setIsSavingProvider(false);
    }
  }, [applyPayload, providerForm, saveProviderPayload]);

  const toggleProvider = useCallback(async (provider: Provider, enabled: boolean) => {
    setSavingProviderId(provider.id);
    try {
      const payload = await saveProviderPayload({
        providerId: provider.id,
        name: provider.name,
        baseUrl: provider.base_url,
        apiKey: "",
        enabled,
      });
      applyPayload(payload);
    } catch (err) {
      console.error("Provider could not be updated.", err);
    } finally {
      setSavingProviderId(null);
    }
  }, [applyPayload, saveProviderPayload]);

  const deleteProvider = useCallback(async (provider: Provider) => {
    setDeletingProviderId(provider.id);
    try {
      if (isPreviewMode) {
        const nextProviders = providers.filter(item => item.id !== provider.id);
        applyPayload({
          providers: nextProviders,
          route: routeForProviders(nextProviders, route),
        });
        setOpenModelPicker(null);
        setConfirmingDeleteProviderId(null);
        return;
      }
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}`, {
        method: "DELETE",
        headers: authHeaders(accessToken),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
      setOpenModelPicker(null);
      setConfirmingDeleteProviderId(null);
    } catch (err) {
      console.error("Provider could not be deleted.", err);
    } finally {
      setDeletingProviderId(null);
    }
  }, [accessToken, applyPayload, isPreviewMode, providers, route]);

  const toggleModel = useCallback(async (provider: Provider, model: ProviderModel, enabled: boolean) => {
    setSavingModelId(model.id);
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
    } catch (err) {
      console.error("Model could not be updated.", err);
    } finally {
      setSavingModelId(null);
    }
  }, [accessToken, applyPayload, isPreviewMode, providers, route]);

  const updateChatRoute = useCallback(async (primaryId: string) => {
    if (!primaryId)
      return;
    setIsRouteSaving(true);
    try {
      if (isPreviewMode) {
        applyPayload({
          providers,
          route: {
            task_type: CHAT_ROUTE_TASK,
            primary_model_id: primaryId,
          },
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
    } catch (err) {
      console.error("Chat model could not be updated.", err);
    } finally {
      setIsRouteSaving(false);
    }
  }, [accessToken, applyPayload, isPreviewMode, providers]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8">
      <SurfacePanel className="settings-panel relative z-20">
        <div className="settings-page-header">
          <div className="min-w-0">
            <h2 className="settings-title text-xl">AI Providers</h2>
            <p className="settings-copy mt-1 max-w-2xl text-sm">
              Connect API providers, sync their available models, and choose the model chat should use.
            </p>
          </div>
          <div className="settings-actions">
            <Button size="sm" onClick={openNewProviderForm}>
              <Plus className="h-3.5 w-3.5" />
              Add provider
            </Button>
          </div>
        </div>

        <div className="settings-toolbar">
          <div className="settings-tabs">
            <button
              type="button"
              className={`settings-tab ${activeSection === "providers" ? "settings-tab-active" : ""}`}
              onClick={() => {
                setActiveSection("providers");
                setSearchQuery("");
              }}
            >
              Providers
            </button>
            <button
              type="button"
              className={`settings-tab ${activeSection === "models" ? "settings-tab-active" : ""}`}
              onClick={() => {
                setActiveSection("models");
                setSearchQuery("");
              }}
            >
              Models
            </button>
          </div>
          <input
            className="settings-search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={activeSection === "providers" ? "Search providers..." : "Search models..."}
          />
        </div>

        <section className="settings-content">
          {activeSection === "providers" ? (
            <>
              {isLoading ? (
                <div className="settings-empty">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted" />
                </div>
              ) : null}

              {!isLoading && filteredProviders.length === 0 ? (
                <EmptyState title={providers.length === 0 ? "No providers configured." : "No providers match your search."}>
                  {providers.length === 0 ? (
                    <Button onClick={openNewProviderForm}>
                      <Plus className="h-3.5 w-3.5" />
                      Add provider
                    </Button>
                  ) : null}
                </EmptyState>
              ) : null}

              {filteredProviders.length > 0 ? (
                <div className="settings-list">
                  <div className="settings-list-head provider-grid">
                    <span>Provider</span>
                    <span className="text-right">Actions</span>
                  </div>

                  {filteredProviders.map(provider => {
                    const isProviderEnabled = boolValue(provider.enabled);
                    const isConfirmingDelete = confirmingDeleteProviderId === provider.id;

                    return (
                      <div key={provider.id} className="settings-list-row provider-grid">
                        <div className="min-w-0">
                          <h4 className="truncate text-sm font-extrabold text-default">{provider.name}</h4>
                          <p className="mt-1 truncate text-xs font-semibold text-muted">{provider.base_url}</p>
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
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div className="settings-model-route">
                <ModelPicker
                  label="Default model"
                  hint={enabledRows.length === 0 ? "Enable a chat model first." : "Only enabled chat models appear here."}
                  value={primaryModelExists ? primaryModelId : ""}
                  placeholder="No enabled models"
                  options={chatModelOptions}
                  isOpen={openModelPicker === "primary"}
                  disabled={enabledRows.length === 0 || isRouteSaving}
                  onOpenChange={(open) => setOpenModelPicker(open ? "primary" : null)}
                  onChange={(nextPrimaryId) => void updateChatRoute(nextPrimaryId)}
                />
              </div>

              {isLoading ? (
                <div className="settings-empty">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted" />
                </div>
              ) : null}

              {!isLoading && filteredModelRows.length === 0 ? (
                <EmptyState title={modelRows.length === 0 ? "No models synced." : "No models match your search."}>
                  {modelRows.length === 0 ? (
                    <Button onClick={() => setActiveSection("providers")}>
                      Providers
                    </Button>
                  ) : null}
                </EmptyState>
              ) : null}

              {filteredModelRows.length > 0 ? (
                <div className="settings-list">
                  <div className="settings-list-head model-grid">
                    <span>Model</span>
                    <span>Provider</span>
                    <span className="text-right">Enabled</span>
                  </div>

                  {filteredModelRows.map(({ provider, model }) => {
                    const modelEnabled = boolValue(model.enabled);

                    return (
                      <div key={model.id} className="settings-list-row model-grid">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-extrabold text-default">{modelDisplayName(model)}</p>
                          {model.model_name !== model.display_name ? (
                            <p className="mt-1 truncate text-[11px] font-semibold text-muted">{model.model_name}</p>
                          ) : null}
                        </div>

                        <div className="min-w-0">
                          <p className="truncate text-xs font-bold text-default">{provider.name}</p>
                        </div>

                        <div className="xl:justify-self-end">
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
              ) : null}
            </>
          )}
        </section>
      </SurfacePanel>

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
