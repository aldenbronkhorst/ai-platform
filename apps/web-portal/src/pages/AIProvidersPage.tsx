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
import { API_BASE_URL, fetchWithTimeout, type AccessTokenGetter } from "../hooks/useApi";

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
  provider_key?: string | null;
  provider_type: string;
  base_url: string;
  enabled: string;
  api_key_status: string;
  secret_reference?: string | null;
  models: ProviderModel[];
}

interface Route {
  task_type: string;
  label?: string | null;
  model_task_type?: string | null;
  primary_model_id?: string | null;
}

interface ProviderCatalogItem {
  key: string;
  name: string;
  provider_type: string;
  base_url: string;
  auth_label: string;
  supports_custom_name: boolean;
  supports_custom_base_url: boolean;
  configured_provider_id?: string | null;
}

interface ProviderListResponse {
  providers: Provider[];
  route?: Route | null;
  routes?: Route[];
  catalog?: ProviderCatalogItem[];
}

interface ProviderFormState {
  providerId: string | null;
  providerKey: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
}

interface EnabledModelRow {
  provider: Provider;
  model: ProviderModel;
}

interface ProviderRow {
  key: string;
  catalogItem: ProviderCatalogItem | null;
  provider: Provider | null;
}

interface PickerOption {
  value: string;
  label: string;
}

type ProviderSettingsSection = "providers" | "models";

const CHAT_ROUTE_TASK = "general_chat";
const CHAT_MODEL_TASK = "chat";
const VOICE_TRANSCRIPTION_MODEL_TASK = "voice_transcription";
const IMAGE_GENERATION_MODEL_TASK = "image_generation";
const OPENAI_COMPATIBLE_PROVIDER_TYPE = "openai_compatible";
const CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY = "custom_openai_compatible";

const routeDefinitions = [
  { taskType: CHAT_ROUTE_TASK, modelTaskType: CHAT_MODEL_TASK, label: "Chat", hint: "Default model for normal assistant chats." },
  { taskType: VOICE_TRANSCRIPTION_MODEL_TASK, modelTaskType: VOICE_TRANSCRIPTION_MODEL_TASK, label: "Voice", hint: "Speech-to-text model used for microphone input." },
  { taskType: IMAGE_GENERATION_MODEL_TASK, modelTaskType: IMAGE_GENERATION_MODEL_TASK, label: "Image", hint: "Image generation model for visual requests." },
];

const modelTaskOptions = [
  { value: CHAT_MODEL_TASK, label: "Chat" },
  { value: VOICE_TRANSCRIPTION_MODEL_TASK, label: "Voice transcription" },
  { value: IMAGE_GENERATION_MODEL_TASK, label: "Image generation" },
];

const emptyProviderForm = (): ProviderFormState => ({
  providerId: null,
  providerKey: "",
  name: "",
  baseUrl: "",
  apiKey: "",
  enabled: true,
});

function boolValue(value: string | undefined) {
  return value === "true";
}

function isActiveProvider(provider: Provider | null | undefined) {
  return Boolean(provider && boolValue(provider.enabled) && provider.api_key_status === "saved");
}

function modelDisplayName(model: ProviderModel) {
  return model.display_name || model.model_name;
}

function modelTaskType(model: ProviderModel) {
  return model.config_json?.task_type || CHAT_MODEL_TASK;
}

function modelOptionLabel(row: EnabledModelRow) {
  return `${row.provider.name} - ${modelDisplayName(row.model)}`;
}

function providerRowMatchesQuery(row: ProviderRow, query: string) {
  if (!query)
    return true;
  return (
    (row.catalogItem?.name || row.provider?.name || "").toLowerCase().includes(query)
    || (row.catalogItem?.base_url || row.provider?.base_url || "").toLowerCase().includes(query)
    || (row.provider?.name || "").toLowerCase().includes(query)
  );
}

function modelRowMatchesQuery(row: EnabledModelRow, query: string) {
  if (!query)
    return true;
  return (
    row.provider.name.toLowerCase().includes(query)
    || modelDisplayName(row.model).toLowerCase().includes(query)
    || row.model.model_name.toLowerCase().includes(query)
  );
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

async function authHeaders(getAccessToken: AccessTokenGetter, includeJson = false) {
  const headers: Record<string, string> = {};
  if (includeJson)
    headers["Content-Type"] = "application/json";
  const accessToken = await getAccessToken({ redirectOnFailure: true });
  if (!accessToken) throw new Error("Microsoft session expired. Please sign in again.");
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
          backgroundColor: checked ? "var(--ui-control-active-background)" : "var(--color-surface-raised)",
          borderColor: checked ? "var(--ui-stroke-primary)" : "var(--color-border)",
        }}
      >
        <span
          className="absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full transition-transform"
          style={{
            left: checked ? "24px" : "4px",
            backgroundColor: checked ? "var(--color-surface)" : "var(--color-text-muted)",
          }}
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
  catalog,
  form,
  isSaving,
  onChange,
  onClose,
  onSubmit,
}: {
  catalog: ProviderCatalogItem[];
  form: ProviderFormState;
  isSaving: boolean;
  onChange: (patch: Partial<ProviderFormState>) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const isEdit = Boolean(form.providerId);
  const selectedPreset = catalog.find(item => item.key === form.providerKey);
  const isCustom = form.providerKey === CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="relative w-full max-w-xl overflow-hidden rounded-lg border border-default bg-raised">
        <form onSubmit={onSubmit}>
          <div className="flex items-start justify-between gap-4 border-b border-default bg-raised p-5">
            <div>
              <h3 className="text-base font-extrabold text-default">{isEdit ? "Edit provider" : "Connect provider"}</h3>
              <p className="mt-1 text-xs font-semibold text-muted">
                Choose a known provider, paste the key, and the platform will sync available models.
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

            <FieldLabel label="Provider">
              <select
                value={form.providerKey}
                disabled={isEdit}
                onChange={(event) => {
                  const key = event.target.value;
                  const preset = catalog.find(item => item.key === key);
                  if (preset) {
                    onChange({
                      providerKey: key,
                      name: preset.name,
                      baseUrl: preset.base_url,
                    });
                    return;
                  }
                  onChange({
                    providerKey: key,
                    name: "",
                    baseUrl: "",
                  });
                }}
                className="w-full rounded-lg border border-default bg-surface px-3 py-2 text-sm font-semibold text-default outline-none focus:border-soft disabled:cursor-not-allowed disabled:opacity-60"
              >
                <option value="">Select provider</option>
                {catalog.map(provider => (
                  <option key={provider.key} value={provider.key}>{provider.name}</option>
                ))}
                <option value={CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY}>Custom OpenAI-compatible</option>
              </select>
            </FieldLabel>

            <FieldLabel label="Provider name">
              <TextField
                value={form.name}
                onChange={(event) => onChange({ name: event.target.value })}
                placeholder="Provider name"
                disabled={!isCustom && Boolean(selectedPreset)}
                required
              />
            </FieldLabel>

            <FieldLabel label="API endpoint">
              <TextField
                value={form.baseUrl}
                onChange={(event) => onChange({ baseUrl: event.target.value })}
                placeholder="https://api.provider.com/v1"
                disabled={!isCustom && Boolean(selectedPreset)}
                required
              />
            </FieldLabel>

            <FieldLabel label={selectedPreset?.auth_label || "API key"}>
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
              {isEdit ? "Save changes" : "Connect provider"}
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
  getAccessToken,
}: {
  getAccessToken: AccessTokenGetter;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [route, setRoute] = useState<Route | null>(null);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [catalog, setCatalog] = useState<ProviderCatalogItem[]>([]);
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
  const [openModelPicker, setOpenModelPicker] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showAvailableProviders, setShowAvailableProviders] = useState(false);
  const [showInactiveModels, setShowInactiveModels] = useState(false);

  const allModelRows = useMemo(() => {
    return providers.flatMap(provider => provider.models.map(model => ({ provider, model })));
  }, [providers]);

  const providerRows = useMemo<ProviderRow[]>(() => {
    const catalogKeys = new Set(catalog.map(item => item.key));
    const rows = catalog.map(item => ({
      key: item.key,
      catalogItem: item,
      provider: providers.find(provider => provider.id === item.configured_provider_id || provider.provider_key === item.key) || null,
    }));
    const customRows = providers
      .filter(provider => !provider.provider_key || !catalogKeys.has(provider.provider_key))
      .map(provider => ({
        key: provider.id,
        catalogItem: null,
        provider,
      }));
    return [...rows, ...customRows];
  }, [catalog, providers]);

  const activeProviderRows = useMemo(() => {
    return providerRows.filter(row => isActiveProvider(row.provider));
  }, [providerRows]);

  const availableProviderRows = useMemo(() => {
    return providerRows.filter(row => !isActiveProvider(row.provider));
  }, [providerRows]);

  const activeModelRows = useMemo(() => {
    return allModelRows.filter(row => isActiveProvider(row.provider) && boolValue(row.model.enabled));
  }, [allModelRows]);

  const inactiveModelRows = useMemo(() => {
    return allModelRows.filter(row => !isActiveProvider(row.provider) || !boolValue(row.model.enabled));
  }, [allModelRows]);

  const filteredActiveProviderRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return activeProviderRows.filter(row => providerRowMatchesQuery(row, query));
  }, [activeProviderRows, searchQuery]);

  const filteredAvailableProviderRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return availableProviderRows.filter(row => providerRowMatchesQuery(row, query));
  }, [availableProviderRows, searchQuery]);

  const filteredActiveModelRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return activeModelRows.filter(row => modelRowMatchesQuery(row, query));
  }, [activeModelRows, searchQuery]);

  const filteredInactiveModelRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return inactiveModelRows.filter(row => modelRowMatchesQuery(row, query));
  }, [inactiveModelRows, searchQuery]);

  const enabledRowsByTask = useMemo(() => {
    return providers.flatMap(provider => provider.models
      .filter(model => isActiveProvider(provider) && boolValue(model.enabled))
      .map(model => ({ provider, model })));
  }, [providers]);

  const modelOptionsForTask = useCallback((modelTask: string) => {
    return enabledRowsByTask.filter(row => modelTaskType(row.model) === modelTask).map(row => ({
      value: row.model.id,
      label: modelOptionLabel(row),
    }));
  }, [enabledRowsByTask]);

  const routeForTask = useCallback((taskType: string) => {
    return routes.find(item => item.task_type === taskType) || (taskType === CHAT_ROUTE_TASK ? route : null);
  }, [route, routes]);

  const primaryModelExists = useCallback((taskType: string, modelTask: string) => {
    const primaryModelId = routeForTask(taskType)?.primary_model_id || "";
    return enabledRowsByTask.some(row => row.model.id === primaryModelId && modelTaskType(row.model) === modelTask);
  }, [enabledRowsByTask, routeForTask]);

  const applyPayload = useCallback((payload: ProviderListResponse) => {
    setProviders(payload.providers);
    setRoute(payload.route || null);
    setRoutes(payload.routes || (payload.route ? [payload.route] : []));
    setCatalog(payload.catalog || []);
  }, []);

  const loadProviders = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetchWithTimeout(`${API_BASE_URL}/model-providers`, {
        headers: await authHeaders(getAccessToken),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
    } catch (err) {
      console.error("AI provider settings could not be reached.", err);
    } finally {
      setIsLoading(false);
    }
  }, [applyPayload, getAccessToken]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadProviders();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadProviders]);

  const saveProviderPayload = useCallback(async (form: ProviderFormState) => {
    const body: {
      provider_id?: string;
      provider_key?: string;
      name: string;
      provider_type?: string;
      base_url: string;
      api_key?: string;
      enabled: boolean;
    } = {
      provider_key: form.providerKey || CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY,
      name: form.name.trim(),
      base_url: form.baseUrl.trim(),
      enabled: form.enabled,
    };
    if (form.providerKey === CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY)
      body.provider_type = OPENAI_COMPATIBLE_PROVIDER_TYPE;
    if (form.providerId)
      body.provider_id = form.providerId;
    if (form.apiKey.trim())
      body.api_key = form.apiKey.trim();

    const response = await fetch(`${API_BASE_URL}/model-providers`, {
      method: "POST",
      headers: await authHeaders(getAccessToken, true),
      body: JSON.stringify(body),
    });
    if (!response.ok)
      throw new Error(await readApiError(response));
    return await response.json() as ProviderListResponse;
  }, [getAccessToken]);

  const openNewProviderForm = useCallback((preset?: ProviderCatalogItem | null) => {
    const nextPreset = preset || catalog.find(item => !item.configured_provider_id) || catalog[0] || null;
    setProviderForm(nextPreset ? {
      providerId: null,
      providerKey: nextPreset.key,
      name: nextPreset.name,
      baseUrl: nextPreset.base_url,
      apiKey: "",
      enabled: true,
    } : emptyProviderForm());
    setIsProviderFormOpen(true);
  }, [catalog]);

  const openEditProviderForm = useCallback((provider: Provider) => {
    const preset = catalog.find(item => item.key === provider.provider_key || item.configured_provider_id === provider.id);
    setProviderForm({
      providerId: provider.id,
      providerKey: preset?.key || provider.provider_key || CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY,
      name: provider.name,
      baseUrl: provider.base_url,
      apiKey: "",
      enabled: boolValue(provider.enabled),
    });
    setIsProviderFormOpen(true);
  }, [catalog]);

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
      const preset = catalog.find(item => item.key === provider.provider_key || item.configured_provider_id === provider.id);
      const payload = await saveProviderPayload({
        providerId: provider.id,
        providerKey: preset?.key || provider.provider_key || CUSTOM_OPENAI_COMPATIBLE_PROVIDER_KEY,
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
  }, [applyPayload, catalog, saveProviderPayload]);

  const deleteProvider = useCallback(async (provider: Provider) => {
    setDeletingProviderId(provider.id);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}`, {
        method: "DELETE",
        headers: await authHeaders(getAccessToken),
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
  }, [applyPayload, getAccessToken]);

  const toggleModel = useCallback(async (provider: Provider, model: ProviderModel, enabled: boolean) => {
    setSavingModelId(model.id);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}/models/${model.id}`, {
        method: "PATCH",
        headers: await authHeaders(getAccessToken, true),
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
  }, [applyPayload, getAccessToken]);

  const updateModelTask = useCallback(async (provider: Provider, model: ProviderModel, taskType: string) => {
    setSavingModelId(model.id);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/${provider.id}/models/${model.id}`, {
        method: "PATCH",
        headers: await authHeaders(getAccessToken, true),
        body: JSON.stringify({ task_type: taskType }),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
    } catch (err) {
      console.error("Model task could not be updated.", err);
    } finally {
      setSavingModelId(null);
    }
  }, [applyPayload, getAccessToken]);

  const updateModelRoute = useCallback(async (taskType: string, primaryId: string) => {
    if (!primaryId)
      return;
    setIsRouteSaving(true);
    try {
      const response = await fetch(`${API_BASE_URL}/model-providers/route`, {
        method: "PATCH",
        headers: await authHeaders(getAccessToken, true),
        body: JSON.stringify({
          task_type: taskType,
          primary_model_id: primaryId,
        }),
      });
      if (!response.ok)
        throw new Error(await readApiError(response));
      applyPayload(await response.json() as ProviderListResponse);
    } catch (err) {
      console.error("Model route could not be updated.", err);
    } finally {
      setIsRouteSaving(false);
    }
  }, [applyPayload, getAccessToken]);

  const renderProviderRow = (row: ProviderRow) => {
    const provider = row.provider;
    const catalogItem = row.catalogItem;
    const displayName = provider?.name || catalogItem?.name || "Provider";
    const baseUrl = provider?.base_url || catalogItem?.base_url || "";
    const isProviderEnabled = boolValue(provider?.enabled);
    const isProviderActive = isActiveProvider(provider);
    const isConfirmingDelete = provider ? confirmingDeleteProviderId === provider.id : false;
    const statusText = provider
      ? isProviderActive
        ? "Connected and active"
        : provider.api_key_status === "saved"
          ? "Connected but off"
          : "Needs API key"
      : "Not connected";

    return (
      <div key={row.key} className="settings-list-row provider-grid">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-extrabold text-default">{displayName}</h4>
          <p className="mt-1 truncate text-xs font-semibold text-muted">
            {statusText}{baseUrl ? ` - ${baseUrl}` : ""}
          </p>
        </div>

        <div className="flex items-center gap-2 lg:justify-end">
          {provider ? (
            <>
              <SwitchControl
                checked={isProviderEnabled}
                ariaLabel={`Toggle ${displayName}`}
                disabled={savingProviderId === provider.id}
                onChange={(enabled) => void toggleProvider(provider, enabled)}
              />
              <IconButton label={`Edit ${displayName}`} onClick={() => openEditProviderForm(provider)}>
                <SlidersHorizontal className="h-3.5 w-3.5" />
              </IconButton>
              {isConfirmingDelete ? (
                <>
                  <IconButton label={`Cancel deleting ${displayName}`} onClick={() => setConfirmingDeleteProviderId(null)}>
                    <X className="h-3.5 w-3.5" />
                  </IconButton>
                  <IconButton
                    label={`Confirm delete ${displayName}`}
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
                  label={`Delete ${displayName}`}
                  className="text-[var(--color-danger)] hover:text-[var(--color-danger)]"
                  disabled={deletingProviderId === provider.id}
                  onClick={() => setConfirmingDeleteProviderId(provider.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              )}
            </>
          ) : (
            <Button size="sm" onClick={() => openNewProviderForm(catalogItem)}>
              <Plus className="h-3.5 w-3.5" />
              Connect
            </Button>
          )}
        </div>
      </div>
    );
  };

  const renderModelRow = ({ provider, model }: EnabledModelRow) => {
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
          {!isActiveProvider(provider) ? (
            <p className="mt-1 truncate text-[11px] font-semibold text-muted">Provider inactive</p>
          ) : null}
        </div>

        <select
          value={modelTaskType(model)}
          disabled={savingModelId === model.id}
          onChange={(event) => void updateModelTask(provider, model, event.target.value)}
          className="w-full rounded-md border border-default bg-surface px-2.5 py-2 text-xs font-bold text-default outline-none focus:border-soft disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`Task for ${modelDisplayName(model)}`}
        >
          {modelTaskOptions.map(option => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>

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
  };

  return (
    <div className="settings-page mx-auto flex w-full max-w-6xl flex-col gap-4 pb-8">
      <div className="settings-page-header">
        <div className="min-w-0">
          <h2 className="settings-title text-xl">AI Providers</h2>
          <p className="settings-copy mt-1 max-w-2xl text-sm">
            Connect preset providers, sync their models, and route each capability to the right model.
          </p>
        </div>
        <div className="settings-actions">
          <Button size="sm" onClick={() => openNewProviderForm()}>
            <Plus className="h-3.5 w-3.5" />
            Connect provider
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

            {!isLoading && filteredActiveProviderRows.length === 0 ? (
              <EmptyState title={activeProviderRows.length === 0 ? "No active providers yet." : "No active providers match your search."}>
                <Button onClick={() => openNewProviderForm()}>
                  <Plus className="h-3.5 w-3.5" />
                  Connect provider
                </Button>
              </EmptyState>
            ) : null}

            {filteredActiveProviderRows.length > 0 ? (
              <div className="settings-list">
                <div className="settings-list-head provider-grid">
                  <span>Provider</span>
                  <span className="text-right">Actions</span>
                </div>

                {filteredActiveProviderRows.map(row => renderProviderRow(row))}
              </div>
            ) : null}

            {!isLoading && filteredAvailableProviderRows.length > 0 ? (
              <div className="settings-secondary-section">
                <button
                  type="button"
                  className="settings-disclosure-row"
                  aria-expanded={showAvailableProviders}
                  onClick={() => setShowAvailableProviders(value => !value)}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Plus className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">Add more providers</span>
                  </span>
                  <span className="settings-count">{filteredAvailableProviderRows.length}</span>
                </button>
                {showAvailableProviders ? (
                  <div className="settings-list settings-list-secondary">
                    {filteredAvailableProviderRows.map(row => renderProviderRow(row))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        ) : (
          <>
            <div className="settings-model-route">
              {routeDefinitions.map(definition => {
                const routeState = routeForTask(definition.taskType);
                const options = modelOptionsForTask(definition.modelTaskType);
                const primaryModelId = routeState?.primary_model_id || "";
                return (
                  <ModelPicker
                    key={definition.taskType}
                    label={`${definition.label} model`}
                    hint={options.length === 0 ? `Enable a ${definition.label.toLowerCase()} model first.` : definition.hint}
                    value={primaryModelExists(definition.taskType, definition.modelTaskType) ? primaryModelId : ""}
                    placeholder="No enabled models"
                    options={options}
                    isOpen={openModelPicker === definition.taskType}
                    disabled={options.length === 0 || isRouteSaving}
                    onOpenChange={(open) => setOpenModelPicker(open ? definition.taskType : null)}
                    onChange={(nextPrimaryId) => void updateModelRoute(definition.taskType, nextPrimaryId)}
                  />
                );
              })}
            </div>

              {isLoading ? (
                <div className="settings-empty">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted" />
                </div>
              ) : null}

              {!isLoading && filteredActiveModelRows.length === 0 ? (
                <EmptyState title={allModelRows.length === 0 ? "No models synced." : "No active models match your search."}>
                  {allModelRows.length === 0 ? (
                    <Button onClick={() => setActiveSection("providers")}>
                      Providers
                    </Button>
                  ) : null}
                </EmptyState>
              ) : null}

              {filteredActiveModelRows.length > 0 ? (
                <div className="settings-list">
                  <div className="settings-list-head model-grid">
                    <span>Model</span>
                    <span>Provider</span>
                    <span>Task</span>
                    <span className="text-right">Enabled</span>
                  </div>

                  {filteredActiveModelRows.map(row => renderModelRow(row))}
                </div>
              ) : null}

              {!isLoading && filteredInactiveModelRows.length > 0 ? (
                <div className="settings-secondary-section">
                  <button
                    type="button"
                    className="settings-disclosure-row"
                    aria-expanded={showInactiveModels}
                    onClick={() => setShowInactiveModels(value => !value)}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <SlidersHorizontal className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">Show inactive models</span>
                    </span>
                    <span className="settings-count">{filteredInactiveModelRows.length}</span>
                  </button>
                  {showInactiveModels ? (
                    <div className="settings-list settings-list-secondary">
                      {filteredInactiveModelRows.map(row => renderModelRow(row))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </>
        )}
      </section>

      {isProviderFormOpen ? (
        <ProviderFormModal
          catalog={catalog}
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
