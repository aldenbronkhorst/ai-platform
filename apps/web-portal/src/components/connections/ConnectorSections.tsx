import type { FormEvent, ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Trash2,
} from "lucide-react";
import { GlassButton } from "../ui/GlassButton";
import { GlassInput } from "../ui/GlassInput";
import {
  MICROSOFT_NATIVE_CONNECTOR_KEY_SET,
  formatDateTime,
  formatOptionalStatus,
  formatStatusLabel,
  getStatusTone,
  type ConnectorDef,
  type ConnectorMeta,
  type MicrosoftNativeDeviceCode,
  type OdooStatus,
  type StatusTone,
} from "./connectionShared";

function statusBadgeClass(tone: StatusTone) {
  switch (tone) {
    case "success":
      return "text-[var(--color-success)] bg-[var(--color-success)]/10";
    case "danger":
      return "text-[var(--color-danger)] bg-[var(--color-danger)]/10";
    case "warning":
      return "text-[var(--color-warning)] bg-[var(--color-warning)]/10";
    default:
      return "text-muted bg-surface";
  }
}

export function StatusBadge({
  status,
  fallback,
  hasError,
}: {
  status?: string;
  fallback: string;
  hasError?: boolean;
}) {
  const tone = getStatusTone(status, hasError);
  const label = status ? formatStatusLabel(status) : fallback;

  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1 rounded-full ${statusBadgeClass(tone)}`}>
      {tone === "success" ? <CheckCircle2 className="w-3 h-3" /> : null}
      {tone === "danger" ? <AlertTriangle className="w-3 h-3" /> : null}
      {label}
    </span>
  );
}

function ActionGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {children}
    </div>
  );
}

function FormField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[11px] font-bold uppercase tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}

export function DetailCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-default bg-canvas p-4">
      {children}
    </div>
  );
}

function InfoGrid({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <DetailCard>
      <dl className="grid grid-cols-[128px_1fr] gap-x-4 gap-y-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted">{row.label}</dt>
            <dd className="text-default min-w-0 break-words">{row.value}</dd>
          </div>
        ))}
      </dl>
    </DetailCard>
  );
}

function isMicrosoftNativeConnector(key: string) {
  return MICROSOFT_NATIVE_CONNECTOR_KEY_SET.has(key);
}

export function ConnectorLogo({ connectorKey, className = "w-5 h-5" }: { connectorKey: string; className?: string }) {
  if (connectorKey === "odoo") {
    return (
      <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Odoo logo">
        <path
          fill="#714B67"
          d="M21.1002 15.7957c-1.6015 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2979 2.8998-2.8998 2.8998zm0-1.2c.9388.0006 1.7003-.7601 1.7008-1.6989.0004-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zm-6.0655 1.2c-1.6014 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2999 2.8998-2.8998 2.8998zm0-1.2c.9389.0006 1.7003-.7601 1.7008-1.6989.0005-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zM11.865 12.858c0 1.6199-1.2979 2.9378-2.8977 2.9378s-2.8998-1.314-2.8998-2.9358 1.1799-2.8597 2.8998-2.8597c.6359 0 1.2239.134 1.6998.484v-1.68a.6.6 0 0 1 1.2 0v4.0537h-.002zm-2.8977 1.7399c.9388.0005 1.7002-.7602 1.7007-1.699.0005-.9388-.7602-1.7003-1.699-1.7007h-.0017c-.9389.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699zm-6.0675 1.1979C1.2983 15.7957 0 14.4974 0 12.8959s1.2983-2.8997 2.8998-2.8997 2.8997 1.2982 2.8997 2.8997c0 1.5999-1.2999 2.8998-2.8997 2.8998zm0-1.2c.9388.0006 1.7002-.7601 1.7007-1.699.0005-.9387-.7602-1.7002-1.699-1.7006h-.0017c-.9388.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699z"
        />
      </svg>
    );
  }

  if (isMicrosoftNativeConnector(connectorKey)) {
    return (
      <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Microsoft connector logo">
        <rect x="2.5" y="2.5" width="8.5" height="8.5" rx="1" fill="#F25022" />
        <rect x="13" y="2.5" width="8.5" height="8.5" rx="1" fill="#7FBA00" />
        <rect x="2.5" y="13" width="8.5" height="8.5" rx="1" fill="#00A4EF" />
        <rect x="13" y="13" width="8.5" height="8.5" rx="1" fill="#FFB900" />
      </svg>
    );
  }

  if (connectorKey === "github") {
    return (
      <svg className={`${className} text-default`} viewBox="0 0 24 24" role="img" aria-label="GitHub logo">
        <path
          fill="currentColor"
          d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
        />
      </svg>
    );
  }

  return <div className={`${className} rounded-full bg-muted`} />;
}

function ConnectorDetailShell({
  connector,
  status,
  fallback,
  hasStatusError,
  children,
}: {
  connector: ConnectorDef;
  status?: string;
  fallback: string;
  hasStatusError?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="p-2.5 rounded-xl bg-canvas border border-default shrink-0">
          <ConnectorLogo connectorKey={connector.key} />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-bold text-base text-default">{connector.name}</h3>
          <p className="text-xs text-muted mt-0.5">{connector.subtitle}</p>
        </div>
        <StatusBadge status={status} fallback={fallback} hasError={hasStatusError} />
      </div>
      {children}
    </div>
  );
}

export function OdooConnectorSection({
  connector,
  status,
  statusFallback,
  hasStatusError,
  odooStatus,
  odooUrl,
  odooDb,
  odooUsername,
  odooApiKey,
  isConnecting,
  onConnect,
  onDisconnect,
  onOdooUrlChange,
  onOdooDbChange,
  onOdooUsernameChange,
  onOdooApiKeyChange,
}: {
  connector: ConnectorDef;
  status?: string;
  statusFallback: string;
  hasStatusError?: boolean;
  odooStatus: OdooStatus | null;
  odooUrl: string;
  odooDb: string;
  odooUsername: string;
  odooApiKey: string;
  isConnecting: boolean;
  onConnect: (event: FormEvent) => void;
  onDisconnect: () => void;
  onOdooUrlChange: (value: string) => void;
  onOdooDbChange: (value: string) => void;
  onOdooUsernameChange: (value: string) => void;
  onOdooApiKeyChange: (value: string) => void;
}) {
  const isOdooStatusLoaded = odooStatus !== null;
  const isOdooDisconnected = odooStatus?.status === "not_connected";

  return (
    <ConnectorDetailShell connector={connector} status={status || odooStatus?.status} fallback={statusFallback} hasStatusError={hasStatusError}>
      {!isOdooStatusLoaded ? (
        <DetailCard>
          <p className="text-sm text-muted">Loading connection details...</p>
        </DetailCard>
      ) : !isOdooDisconnected ? (
        <InfoGrid
          rows={[
            { label: "Status", value: formatStatusLabel(odooStatus.status) },
            { label: "Instance URL", value: <span className="break-all">{odooStatus.odoo_url || "—"}</span> },
            { label: "Database", value: odooStatus.odoo_db || "—" },
            { label: "Username", value: odooStatus.provider_username || "—" },
            { label: "Environment", value: odooStatus.target_environment || "—" },
            { label: "Last Verified", value: formatDateTime(odooStatus.last_verified_at) },
          ]}
        />
      ) : (
        <DetailCard>
          <p className="text-sm text-muted">Not connected.</p>
        </DetailCard>
      )}

      {isOdooStatusLoaded && !isOdooDisconnected ? (
        <ActionGroup>
          <GlassButton size="sm" variant="danger" onClick={onDisconnect}>
            <Trash2 className="w-3.5 h-3.5" /> Disconnect
          </GlassButton>
        </ActionGroup>
      ) : null}

      {isOdooStatusLoaded && isOdooDisconnected && (
        <form onSubmit={onConnect} className="space-y-4">
          <div className="grid gap-3">
            <FormField label="Instance URL">
              <GlassInput type="url" required placeholder="https://your-odoo-instance.com" value={odooUrl} onChange={e => onOdooUrlChange(e.target.value)} />
            </FormField>
            <FormField label="Database">
              <GlassInput type="text" required placeholder="Database name" value={odooDb} onChange={e => onOdooDbChange(e.target.value)} />
            </FormField>
            <FormField label="Username">
              <GlassInput type="email" required placeholder="user@example.com" value={odooUsername} onChange={e => onOdooUsernameChange(e.target.value)} />
            </FormField>
            <FormField label="API Key">
              <GlassInput type="password" required placeholder="Odoo API key" value={odooApiKey} onChange={e => onOdooApiKeyChange(e.target.value)} />
            </FormField>
          </div>
          <ActionGroup>
            <GlassButton type="submit" disabled={isConnecting}>
              {isConnecting ? "Connecting..." : "Verify & Save"}
            </GlassButton>
          </ActionGroup>
        </form>
      )}
    </ConnectorDetailShell>
  );
}

export function MicrosoftNativeConnectorSection({
  connector,
  meta,
  status,
  statusFallback,
  hasStatusError,
  isStarting,
  isPolling,
  activeDeviceCode,
  onConnect,
  onDisconnect,
  onOpenDeviceLogin,
}: {
  connector: ConnectorDef;
  meta?: ConnectorMeta;
  status?: string;
  statusFallback: string;
  hasStatusError?: boolean;
  isStarting: boolean;
  isPolling: boolean;
  activeDeviceCode: (MicrosoftNativeDeviceCode & { connectorKey: string }) | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onOpenDeviceLogin: (verificationUrl?: string) => void;
}) {
  const readinessStatus = meta?.state?.readiness_status
    || meta?.metadata?.overall_status
    || status;
  const tooling = meta?.metadata?.tooling || [];
  const authAppName = meta?.metadata?.auth_app_name;

  return (
    <ConnectorDetailShell connector={connector} status={readinessStatus} fallback={statusFallback} hasStatusError={hasStatusError}>
      <DetailCard>
        <div className="space-y-2 text-sm text-muted">
          <p>
            Separate native Microsoft connector. This stores only the {connector.name} token; access is still limited by the signed-in user's Microsoft roles, Azure RBAC, workload permissions, and tenant consent.
          </p>
          {authAppName ? <p className="text-xs">Microsoft sign-in app: {authAppName}</p> : null}
          {tooling.length > 0 ? <p className="text-xs">Tools: {tooling.join(", ")}</p> : null}
        </div>
      </DetailCard>

      <InfoGrid
        rows={[
          { label: "Account", value: formatOptionalStatus(meta?.state?.account_status || status) },
          { label: "Readiness", value: formatOptionalStatus(readinessStatus) },
          { label: "Token", value: formatOptionalStatus(meta?.state?.token_status) },
          { label: "User", value: meta?.metadata?.provider_username || "—" },
          { label: "Last Verified", value: formatDateTime(meta?.last_verified_at) },
        ]}
      />

      <ActionGroup>
        <GlassButton size="sm" onClick={onConnect} disabled={isStarting || isPolling}>
          {isStarting ? "Starting sign-in..." : isPolling ? "Waiting for authentication..." : status === "connected" ? "Refresh Sign-In" : `Connect ${connector.name}`}
        </GlassButton>
        <GlassButton size="sm" variant="danger" onClick={onDisconnect}>
          <Trash2 className="w-3.5 h-3.5" /> Disconnect
        </GlassButton>
      </ActionGroup>

      {activeDeviceCode && (
        <DetailCard>
          <div className="text-sm space-y-3">
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted">{connector.name} sign-in</p>
              <p className="font-semibold text-default">Device Code: <span className="font-mono text-lg">{activeDeviceCode.user_code}</span></p>
              <p className="text-muted text-xs">
                {activeDeviceCode.scope_label || connector.name} via {activeDeviceCode.auth_app_name || "Microsoft"}.
              </p>
              <p className="text-muted text-xs">
                Open <a href={activeDeviceCode.verification_url} target="_blank" rel="noopener noreferrer" className="underline">{activeDeviceCode.verification_url}</a> and enter the code above.
              </p>
            </div>
            <ActionGroup>
              <GlassButton size="sm" onClick={() => onOpenDeviceLogin(activeDeviceCode.verification_url)}>
                Open Microsoft Sign-In
              </GlassButton>
            </ActionGroup>
          </div>
        </DetailCard>
      )}
    </ConnectorDetailShell>
  );
}

export function GitHubConnectorSection({
  connector,
  meta,
  status,
  statusFallback,
  hasStatusError,
  onConnect,
}: {
  connector: ConnectorDef;
  meta?: ConnectorMeta;
  status?: string;
  statusFallback: string;
  hasStatusError?: boolean;
  onConnect: () => void;
}) {
  return (
    <ConnectorDetailShell connector={connector} status={status} fallback={statusFallback} hasStatusError={hasStatusError}>
      <DetailCard>
        <p className="text-sm text-muted">Connect with GitHub OAuth.</p>
      </DetailCard>

      <InfoGrid
        rows={[
          { label: "Account", value: formatOptionalStatus(meta?.state?.account_status || status) },
          { label: "Token", value: formatOptionalStatus(meta?.state?.token_status) },
          { label: "User", value: meta?.metadata?.provider_username || "—" },
          { label: "Last Verified", value: formatDateTime(meta?.last_verified_at) },
        ]}
      />

      <ActionGroup>
        <GlassButton size="sm" onClick={onConnect}>
          <GitBranch className="w-4 h-4" /> Connect with GitHub
        </GlassButton>
      </ActionGroup>
    </ConnectorDetailShell>
  );
}
