import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, CircleDashed, Loader2, TerminalSquare, Wrench } from "lucide-react";
import type { ChatMessage } from "../../types";

interface PendingAssistantProps {
  message: ChatMessage;
}

interface ActivityEvent {
  event?: string;
  span_id?: string;
  span_type?: string;
  span_name?: string;
  status?: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  input_summary?: Record<string, unknown>;
  output_summary?: Record<string, unknown>;
  error_type?: string | null;
  error_message?: string | null;
}

interface ActivityRow {
  id: string;
  status: "running" | "success" | "failed" | "pending";
  title: string;
  detail: string;
  durationMs?: number | null;
  icon: "tool" | "model" | "default";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function metadataRecord(message: ChatMessage) {
  return isRecord(message.metadata_json) ? message.metadata_json : {};
}

function activityEvents(message: ChatMessage): ActivityEvent[] {
  const metadata = metadataRecord(message);
  if (!Array.isArray(metadata.activity_events)) return [];
  return metadata.activity_events.filter(isRecord) as ActivityEvent[];
}

function startedAt(message: ChatMessage) {
  const metadata = metadataRecord(message);
  const context = isRecord(metadata.progress_context) ? metadata.progress_context : {};
  return typeof context.started_at === "string" ? context.started_at : message.created_at;
}

function elapsedSeconds(started: string) {
  const startedMs = Date.parse(started);
  if (Number.isNaN(startedMs)) return 0;
  return Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function formatDuration(ms?: number | null) {
  if (!ms || ms < 1000) return "";
  return ` in ${formatElapsed(Math.round(ms / 1000))}`;
}

function textValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function compactKeys(value: unknown) {
  if (!isRecord(value)) return "";
  const keys = Object.keys(value).filter(key => value[key] !== undefined && value[key] !== null);
  if (keys.length === 0) return "";
  return keys.slice(0, 4).join(", ");
}

function toolDetail(input: Record<string, unknown> | undefined) {
  if (!input) return "Running a connected account tool.";
  const args = isRecord(input.arguments) ? input.arguments : {};
  const command = textValue(args.command);
  if (command) return command.length > 120 ? `${command.slice(0, 117)}...` : command;
  const keys = compactKeys(args);
  return keys ? `Arguments: ${keys}` : "Running a connected account tool.";
}

function resultDetail(output: Record<string, unknown> | undefined, status: string, durationMs?: number | null) {
  if (status === "failed") return "The operation returned an error.";
  const result = isRecord(output?.result) ? output.result : output;
  const count = typeof result?.count === "number" ? `${result.count} result${result.count === 1 ? "" : "s"}` : "";
  const keys = compactKeys(result);
  const summary = count || (keys ? `Returned: ${keys}` : "Completed.");
  return `${summary}${formatDuration(durationMs)}`;
}

function titleFor(event: ActivityEvent) {
  const name = event.span_name || "operation";
  const type = event.span_type || "operation";
  const status = event.status || "running";
  if (type === "context_build") return status === "running" ? "Preparing context" : "Context ready";
  if (type === "provider_call") return status === "running" ? `Calling ${name}` : "Model responded";
  if (type === "model_request") return status === "running" ? "Running assistant" : "Assistant finished";
  if (type === "tool_call") {
    if (status === "failed") return `${name} failed`;
    return status === "running" ? `Using ${name}` : `Ran ${name}`;
  }
  return status === "running" ? `Running ${name}` : `${name} complete`;
}

function detailFor(event: ActivityEvent) {
  if (event.status === "failed") return event.error_message || event.error_type || "The operation failed.";
  if (event.span_type === "tool_call") {
    return event.event === "span_finished"
      ? resultDetail(event.output_summary, event.status || "success", event.duration_ms)
      : toolDetail(event.input_summary);
  }
  if (event.span_type === "provider_call") {
    const input = event.input_summary || {};
    const request = isRecord(input.request) ? input.request : {};
    const toolCount = typeof input.tool_count === "number" ? input.tool_count : 0;
    const maxTokens = typeof request.max_tokens === "number" ? request.max_tokens : undefined;
    if (event.event === "span_finished") {
      const output = event.output_summary || {};
      const tokens = typeof output.total_tokens === "number" ? `${output.total_tokens} tokens` : "";
      return tokens ? `Received model output using ${tokens}${formatDuration(event.duration_ms)}.` : `Received model output${formatDuration(event.duration_ms)}.`;
    }
    return `${toolCount} tool${toolCount === 1 ? "" : "s"} available${maxTokens ? `, ${maxTokens} max output tokens` : ""}.`;
  }
  if (event.span_type === "context_build" && event.event === "span_finished") {
    const output = event.output_summary || {};
    const systems = Array.isArray(output.connected_systems) ? output.connected_systems.length : 0;
    const tools = typeof output.tool_count === "number" ? output.tool_count : 0;
    return `${systems} connected account${systems === 1 ? "" : "s"}, ${tools} tool${tools === 1 ? "" : "s"} selected.`;
  }
  return event.event === "span_finished" ? `Completed${formatDuration(event.duration_ms)}.` : "In progress.";
}

function rowsFromEvents(events: ActivityEvent[]): ActivityRow[] {
  const bySpan = new Map<string, ActivityEvent>();
  for (const event of events) {
    if (!event.span_id) continue;
    bySpan.set(event.span_id, { ...bySpan.get(event.span_id), ...event });
  }
  return Array.from(bySpan.values()).map((event, index) => ({
    id: event.span_id || `${event.span_name}-${index}`,
    status: event.status === "failed" ? "failed" : event.event === "span_finished" ? "success" : "running",
    title: titleFor(event),
    detail: detailFor(event),
    durationMs: event.duration_ms,
    icon: event.span_type === "tool_call" ? "tool" : event.span_type === "provider_call" ? "model" : "default",
  }));
}

function RowIcon({ row }: { row: ActivityRow }) {
  if (row.status === "success") return <CheckCircle2 className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.status === "failed") return <CircleDashed className="mt-0.5 w-3.5 h-3.5 text-danger shrink-0" />;
  if (row.icon === "tool") return <Wrench className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.icon === "model") return <TerminalSquare className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  return <Loader2 className="mt-0.5 w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  const start = useMemo(() => startedAt(message), [message]);
  const [elapsed, setElapsed] = useState(() => elapsedSeconds(start));
  const rows = useMemo(() => rowsFromEvents(activityEvents(message)), [message]);
  const current = [...rows].reverse().find(row => row.status === "running");
  const visibleRows = rows.length > 0 ? rows : [{
    id: "starting",
    status: "running" as const,
    title: "Starting",
    detail: "Opening the assistant run.",
    icon: "default" as const,
  }];

  useEffect(() => {
    const interval = setInterval(() => setElapsed(elapsedSeconds(start)), 1000);
    return () => clearInterval(interval);
  }, [start]);

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-2xl min-w-0 py-1">
        <div className="flex items-center gap-2 text-sm">
          <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
          <span className="font-semibold text-default">Working for {formatElapsed(elapsed)}</span>
        </div>

        <p className="mt-1 text-sm text-muted leading-relaxed">
          {current ? current.detail : visibleRows[visibleRows.length - 1]?.detail}
        </p>

        <div className="mt-3 border-l border-default pl-3 space-y-2">
          {visibleRows.map(row => (
            <div
              key={row.id}
              className={`flex items-start gap-2 text-xs ${row.status === "running" ? "text-default" : "text-muted"}`}
            >
              <RowIcon row={row} />
              <div className="min-w-0">
                <div className="font-semibold leading-snug">{row.title}</div>
                <div className="text-[11px] text-muted leading-snug break-words">{row.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
