export interface ConnectorField {
  name: string;
  label?: string;
  type?: string;
  required?: boolean;
  secret?: boolean;
  placeholder?: string;
}

export interface ConnectorManifest {
  id: string;
  display_name?: string;
  subtitle?: string;
  version?: string;
  auth_method?: string;
  connection_fields?: ConnectorField[];
}

export interface ConnectorMeta {
  connector_key: string;
  display_name?: string;
  subtitle?: string;
  version?: string;
  status?: string;
  auth_method?: string;
  last_verified_at?: string | null;
  actions_available?: string[];
  state?: {
    configured?: boolean;
    account_status?: string;
    source?: string;
  };
  manifest?: ConnectorManifest | null;
  configuration?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  identity?: Record<string, unknown>;
  error?: string;
}

export type StatusTone = "success" | "danger" | "warning" | "neutral";

export function formatStatusLabel(status: string) {
  return status
    .split("_")
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function formatDateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "-";
}

export function getStatusTone(status?: string, hasError = false): StatusTone {
  if (hasError || status === "error" || status === "failed" || status === "unavailable") return "danger";
  if (status === "connected" || status === "active" || status === "ready") return "success";
  if (status === "not_connected" || status === "disconnected" || status === "warning") return "warning";
  return "neutral";
}

export function panelToneClass(tone: StatusTone) {
  if (tone === "success") return "border-[var(--color-success)]/25 bg-[var(--color-success)]/5";
  if (tone === "danger") return "border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5";
  if (tone === "warning") return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/5";
  return "border-default bg-surface/50";
}

export function panelTitleClass(tone: StatusTone) {
  if (tone === "success") return "text-[var(--color-success)]";
  if (tone === "danger") return "text-[var(--color-danger)]";
  if (tone === "warning") return "text-[var(--color-warning)]";
  return "text-default";
}
