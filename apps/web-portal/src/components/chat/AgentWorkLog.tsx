import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cloud,
  Database,
  FileText,
  GitBranch,
  Loader2,
  Search,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { ChatMessage } from "../../types";

interface AgentWorkLogProps {
  message: ChatMessage;
  variant: "live" | "completed";
}

interface ActivityEvent {
  event?: string;
  span_type?: string;
  span_name?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  input_summary?: Record<string, unknown>;
  output_summary?: Record<string, unknown>;
}

interface StreamWorkItem {
  kind?: string;
  text?: string;
  provider?: string;
  model?: string;
  event?: unknown;
}

interface WorkStep {
  icon: LucideIcon;
  title: string;
  detail?: string;
  status: "running" | "success" | "warning" | "failed";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function metadataRecord(message: ChatMessage) {
  return isRecord(message.metadata_json) ? message.metadata_json : {};
}

function textValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function activityEvents(message: ChatMessage): ActivityEvent[] {
  const metadata = metadataRecord(message);
  if (!Array.isArray(metadata.activity_events)) return [];
  return metadata.activity_events.filter(isRecord) as ActivityEvent[];
}

function streamWorkItems(message: ChatMessage): StreamWorkItem[] {
  const metadata = metadataRecord(message);
  if (!Array.isArray(metadata.stream_work_items)) return [];
  return metadata.stream_work_items.filter(isRecord) as StreamWorkItem[];
}

function streamSource(message: ChatMessage, events: ActivityEvent[]) {
  const metadata = metadataRecord(message);
  const provider = typeof metadata.stream_provider === "string" ? metadata.stream_provider.trim() : "";
  const model = typeof metadata.stream_model === "string" ? metadata.stream_model.trim() : "";
  if (provider || model) return [provider, model].filter(Boolean).join(" · ");
  return [...events].reverse().find(event => event.span_type === "provider_call")?.span_name || "";
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function eventTimeMs(event: ActivityEvent, key: "started_at" | "ended_at") {
  const value = event[key];
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function fallbackStartMs(message: ChatMessage) {
  const metadata = metadataRecord(message);
  const context = isRecord(metadata.progress_context) ? metadata.progress_context : {};
  const start = typeof context.started_at === "string" ? context.started_at : message.created_at;
  const parsed = Date.parse(start);
  return Number.isNaN(parsed) ? null : parsed;
}

function elapsedFromEvents(message: ChatMessage, events: ActivityEvent[], live: boolean, now: number) {
  const started = events.map(event => eventTimeMs(event, "started_at")).filter((value): value is number => value !== null);
  const start = started.length ? Math.min(...started) : fallbackStartMs(message);
  if (start === null) return 0;

  if (live) return Math.max(0, Math.floor((now - start) / 1000));

  const ended = events.map(event => eventTimeMs(event, "ended_at")).filter((value): value is number => value !== null);
  const end = ended.length ? Math.max(...ended) : now;
  return Math.max(0, Math.round((end - start) / 1000));
}

function isTableLikeLine(line: string) {
  const trimmed = line.trim();
  if (!trimmed) return true;
  if ((trimmed.match(/\|/g) || []).length >= 2) return true;
  if ([...trimmed].every(char => char === "|" || char === "-" || char === ":" || /\s/.test(char))) return true;
  if (/^(so|po|inv|cn|dn)-?\d/i.test(trimmed) && trimmed.split(/\s+/).length > 4) return true;
  const digitCount = (trimmed.match(/\d/g) || []).length;
  return trimmed.length > 80 && digitCount / trimmed.length > 0.3;
}

function collapseRepeatedWords(text: string) {
  let cleaned = text;
  for (let i = 0; i < 3; i += 1) {
    cleaned = cleaned.replace(/\b([A-Za-z][A-Za-z0-9'/-]{2,})\1(?=\s|$|[.,;:!?])/gi, "$1");
    cleaned = cleaned.replace(/\b([A-Za-z0-9][A-Za-z0-9'/-]{2,})\b(?:\s+\1\b)+/gi, "$1");
  }
  return cleaned;
}

function usefulText(text: string) {
  const usefulLines = text
    .replace(/\r/g, "\n")
    .split("\n")
    .map(line => line.trim())
    .filter(line => line && !isTableLikeLine(line));
  const joined = collapseRepeatedWords(usefulLines.join(" ").replace(/\s+/g, " ").trim());
  if (!joined) return "";
  const sentences = joined.split(/(?<=[.!?])\s+/).filter(Boolean);
  const excerpt = (sentences.length > 2 ? sentences.slice(-2).join(" ") : joined).trim();
  return excerpt.length > 240 ? `${excerpt.slice(0, 237).trim()}...` : excerpt;
}

function connectorLabel(value: string) {
  const normalized = value.replace(/^ms_/, "microsoft_").replace(/_/g, " ").trim().toLowerCase();
  if (!normalized) return "Tool";
  if (normalized.includes("odoo")) return "Odoo";
  if (normalized.includes("github")) return "GitHub";
  if (normalized.includes("azure")) return "Azure";
  if (normalized.includes("graph")) return "Microsoft Graph";
  if (normalized.includes("exchange")) return "Exchange";
  if (normalized.includes("teams")) return "Teams";
  if (normalized.includes("sharepoint")) return "SharePoint";
  if (normalized.includes("document")) return "Documents";
  return normalized.replace(/\b\w/g, char => char.toUpperCase());
}

function iconForConnector(label: string): LucideIcon {
  const normalized = label.toLowerCase();
  if (normalized.includes("odoo")) return Database;
  if (normalized.includes("github")) return GitBranch;
  if (normalized.includes("azure") || normalized.includes("microsoft")) return Cloud;
  if (normalized.includes("document") || normalized.includes("file")) return FileText;
  return Wrench;
}

function resultDetail(output: Record<string, unknown>) {
  const result = isRecord(output.result) ? output.result : {};
  const count = textValue(result.count);
  const status = textValue(result.status);
  const message = textValue(result.message);
  if (count) return `Returned ${count} item${count === "1" ? "" : "s"}.`;
  if (message) return message.length > 120 ? `${message.slice(0, 117).trim()}...` : message;
  if (status) return `Finished with status ${status}.`;
  return "";
}

function argumentDetail(input: Record<string, unknown>) {
  const action = textValue(input.action);
  const args = isRecord(input.arguments) ? input.arguments : {};
  const mode = textValue(args.mode);
  const model = textValue(args.model);
  const query = textValue(args.query || args.path || args.command);
  return [action, mode && `mode ${mode}`, model && `on ${model}`, query && query.slice(0, 80)].filter(Boolean).join(" · ");
}

function stepFromActivity(event: ActivityEvent): WorkStep | null {
  const input = event.input_summary || {};
  const output = event.output_summary || {};
  const status = event.status === "failed" ? "failed" : event.status === "warning" ? "warning" : event.event === "span_finished" ? "success" : "running";

  if (event.span_type === "context_build") {
    if (event.event === "span_finished") {
      const toolCount = textValue(output.tool_count);
      const memories = textValue(output.memories_injected);
      const detail = [toolCount && `${toolCount} tools available`, memories && `${memories} memories used`].filter(Boolean).join(" · ");
      return { icon: CheckCircle2, title: "Context ready", detail, status };
    }
    return { icon: Search, title: "Preparing context", detail: "Checking chat history, memory, files, and connected accounts.", status };
  }

  if (event.span_type === "provider_call") {
    if (event.event === "span_finished") {
      const latency = textValue(output.latency_ms);
      return { icon: CheckCircle2, title: "Model pass complete", detail: latency ? `${latency}ms` : "", status };
    }
    return { icon: Brain, title: `Thinking with ${event.span_name || "the selected model"}`, detail: "Waiting for the model response.", status };
  }

  if (event.span_type === "tool_call") {
    const toolName = textValue(input.tool_name || event.span_name);
    const label = connectorLabel(textValue(input.connector) || toolName);
    if (event.event === "span_finished") {
      return { icon: status === "failed" ? AlertTriangle : CheckCircle2, title: `${label} finished`, detail: resultDetail(output), status };
    }
    return { icon: iconForConnector(label), title: `Using ${label}`, detail: argumentDetail(input), status };
  }

  return null;
}

function stepFromStreamItem(item: StreamWorkItem, index: number, total: number, live: boolean): WorkStep | null {
  if (item.kind === "activity" && isRecord(item.event)) {
    return stepFromActivity(item.event as ActivityEvent);
  }
  if (item.kind === "thinking") {
    const source = [item.provider, item.model].filter(Boolean).join(" · ");
    const detail = usefulText(item.text || "");
    if (!detail) return null;
    return {
      icon: Brain,
      title: source ? `Thinking with ${source}` : "Thinking",
      detail,
      status: live && index === total - 1 ? "running" : "success",
    };
  }
  if (item.kind === "note") {
    const detail = usefulText(item.text || "");
    if (!detail) return null;
    return {
      icon: Brain,
      title: "Progress note",
      detail,
      status: live && index === total - 1 ? "running" : "success",
    };
  }
  return null;
}

function activitySteps(events: ActivityEvent[]) {
  return events.map(stepFromActivity).filter((step): step is WorkStep => Boolean(step));
}

function workSteps(message: ChatMessage, events: ActivityEvent[], variant: AgentWorkLogProps["variant"]) {
  const streamItems = streamWorkItems(message);
  const live = variant === "live";
  const steps = streamItems.length
    ? streamItems.map((item, index) => stepFromStreamItem(item, index, streamItems.length, live)).filter((step): step is WorkStep => Boolean(step))
    : activitySteps(events);
  return (live ? steps.slice(-14) : steps).slice(-30);
}

function statusClass(status: WorkStep["status"]) {
  if (status === "failed") return "text-[var(--color-danger)]";
  if (status === "warning") return "text-[var(--color-warning)]";
  if (status === "success") return "text-soft";
  return "text-accent";
}

export function AgentWorkLog({ message, variant }: AgentWorkLogProps) {
  const events = useMemo(() => activityEvents(message), [message]);
  const steps = useMemo(() => workSteps(message, events, variant), [message, events, variant]);
  const source = useMemo(() => streamSource(message, events), [message, events]);
  const live = variant === "live";
  const [now, setNow] = useState(() => Date.now());
  const elapsed = useMemo(() => elapsedFromEvents(message, events, live, now), [events, live, message, now]);
  const [expanded, setExpanded] = useState(variant === "live");

  useEffect(() => {
    if (!live) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [live]);

  if (variant === "completed" && steps.length === 0) return null;

  const body = (
    <div className="mt-2 space-y-1.5">
      {steps.map((step, index) => {
        const Icon = step.icon;
        return (
          <div key={`${step.title}-${index}`} className="flex gap-2 text-sm">
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${statusClass(step.status)} ${step.status === "running" ? "animate-pulse" : ""}`} />
            <div className="min-w-0">
              <div className="font-medium text-default">{step.title}</div>
              {step.detail && <div className="text-xs leading-relaxed text-muted">{step.detail}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );

  if (variant === "completed") {
    return (
      <div className="mb-3 text-sm">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold text-muted transition-all hover-bg-surface hover-text-default"
          onClick={() => setExpanded(value => !value)}
        >
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          Worked for {formatElapsed(elapsed)}
        </button>
        {expanded && body}
      </div>
    );
  }

  return (
    <div className="w-full flex justify-start">
      <div className="w-full max-w-2xl min-w-0 py-1 text-sm">
        <div className="flex items-center gap-2 text-xs text-soft">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
          <span className="font-semibold text-muted">{source || "Working"}</span>
          <span>{formatElapsed(elapsed)}</span>
        </div>
        {body}
      </div>
    </div>
  );
}
