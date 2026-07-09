import type { FormEvent, ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Trash2,
} from "lucide-react";
import { Button } from "../ui/Button";
import { TextField } from "../ui/TextField";
import {
  formatDateTime,
  formatStatusLabel,
  getStatusTone,
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

function DetailCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-default bg-canvas p-4">
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

export function ConnectorLogo({ connectorKey, className = "w-5 h-5" }: { connectorKey: string; className?: string }) {
  if (connectorKey !== "odoo") return <div className={`${className} rounded-full bg-muted`} />;

  return (
    <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Odoo logo">
      <path
        fill="#714B67"
        d="M21.1002 15.7957c-1.6015 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2979 2.8998-2.8998 2.8998zm0-1.2c.9388.0006 1.7003-.7601 1.7008-1.6989.0004-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zm-6.0655 1.2c-1.6014 0-2.8997-1.2983-2.8997-2.8998s1.2983-2.8997 2.8997-2.8997c1.6015 0 2.8998 1.2982 2.8998 2.8997 0 1.5999-1.2999 2.8998-2.8998 2.8998zm0-1.2c.9389.0006 1.7003-.7601 1.7008-1.6989.0005-.9388-.7602-1.7003-1.699-1.7007h-.0018c-.9388.0004-1.6994.7619-1.699 1.7007.0005.9381.761 1.6985 1.699 1.699zM11.865 12.858c0 1.6199-1.2979 2.9378-2.8977 2.9378s-2.8998-1.314-2.8998-2.9358 1.1799-2.8597 2.8998-2.8597c.6359 0 1.2239.134 1.6998.484v-1.68a.6.6 0 0 1 1.2 0v4.0537h-.002zm-2.8977 1.7399c.9388.0005 1.7002-.7602 1.7007-1.699.0005-.9388-.7602-1.7003-1.699-1.7007h-.0017c-.9389.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699zm-6.0675 1.1979C1.2983 15.7957 0 14.4974 0 12.8959s1.2983-2.8997 2.8998-2.8997 2.8997 1.2982 2.8997 2.8997c0 1.5999-1.2999 2.8998-2.8997 2.8998zm0-1.2c.9388.0006 1.7002-.7601 1.7007-1.699.0005-.9387-.7602-1.7002-1.699-1.7006h-.0017c-.9388.0004-1.6995.7619-1.699 1.7007.0004.9381.7608 1.6985 1.699 1.699z"
      />
    </svg>
  );
}

export function OdooConnectorSection({
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
    <div className="space-y-5">
      {!isOdooStatusLoaded ? (
        <DetailCard>
          <p className="text-sm text-muted">Loading connection details...</p>
        </DetailCard>
      ) : !isOdooDisconnected ? (
        <InfoGrid
          rows={[
            { label: "Status", value: formatStatusLabel(odooStatus.status) },
            { label: "Instance URL", value: <span className="break-all">{odooStatus.odoo_url || "-"}</span> },
            { label: "Database", value: odooStatus.odoo_db || "-" },
            { label: "Username", value: odooStatus.provider_username || "-" },
            { label: "Environment", value: odooStatus.target_environment || "-" },
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
          <Button size="sm" variant="danger" onClick={onDisconnect}>
            <Trash2 className="w-3.5 h-3.5" /> Disconnect
          </Button>
        </ActionGroup>
      ) : null}

      {isOdooStatusLoaded && isOdooDisconnected ? (
        <form onSubmit={onConnect} className="space-y-4">
          <div className="grid gap-3">
            <FormField label="Instance URL">
              <TextField type="url" required placeholder="https://your-odoo-instance.com" value={odooUrl} onChange={e => onOdooUrlChange(e.target.value)} />
            </FormField>
            <FormField label="Database">
              <TextField type="text" required placeholder="Database name" value={odooDb} onChange={e => onOdooDbChange(e.target.value)} />
            </FormField>
            <FormField label="Username">
              <TextField type="email" required placeholder="user@example.com" value={odooUsername} onChange={e => onOdooUsernameChange(e.target.value)} />
            </FormField>
            <FormField label="API Key">
              <TextField type="password" required placeholder="Odoo API key" value={odooApiKey} onChange={e => onOdooApiKeyChange(e.target.value)} />
            </FormField>
          </div>
          <ActionGroup>
            <Button type="submit" disabled={isConnecting}>
              {isConnecting ? "Connecting..." : "Verify & Save"}
            </Button>
          </ActionGroup>
        </form>
      ) : null}
    </div>
  );
}
