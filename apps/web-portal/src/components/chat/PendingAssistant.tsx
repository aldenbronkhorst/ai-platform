import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import type { ChatMessage } from "../../types";

interface PendingAssistantProps {
  message: ChatMessage;
}

interface ActivityEvent {
  event?: string;
  span_type?: string;
  span_name?: string;
  status?: string;
  input_summary?: Record<string, unknown>;
  output_summary?: Record<string, unknown>;
}

interface FriendlyPhase {
  title: string;
  detail: string;
}

const DEFAULT_PHASE: FriendlyPhase = {
  title: "Thinking",
  detail: "Working out the best answer.",
};

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

function textValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function eventSearchText(event: ActivityEvent) {
  const input = event.input_summary || {};
  const output = event.output_summary || {};
  return [
    event.span_name,
    event.span_type,
    textValue(input.action),
    textValue(input.connector),
    textValue(input.tool_name),
    textValue(output.connector),
    textValue(output.tool_name),
    JSON.stringify(input.arguments || {}),
  ].filter(Boolean).join(" ").toLowerCase();
}

function connectedAppPhase(text: string): FriendlyPhase {
  if (text.includes("odoo")) {
    return { title: "Checking Odoo", detail: "Looking up the business information needed for your answer." };
  }
  if (text.includes("github")) {
    return { title: "Checking GitHub", detail: "Looking at the connected GitHub information." };
  }
  if (
    text.includes("microsoft")
    || text.includes("azure")
    || text.includes("graph")
    || text.includes("exchange")
    || text.includes("teams")
    || text.includes("sharepoint")
  ) {
    return { title: "Checking Microsoft", detail: "Using your connected Microsoft account where it helps." };
  }
  if (text.includes("document") || text.includes("artifact") || text.includes("file")) {
    return { title: "Reading your files", detail: "Pulling useful details from the files in this chat." };
  }
  return { title: "Checking connected apps", detail: "Looking up information from the systems you connected." };
}

function friendlyPhase(events: ActivityEvent[]): FriendlyPhase {
  if (events.length === 0) return DEFAULT_PHASE;

  const latestFirst = [...events].reverse();
  const running = latestFirst.find(event => event.event !== "span_finished" && event.status !== "success" && event.status !== "failed");
  const current = running || latestFirst[0];
  const text = eventSearchText(current);

  if (current.status === "failed") {
    return {
      title: "Still working",
      detail: "One check did not work, so I am using what is available.",
    };
  }

  if (current.span_type === "tool_call") {
    return connectedAppPhase(text);
  }

  if (current.span_type === "context_build") {
    if (text.includes("memory")) {
      return { title: "Checking memory", detail: "Looking for useful things you have asked me to remember." };
    }
    if (text.includes("document") || text.includes("artifact") || text.includes("file")) {
      return { title: "Reading your files", detail: "Finding the relevant file details before answering." };
    }
    return { title: "Getting ready", detail: "Checking chat history, memory, and connected account status." };
  }

  const finishedTools = events.some(event => event.span_type === "tool_call" && event.event === "span_finished");
  if (finishedTools) {
    return { title: "Writing the reply", detail: "Using the information found to answer clearly." };
  }

  return DEFAULT_PHASE;
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  const start = useMemo(() => startedAt(message), [message]);
  const [elapsed, setElapsed] = useState(() => elapsedSeconds(start));
  const phase = useMemo(() => friendlyPhase(activityEvents(message)), [message]);

  useEffect(() => {
    const interval = setInterval(() => setElapsed(elapsedSeconds(start)), 1000);
    return () => clearInterval(interval);
  }, [start]);

  return (
    <div className="w-full flex justify-start">
      <div className="w-full max-w-2xl min-w-0 py-1 text-sm">
        <div className="flex items-center gap-2 text-muted">
          <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
          <span className="font-semibold text-default">{phase.title}</span>
          <span className="text-xs text-soft">{formatElapsed(elapsed)}</span>
        </div>
        <p className="mt-1 text-muted leading-relaxed">{phase.detail}</p>
      </div>
    </div>
  );
}
