import type { ChatMessage, ChatSession } from "../types";
import { isAbortError } from "../hooks/useApi";

export const CHAT_STREAM_INACTIVITY_TIMEOUT_MS = 120_000;
export const CHAT_STREAM_COMPLETION_POLL_INTERVAL_MS = 2_500;
export const CHAT_STREAM_COMPLETION_POLL_TIMEOUT_MS = 180_000;

const CHAT_SESSIONS_CACHE_PREFIX = "ai-platform.chatSessions.";
const THINKING_STATUS_PREFIX_RE =
  /^\s*(?:(?:[^\s.]{1,16})\s+)?(?:processing|thinking|reasoning|analyzing|pondering|contemplating|musing|cogitating|ruminating|deliberating|mulling|reflecting|computing|synthesizing|formulating|brainstorming)\.\.\.\s*/i;
const EMPTY_THINKING_PLACEHOLDER_RE =
  /\b(?:current rewritten thinking|next thinking to process|provide the thinking content|don't see any .*thinking)\b/i;

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

export function pendingProgressMetadata(requestId: string, content: string, artifactCount: number, startedAt: string) {
  const summary = content.trim().replace(/\s+/g, " ").slice(0, 120);
  return {
    request_id: requestId,
    progress_context: {
      summary,
      has_artifacts: artifactCount > 0,
      started_at: startedAt,
    },
    activity_events: [],
    message_parts: [],
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

function detailString(value: unknown, defaultValue = "") {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return defaultValue;
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

export async function uploadFailureFromResponse(res: Response, defaultMessage: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body && typeof body === "object" && "detail" in body
    ? (body as { detail?: unknown }).detail
    : body;

  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    return detailString(record.error_message || record.message || record.error || detail, defaultMessage);
  }
  return detailString(detail, defaultMessage);
}

function coerceGatewayText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    return value.map(item => {
      if (typeof item === "string") return item;
      if (isRecord(item)) {
        if (typeof item.text === "string") return item.text;
        if (typeof item.output_text === "string") return item.output_text;
      }
      return "";
    }).join("");
  }
  if (isRecord(value)) {
    if (typeof value.text === "string") return value.text;
    if (typeof value.output_text === "string") return value.output_text;
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return String(value);
}

function coerceThinkingText(value: unknown): string {
  const raw = coerceGatewayText(value).replace(THINKING_STATUS_PREFIX_RE, "");
  return EMPTY_THINKING_PLACEHOLDER_RE.test(raw) ? "" : raw;
}

export function appendActivityEvent(message: ChatMessage, event: unknown): ChatMessage {
  const metadata = isRecord(message.metadata_json) ? { ...message.metadata_json } : {};
  const current = Array.isArray(metadata.activity_events) ? metadata.activity_events : [];
  metadata.activity_events = [...current, event];
  return { ...message, metadata_json: metadata };
}

type StoredMessagePart =
  | { type: "text"; text: string }
  | { type: "reasoning"; text: string }
  | {
    type: "tool-call";
    toolCallId: string;
    toolName: string;
    args: unknown;
    argsText: string;
    result?: unknown;
    isError?: boolean;
    durationMs?: number;
  };

function eventText(event: Record<string, unknown>, key: string) {
  const value = event[key];
  return typeof value === "string" ? value : "";
}

function eventNumber(event: Record<string, unknown>, key: string) {
  const value = event[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function messagePartsFrom(value: unknown): StoredMessagePart[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).flatMap((part): StoredMessagePart[] => {
    if (part.type === "text") {
      return typeof part.text === "string" ? [{ type: "text", text: part.text }] : [];
    }
    if (part.type === "reasoning") {
      return typeof part.text === "string" ? [{ type: "reasoning", text: part.text }] : [];
    }
    if (part.type === "tool-call") {
      const toolCallId = eventText(part, "toolCallId");
      const toolName = eventText(part, "toolName") || "tool";
      if (!toolCallId) return [];
      const next: StoredMessagePart = {
        type: "tool-call",
        toolCallId,
        toolName,
        args: "args" in part ? part.args : {},
        argsText: eventText(part, "argsText"),
      };
      if ("result" in part) next.result = part.result;
      if (typeof part.isError === "boolean") next.isError = part.isError;
      const durationMs = eventNumber(part, "durationMs");
      if (durationMs !== undefined) next.durationMs = durationMs;
      return [next];
    }
    return [];
  });
}

const STREAM_PART = {
  reasoning: (text: string): StoredMessagePart => ({ type: "reasoning", text }),
  text: (text: string): StoredMessagePart => ({ type: "text", text }),
};

function appendStreamPart(
  parts: StoredMessagePart[],
  type: "reasoning" | "text",
  delta: string,
): StoredMessagePart[] {
  if (!delta) return parts;
  const next = [...parts];

  for (let i = next.length - 1; i >= 0; i -= 1) {
    const part = next[i];

    if (part.type === type) {
      next[i] = { ...part, text: `${part.text}${delta}` };
      return next;
    }

    if (part.type !== "text" && part.type !== "reasoning") {
      break;
    }
  }

  return [...next, STREAM_PART[type](delta)];
}

function appendTextPart(parts: StoredMessagePart[], delta: string): StoredMessagePart[] {
  return appendStreamPart(parts, "text", delta);
}

function appendReasoningPart(parts: StoredMessagePart[], delta: string): StoredMessagePart[] {
  return appendStreamPart(parts, "reasoning", delta);
}

function replaceReasoningPart(parts: StoredMessagePart[], text: string): StoredMessagePart[] {
  if (!text) return parts;
  const next = [...parts];
  const last = next[next.length - 1];

  if (last?.type === "reasoning") {
    next[next.length - 1] = { ...last, text };
    return next;
  }

  return [...next, { type: "reasoning", text }];
}

function upsertToolCallPart(parts: StoredMessagePart[], event: Record<string, unknown>): StoredMessagePart[] {
  const id = eventText(event, "id");
  const name = eventText(event, "name") || "tool";
  const eventType = typeof event.type === "string" ? event.type : "";
  const patch: StoredMessagePart = {
    type: "tool-call",
    toolCallId: id || `tool:${parts.length}`,
    toolName: name,
    args: "args" in event ? event.args : {},
    argsText: eventText(event, "verboseArgs"),
  };
  if ("result" in event) patch.result = event.result;
  if (typeof event.isError === "boolean") patch.isError = event.isError;
  if (typeof event.error === "boolean") patch.isError = event.error;
  const durationMs = eventNumber(event, "durationMs");
  if (durationMs !== undefined) patch.durationMs = durationMs;

  const index = parts.findIndex(part => part.type === "tool-call" && id && part.toolCallId === id);
  if (index === -1) return [...parts, patch];
  return parts.map((part, partIndex) => (
    partIndex === index ? { ...part, ...patch, result: eventType === "tool.start" ? undefined : patch.result } : part
  ));
}

export function appendMessagePartEvent(message: ChatMessage, event: unknown): ChatMessage {
  if (!isRecord(event)) return message;
  const type = typeof event.type === "string" ? event.type : "";
  const metadata = isRecord(message.metadata_json) ? { ...message.metadata_json } : {};
  let messageParts = messagePartsFrom(metadata.message_parts);
  let content = message.content || "";
  let status = message.status;

  if (type === "message.delta") {
    const delta = coerceGatewayText(event.delta ?? event.text);
    if (delta) {
      content += delta;
      messageParts = appendTextPart(messageParts, delta).slice(-240);
      status = "streaming";
    }
  } else if (type === "thinking.delta") {
    // Matches Hermes: thinking.delta is status chrome, not visible reasoning.
  } else if (type === "reasoning.delta" || type === "reasoning.available") {
    const delta = coerceThinkingText(event.text ?? event.delta);
    if (delta) {
      status = "streaming";
      if (type === "reasoning.available") {
        messageParts = replaceReasoningPart(messageParts, delta).slice(-240);
      } else {
        messageParts = appendReasoningPart(messageParts, delta).slice(-240);
      }
    }
  } else if (type === "tool.start") {
    messageParts = upsertToolCallPart(messageParts, event).slice(-240);
  } else if (type === "tool.complete") {
    messageParts = upsertToolCallPart(messageParts, event).slice(-240);
  }
  metadata.message_parts = messageParts;

  return { ...message, content, status, metadata_json: metadata };
}

export function mergeStreamMetadata(finalMessage: ChatMessage, pendingMessage: ChatMessage | null): ChatMessage {
  if (!pendingMessage || !isRecord(pendingMessage.metadata_json)) return finalMessage;
  const pendingMetadata = pendingMessage.metadata_json;
  const finalMetadata = isRecord(finalMessage.metadata_json) ? { ...finalMessage.metadata_json } : {};
  if (finalMetadata.message_parts === undefined && pendingMetadata.message_parts !== undefined) {
    finalMetadata.message_parts = pendingMetadata.message_parts;
  }
  return { ...finalMessage, metadata_json: finalMetadata };
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
      ? "The AI service stopped sending progress before it finished. Please try again."
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
