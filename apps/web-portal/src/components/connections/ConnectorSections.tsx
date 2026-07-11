import { AlertTriangle, CheckCircle2, Plug } from "lucide-react";
import { formatStatusLabel, getStatusTone, type StatusTone } from "./connectionShared";

function statusBadgeClass(tone: StatusTone) {
  if (tone === "success") return "text-[var(--color-success)] bg-[var(--color-success)]/10";
  if (tone === "danger") return "text-[var(--color-danger)] bg-[var(--color-danger)]/10";
  if (tone === "warning") return "text-[var(--color-warning)] bg-[var(--color-warning)]/10";
  return "text-muted bg-surface";
}

export function StatusBadge({ status, fallback, hasError }: { status?: string; fallback: string; hasError?: boolean }) {
  const tone = getStatusTone(status, hasError);
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${statusBadgeClass(tone)}`}>
      {tone === "success" ? <CheckCircle2 className="h-3 w-3" /> : null}
      {tone === "danger" ? <AlertTriangle className="h-3 w-3" /> : null}
      {status ? formatStatusLabel(status) : fallback}
    </span>
  );
}

export function ConnectorLogo({ className = "h-5 w-5" }: { connectorKey?: string; className?: string }) {
  return <Plug className={className} aria-hidden="true" />;
}
