export interface ConnectorDef {
  key: string;
  name: string;
  subtitle: string;
}

export interface ConnectorMeta {
  connector_key?: string;
  display_name?: string;
  subtitle?: string;
  status?: string;
  auth_method?: string;
  last_verified_at?: string | null;
  state?: {
    configured?: boolean;
    account_status?: string;
    token_status?: string;
    readiness_status?: string;
    source?: string;
  };
  metadata?: {
    provider_username?: string | null;
    permission_summary?: string | null;
    overall_status?: string | null;
    odoo_url?: string | null;
    odoo_db?: string | null;
  };
}

export interface OdooStatus {
  status: string;
  odoo_url?: string;
  odoo_db?: string;
  provider_username?: string;
  target_environment?: string;
  last_verified_at?: string;
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
  if (hasError) return "danger";
  if (status === "connected" || status === "active" || status === "authorized" || status === "available" || status === "ready") return "success";
  if (status === "error" || status === "failed") return "danger";
  if (
    status === "partial"
    || status === "limited"
    || status === "warning"
    || status === "read_only"
    || status === "needs_token"
    || status === "needs_setup"
    || status === "setup_required"
    || status === "not_connected"
    || status === "expired"
    || status === "missing"
    || status === "missing_consent"
    || status === "missing_permission"
    || status === "not_checked"
  ) return "warning";
  return "neutral";
}

export function panelToneClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "border-[var(--color-success)]/25 bg-[var(--color-success)]/5";
    case "danger":
      return "border-[var(--color-danger)]/25 bg-[var(--color-danger)]/5";
    case "warning":
      return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/5";
    default:
      return "border-default bg-surface/50";
  }
}

export function panelTitleClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "text-[var(--color-success)]";
    case "danger":
      return "text-[var(--color-danger)]";
    case "warning":
      return "text-[var(--color-warning)]";
    default:
      return "text-default";
  }
}
