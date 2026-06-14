import type { ChatMessage, ChatSession } from "../types";
import { isAbortError } from "../hooks/useApi";

export const CHAT_REQUEST_TIMEOUT_MS = 180_000;

const CHAT_SESSIONS_CACHE_PREFIX = "ai-platform.chatSessions.";
const CONNECTOR_PROGRESS_HINTS = [
  { label: "Azure", keywords: ["azure", "subscription", "resource group", "container app", "key vault"] },
  { label: "GitHub", keywords: ["github", "repo", "pull request", "commit", "branch", "workflow", "actions"] },
  { label: "Odoo", keywords: ["odoo", "invoice", "credit note", "customer", "sale order", "profit and loss", "turnover"] },
];

function chatSessionsCacheKey(email: string) {
  return `${CHAT_SESSIONS_CACHE_PREFIX}${email.toLowerCase()}`;
}

export function readCachedChatSessions(email: string): ChatSession[] {
  if (!email) return [];
  try {
    const raw = window.localStorage.getItem(chatSessionsCacheKey(email));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed as ChatSession[] : [];
  } catch {
    return [];
  }
}

export function writeCachedChatSessions(email: string, sessions: ChatSession[]) {
  if (!email) return;
  try {
    window.localStorage.setItem(chatSessionsCacheKey(email), JSON.stringify(sessions));
  } catch {
    // Ignore storage quota/privacy errors; the live API remains authoritative.
  }
}

function chatSessionTime(session: ChatSession) {
  const time = Date.parse(session.last_message_at);
  return Number.isNaN(time) ? 0 : time;
}

export function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((a, b) => chatSessionTime(b) - chatSessionTime(a));
}

export function mergeFetchedChatSessions(
  fetched: ChatSession[],
  existing: ChatSession[],
  activeSessionId: string | null,
) {
  const byId = new Map(fetched.map(session => [session.id, session]));
  if (activeSessionId && !byId.has(activeSessionId)) {
    const activeLocal = existing.find(session => session.id === activeSessionId);
    if (activeLocal) byId.set(activeLocal.id, activeLocal);
  }
  return sortChatSessions(Array.from(byId.values()));
}

export function mobileViewportMatches() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
}

function connectorProgressHints(content: string) {
  const normalized = content.toLowerCase();
  return CONNECTOR_PROGRESS_HINTS
    .filter(({ keywords }) => keywords.some(keyword => normalized.includes(keyword)))
    .map(({ label }) => label);
}

export function pendingProgressMetadata(requestId: string, content: string, artifactCount: number, startedAt: string) {
  const summary = content.trim().replace(/\s+/g, " ").slice(0, 120);
  return {
    request_id: requestId,
    progress_context: {
      summary,
      connectors: connectorProgressHints(content),
      has_artifacts: artifactCount > 0,
      started_at: startedAt,
    },
    activity_events: [],
  };
}

export function patchChatSession(session: ChatSession, patch: Partial<ChatSession>) {
  const updated = { ...session, ...patch };
  return (
    updated.title === session.title &&
    updated.status === session.status &&
    updated.created_at === session.created_at &&
    updated.last_message_at === session.last_message_at
  ) ? session : updated;
}

export interface ChatFailurePayload {
  requestId: string;
  errorType: string;
  errorMessage: string;
  httpStatus: number;
}

function detailString(value: unknown, fallback = "") {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return fallback;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function chatFailureFromDetail(detail: unknown, requestId: string, httpStatus: number): ChatFailurePayload {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    return {
      requestId: detailString(record.request_id, requestId),
      errorType: detailString(record.error_type, "server_error"),
      errorMessage: detailString(record.error_message, "Something went wrong while generating the response."),
      httpStatus,
    };
  }
  return {
    requestId,
    errorType: "server_error",
    errorMessage: "Something went wrong while generating the response.",
    httpStatus,
  };
}

export async function chatFailureFromResponse(res: Response, requestId: string): Promise<ChatFailurePayload> {
  const body = await res.json().catch(() => null);
  const respRequestId = res.headers.get("X-Request-ID") || requestId;
  if (body && body.detail) {
    return chatFailureFromDetail(body.detail, respRequestId, res.status);
  }
  return {
    requestId: respRequestId,
    errorType: "server_error",
    errorMessage: `Server returned ${res.status}`,
    httpStatus: res.status,
  };
}

export async function uploadFailureFromResponse(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body && typeof body === "object" && "detail" in body
    ? (body as { detail?: unknown }).detail
    : body;

  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    return detailString(record.error_message || record.message || record.error || detail, fallback);
  }
  return detailString(detail, fallback);
}

export function appendActivityEvent(message: ChatMessage, event: unknown): ChatMessage {
  const metadata = isRecord(message.metadata_json) ? { ...message.metadata_json } : {};
  const current = Array.isArray(metadata.activity_events) ? metadata.activity_events : [];
  metadata.activity_events = [...current, event];
  return { ...message, metadata_json: metadata };
}

export function parseSseChunk(buffer: string) {
  const events: Array<{ event: string; data: unknown }> = [];
  const blocks = buffer.split(/\n\n/);
  const rest = blocks.pop() || "";
  for (const block of blocks) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split(/\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    const rawData = dataLines.join("\n");
    let data: unknown;
    try {
      data = rawData ? JSON.parse(rawData) : null;
    } catch {
      data = rawData;
    }
    events.push({ event, data });
  }
  return { events, rest };
}

export function chatFailureFromNetwork(err: unknown, requestId: string): ChatFailurePayload {
  const timeout = isAbortError(err);
  return {
    requestId,
    errorType: timeout ? "timeout" : "network",
    errorMessage: timeout
      ? "The request took too long to complete. Please try again or narrow the question."
      : "The AI service could not be reached. Please check your connection and try again.",
    httpStatus: 0,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function messageRequestId(message: ChatMessage): string | null {
  const metadata = message.metadata_json;
  if (!isRecord(metadata)) return null;
  if (typeof metadata.request_id === "string") return metadata.request_id;
  return null;
}

export function normalizeChatMessage(message: ChatMessage): ChatMessage {
  const metadata = message.metadata_json;
  if (!isRecord(metadata) || metadata.failed !== true) return message;

  const requestId = typeof metadata.request_id === "string" ? metadata.request_id : "";
  const errorType = typeof metadata.error_type === "string" ? metadata.error_type : "server_error";
  const errorText = typeof metadata.error_message === "string"
    ? metadata.error_message
    : "The model service could not generate a response right now.";

  return {
    ...message,
    status: "failed",
    error_message: JSON.stringify({
      requestId,
      errorType,
      errorMessage: errorText,
      httpStatus: 502,
    }),
  };
}
