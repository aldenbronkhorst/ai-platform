import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, CircleDashed, Loader2, Search, TerminalSquare, Wrench } from "lucide-react";
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
  meta?: string;
  durationMs?: number | null;
  icon: "context" | "tool" | "model" | "default";
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

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function compactKeys(value: unknown, limit = 4) {
  if (!isRecord(value)) return "";
  const keys = Object.keys(value).filter(key => value[key] !== undefined && value[key] !== null);
  if (keys.length === 0) return "";
  return keys.slice(0, limit).join(", ");
}

function truncateText(value: string, limit = 120) {
  const clean = value.trim().replace(/\s+/g, " ");
  return clean.length > limit ? `${clean.slice(0, limit - 1).trim()}...` : clean;
}

function displayToolName(value: string) {
  if (value === "odoo_ops_runner") return "Odoo";
  if (value === "azure_cli") return "Azure CLI";
  if (value === "github_cli") return "GitHub CLI";
  return value.replace(/_/g, " ");
}

function displaySystemName(value: string) {
  if (value === "odoo") return "Odoo";
  if (value === "azure") return "Azure";
  if (value === "github") return "GitHub";
  return value.replace(/_/g, " ");
}

function plural(count: number, singular: string, pluralWord = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function runningToolTitle(action: string) {
  if (action.startsWith("Query ")) return `Querying ${action.slice(6)}`;
  if (action.startsWith("Run ")) return `Running ${action.slice(4)}`;
  if (action.startsWith("Create ")) return `Creating ${action.slice(7)}`;
  if (action.startsWith("Write ")) return `Updating ${action.slice(6)}`;
  if (action.startsWith("Delete ")) return `Deleting ${action.slice(7)}`;
  if (action.includes(" CLI: ")) return `Running ${action}`;
  return action;
}

function completedToolTitle(action: string) {
  if (action.startsWith("Query ")) return `Queried ${action.slice(6)}`;
  if (action.startsWith("Run ")) return `Ran ${action.slice(4)}`;
  if (action.startsWith("Create ")) return `Created ${action.slice(7)}`;
  if (action.startsWith("Write ")) return `Updated ${action.slice(6)}`;
  if (action.startsWith("Delete ")) return `Deleted ${action.slice(7)}`;
  return `Ran ${action}`;
}

function modelNameFromEvent(event: ActivityEvent) {
  const input = event.input_summary || {};
  const model = isRecord(input.model) ? input.model : {};
  return textValue(model.display_name) || event.span_name || "model";
}

function toolDetail(input: Record<string, unknown> | undefined) {
  if (!input) return "Running a connected account tool.";
  const action = textValue(input.action);
  const args = isRecord(input.arguments) ? input.arguments : {};
  const command = textValue(args.command);
  if (command) return truncateText(command);
  const mode = textValue(args.mode);
  const model = textValue(args.model);
  const fields = Array.isArray(args.fields) ? args.fields.slice(0, 5).join(", ") : "";
  const limit = textValue(args.limit);
  const parts = [
    mode && `mode: ${mode}`,
    model && `model: ${model}`,
    fields && `fields: ${fields}`,
    limit && `limit: ${limit}`,
  ].filter(Boolean);
  if (parts.length > 0) return parts.join(" · ");
  const keys = compactKeys(args, 6);
  return keys ? `Arguments: ${keys}` : action || "Running a connected account tool.";
}

function resultDetail(output: Record<string, unknown> | undefined, status: string, durationMs?: number | null) {
  if (status === "failed") return "The operation returned an error.";
  const result = isRecord(output?.result) ? output.result : output;
  const countValue = isRecord(result) ? numberValue(result.count) : null;
  const count = countValue !== null ? `${countValue} result${countValue === 1 ? "" : "s"}` : "";
  const keys = compactKeys(result);
  const summary = count || (keys ? `Returned: ${keys}` : "Completed.");
  return `${summary}${formatDuration(durationMs)}`;
}

function titleFor(event: ActivityEvent) {
  const input = event.input_summary || {};
  const output = event.output_summary || {};
  const name = event.span_name || "operation";
  const type = event.span_type || "operation";
  const status = event.status || "running";
  if (type === "context_build") return status === "running" ? "Gathering context" : "Context ready";
  if (type === "provider_call") {
    const modelName = modelNameFromEvent(event);
    const toolCalls = numberValue(output.tool_call_count);
    if (status === "running") return `Asking ${modelName}`;
    if (toolCalls && toolCalls > 0) return `${modelName} requested ${toolCalls} tool${toolCalls === 1 ? "" : "s"}`;
    return `${modelName} responded`;
  }
  if (type === "model_request") return status === "running" ? "Running assistant turn" : "Assistant turn complete";
  if (type === "tool_call") {
    const action = textValue(input.action) || displayToolName(name);
    if (status === "failed") return `${action} failed`;
    return status === "running" ? runningToolTitle(action) : completedToolTitle(action);
  }
  return status === "running" ? `Running ${name}` : `${name} complete`;
}

function detailFor(event: ActivityEvent) {
  if (event.status === "failed") return event.error_message || event.error_type || "The operation failed.";
  const input = event.input_summary || {};
  const output = event.output_summary || {};
  if (event.span_type === "tool_call") {
    return event.event === "span_finished"
      ? resultDetail(event.output_summary, event.status || "success", event.duration_ms)
      : toolDetail(event.input_summary);
  }
  if (event.span_type === "provider_call") {
    const request = isRecord(input.request) ? input.request : {};
    const toolCount = typeof input.tool_count === "number" ? input.tool_count : 0;
    const maxTokens = typeof request.max_tokens === "number" ? request.max_tokens : undefined;
    if (event.event === "span_finished") {
      const usage = isRecord(output.usage) ? output.usage : {};
      const tokens = numberValue(usage.total_tokens);
      const toolCalls = numberValue(output.tool_call_count);
      const parts = [
        tokens !== null && `${tokens} tokens`,
        toolCalls !== null && `${toolCalls} tool request${toolCalls === 1 ? "" : "s"}`,
      ].filter(Boolean);
      return `${parts.length > 0 ? parts.join(" · ") : "Received model output"}${formatDuration(event.duration_ms)}.`;
    }
    return `${toolCount} tool${toolCount === 1 ? "" : "s"} available${maxTokens ? `, ${maxTokens} max output tokens` : ""}.`;
  }
  if (event.span_type === "context_build" && event.event === "span_finished") {
    const systems = stringList(output.connected_systems);
    const tools = stringList(output.tools);
    const model = textValue(output.selected_model);
    const memories = numberValue(output.memories_injected);
    const rules = numberValue(output.rules_injected);
    const references = numberValue(output.search_results_injected);
    const contextCounts = [
      memories ? plural(memories, "memory", "memories") : "",
      rules ? plural(rules, "rule") : "",
      references ? plural(references, "reference") : "",
    ].filter(Boolean);
    const parts = [
      systems.length > 0 ? `Connected: ${systems.map(displaySystemName).join(", ")}` : "No connected account context",
      tools.length > 0 ? `Tools: ${tools.map(displayToolName).join(", ")}` : "",
      model ? `Model: ${model}` : "",
      contextCounts.join(", "),
    ].filter(Boolean);
    return parts.join(" · ");
  }
  if (event.span_type === "context_build") {
    const preview = textValue(input.user_message_preview);
    return preview ? `Reviewing: “${truncateText(preview, 90)}”` : "Checking account status, rules, memory, and available tools.";
  }
  if (event.span_type === "model_request" && event.event === "span_finished") {
    const tokens = numberValue(output.total_tokens);
    const toolCalls = numberValue(output.tool_call_count);
    const parts = [
      tokens !== null && `${tokens} tokens`,
      toolCalls !== null && `${toolCalls} tool call${toolCalls === 1 ? "" : "s"}`,
    ].filter(Boolean);
    return parts.length > 0 ? parts.join(" · ") : `Completed${formatDuration(event.duration_ms)}.`;
  }
  return event.event === "span_finished" ? `Completed${formatDuration(event.duration_ms)}.` : "In progress.";
}

function metaFor(event: ActivityEvent) {
  if (event.status === "failed") return event.error_type || "failed";
  if (event.span_type === "tool_call") {
    const input = event.input_summary || {};
    return textValue(input.connector) || displayToolName(event.span_name || "");
  }
  if (event.span_type === "provider_call") {
    const input = event.input_summary || {};
    return textValue(input.attempt_reason) || "model";
  }
  if (event.span_type === "context_build") return "context";
  return event.span_type || "step";
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
    meta: metaFor(event),
    durationMs: event.duration_ms,
    icon: event.span_type === "tool_call" ? "tool" : event.span_type === "provider_call" ? "model" : event.span_type === "context_build" ? "context" : "default",
  }));
}

function RowIcon({ row }: { row: ActivityRow }) {
  if (row.status === "success") return <CheckCircle2 className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.status === "failed") return <CircleDashed className="mt-0.5 w-3.5 h-3.5 text-danger shrink-0" />;
  if (row.icon === "tool") return <Wrench className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.icon === "model") return <TerminalSquare className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.icon === "context") return <Search className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  return <Loader2 className="mt-0.5 w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
}

function activitySummary(rows: ActivityRow[]) {
  if (rows.length === 0) return "Starting";
  const toolCount = rows.filter(row => row.icon === "tool").length;
  const modelCount = rows.filter(row => row.icon === "model").length;
  const completed = rows.filter(row => row.status === "success").length;
  const parts = [
    `${rows.length} event${rows.length === 1 ? "" : "s"}`,
    toolCount > 0 ? `${toolCount} tool${toolCount === 1 ? "" : "s"}` : "",
    modelCount > 0 ? `${modelCount} model call${modelCount === 1 ? "" : "s"}` : "",
    completed > 0 ? `${completed} done` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  const start = useMemo(() => startedAt(message), [message]);
  const [elapsed, setElapsed] = useState(() => elapsedSeconds(start));
  const rows = useMemo(() => rowsFromEvents(activityEvents(message)), [message]);
  const current = [...rows].reverse().find(row => row.status === "running");
  const summary = useMemo(() => activitySummary(rows), [rows]);
  const visibleRows = rows.length > 0 ? rows : [{
    id: "starting",
    status: "running" as const,
    title: "Starting assistant run",
    detail: "Waiting for the first live activity event.",
    meta: "starting",
    icon: "default" as const,
  }];

  useEffect(() => {
    const interval = setInterval(() => setElapsed(elapsedSeconds(start)), 1000);
    return () => clearInterval(interval);
  }, [start]);

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-2xl min-w-0 py-1 text-sm">
        <div className="flex items-center gap-2">
          <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
          <span className="font-semibold text-default">
            {current ? current.title : "Finalizing response"}
          </span>
          <span className="text-xs text-muted">· {formatElapsed(elapsed)}</span>
        </div>

        <p className="mt-1 text-muted leading-relaxed">
          {current ? current.detail : visibleRows[visibleRows.length - 1]?.detail}
        </p>

        <div className="mt-3 flex items-center gap-2 text-[11px] text-soft">
          <span className="h-px flex-1 bg-default" />
          <span>{summary}</span>
        </div>

        <div className="mt-3 space-y-2">
          {visibleRows.map(row => (
            <div
              key={row.id}
              className={`flex items-start gap-2 rounded-lg border px-3 py-2 ${
                row.status === "running"
                  ? "border-default bg-surface/70 text-default"
                  : "border-transparent bg-transparent text-muted"
              }`}
            >
              <RowIcon row={row} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 leading-snug">
                  <span className="font-semibold truncate">{row.title}</span>
                  {row.meta && (
                    <span className="shrink-0 rounded-full border border-default px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-soft">
                      {row.meta}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-muted leading-snug break-words">{row.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
