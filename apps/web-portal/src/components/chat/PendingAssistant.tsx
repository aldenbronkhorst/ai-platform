import { useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Search, TerminalSquare, Wrench } from "lucide-react";
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
  spanType?: string;
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

function meaningfulResultKeys(result: Record<string, unknown>) {
  return stringList(result.keys)
    .filter(key => !["error", "error_type", "message", "status"].includes(key))
    .slice(0, 4);
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
  if (value === "ms_azure_cli") return "Azure Resource Manager";
  if (value === "ms_graph") return "Microsoft Graph";
  if (value === "ms_exchange_powershell") return "Exchange Online PowerShell";
  if (value === "ms_teams_powershell") return "Microsoft Teams PowerShell";
  if (value === "ms_sharepoint_pnp_powershell") return "SharePoint PnP PowerShell";
  if (value === "github_cli") return "GitHub CLI";
  return value.replace(/_/g, " ");
}

function displaySystemName(value: string) {
  if (value === "odoo") return "Odoo";
  if (value === "azure_cli") return "Azure CLI";
  if (value === "microsoft_graph") return "Microsoft Graph";
  if (value === "exchange_online") return "Exchange Online";
  if (value === "teams_admin") return "Teams Admin";
  if (value === "sharepoint_pnp") return "SharePoint / PnP";
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
  if (isRecord(result) && result.error === true) {
    const message = textValue(result.message);
    return `${message ? truncateText(message) : "Handled a recoverable tool issue."}${formatDuration(durationMs)}`;
  }
  const countValue = isRecord(result) ? numberValue(result.count) : null;
  const count = countValue !== null ? `${countValue} result${countValue === 1 ? "" : "s"}` : "";
  const keys = isRecord(result) ? meaningfulResultKeys(result) : [];
  const summary = count || (keys.length > 0 ? `Returned ${keys.join(", ")}` : "Completed.");
  return `${summary}${formatDuration(durationMs)}`;
}

function hasHandledToolIssue(event: ActivityEvent) {
  if (event.status === "failed" || event.span_type !== "tool_call" || event.event !== "span_finished") return false;
  const output = event.output_summary || {};
  const result = isRecord(output.result) ? output.result : {};
  return result.error === true;
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
  if (type === "model_request") return status === "running" ? "Working" : "Finished";
  if (type === "tool_call") {
    const action = textValue(input.action) || displayToolName(name);
    if (status === "failed") return `${action} failed`;
    if (hasHandledToolIssue(event)) return `${action} skipped`;
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
    const contextCounts = [
      memories ? plural(memories, "memory", "memories") : "",
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
    return preview ? `Reviewing: “${truncateText(preview, 90)}”` : "Checking account status, memory, and available tools.";
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
  if (hasHandledToolIssue(event)) {
    const output = event.output_summary || {};
    const result = isRecord(output.result) ? output.result : {};
    return textValue(result.error_type) || "handled";
  }
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
    spanType: event.span_type,
  }));
}

function RowIcon({ row }: { row: ActivityRow }) {
  if (row.status === "success") return <CheckCircle2 className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />;
  if (row.status === "failed") return <AlertCircle className="mt-0.5 w-3.5 h-3.5 text-danger shrink-0" />;
  if (row.status === "running") return <Loader2 className="mt-0.5 w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
  if (row.icon === "tool") return <Wrench className="mt-0.5 w-3.5 h-3.5 text-muted shrink-0" />;
  if (row.icon === "model") return <TerminalSquare className="mt-0.5 w-3.5 h-3.5 text-muted shrink-0" />;
  if (row.icon === "context") return <Search className="mt-0.5 w-3.5 h-3.5 text-muted shrink-0" />;
  return <Loader2 className="mt-0.5 w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
}

function activitySummary(rows: ActivityRow[], elapsed: number, isTurnRunning: boolean) {
  const completed = rows.filter(row => row.status === "success").length;
  const running = rows.find(row => row.status === "running");
  if (running || isTurnRunning) return `Working for ${formatElapsed(elapsed)}`;
  if (completed > 0) return `${plural(completed, "step")} done`;
  return `Working for ${formatElapsed(elapsed)}`;
}

function visibleActivityRows(rows: ActivityRow[]) {
  const nonUmbrella = rows.filter(row =>
    row.spanType !== "model_request" &&
    !(row.spanType === "provider_call" && row.status === "success")
  );
  const collapsed: ActivityRow[] = [];

  for (const row of nonUmbrella) {
    const previous = collapsed[collapsed.length - 1];
    if (
      previous &&
      previous.status === "success" &&
      row.status === "success" &&
      previous.title === row.title &&
      previous.detail === row.detail
    ) {
      continue;
    }
    collapsed.push(row);
  }

  return collapsed.slice(-7);
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  const start = useMemo(() => startedAt(message), [message]);
  const [elapsed, setElapsed] = useState(() => elapsedSeconds(start));
  const rows = useMemo(() => rowsFromEvents(activityEvents(message)), [message]);
  const visibleRows = useMemo(() => visibleActivityRows(rows), [rows]);
  const isTurnRunning = message.status === "pending" || message.status === "sending" || rows.some(row => row.spanType === "model_request" && row.status === "running");
  const current = [...visibleRows].reverse().find(row => row.status === "running");
  const summary = useMemo(() => activitySummary(visibleRows, elapsed, isTurnRunning), [visibleRows, elapsed, isTurnRunning]);
  const displayedRows = visibleRows.length > 0 ? visibleRows : [{
    id: "starting",
    status: "running" as const,
    title: "Starting",
    detail: "Preparing the request.",
    meta: "starting",
    icon: "default" as const,
  }];
  const lastCompleted = [...displayedRows].reverse().find(row => row.status === "success");
  const headline = current?.title || (lastCompleted ? "Composing response" : "Starting");
  const subtext = current?.detail || (lastCompleted ? "Using the gathered results to write the answer." : displayedRows[0].detail);

  useEffect(() => {
    const interval = setInterval(() => setElapsed(elapsedSeconds(start)), 1000);
    return () => clearInterval(interval);
  }, [start]);

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-2xl min-w-0 py-1 text-sm">
        <div className="flex items-center gap-2 text-muted">
          <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
          <span className="font-semibold text-default">{summary}</span>
        </div>

        <p className="mt-1 text-muted leading-relaxed">
          <span className="text-default">{headline}</span>
          {subtext ? <span className="text-muted"> · {subtext}</span> : null}
        </p>

        <div className="mt-3 space-y-2 border-l border-default/70 pl-4">
          {displayedRows.map(row => (
            <div
              key={row.id}
              className={`flex items-start gap-2 ${row.status === "running" ? "text-default" : "text-muted"}`}
            >
              <RowIcon row={row} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 leading-snug">
                  <span className={row.status === "running" ? "font-semibold" : "font-medium"}>{row.title}</span>
                  {row.meta && (
                    <span className="shrink-0 text-[11px] uppercase tracking-wide text-soft">
                      {row.meta.replace(/_/g, " ")}
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted leading-snug break-words">{row.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
