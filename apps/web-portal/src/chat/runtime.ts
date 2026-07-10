import type { ChatMessage, ChatSession } from "../types";

export const CHAT_EVENT_RECONNECT_MS = 1_000;

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

export interface ChatStreamEvent {
  id: number | null;
  event: string;
  data: unknown;
}

export interface ChatFailurePayload {
  requestId: string;
  errorType: string;
  errorMessage: string;
  httpStatus: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function eventText(event: Record<string, unknown>, key: string) {
  const value = event[key];
  return typeof value === "string" ? value : "";
}

function eventNumber(event: Record<string, unknown>, key: string) {
  const value = event[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function messageParts(message: ChatMessage): StoredMessagePart[] {
  const metadata = isRecord(message.metadata_json) ? message.metadata_json : {};
  const value = metadata.message_parts;
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).flatMap((part): StoredMessagePart[] => {
    if (part.type === "text" && typeof part.text === "string") return [{ type: "text", text: part.text }];
    if (part.type === "reasoning" && typeof part.text === "string") return [{ type: "reasoning", text: part.text }];
    if (part.type !== "tool-call") return [];
    const toolCallId = eventText(part, "toolCallId");
    if (!toolCallId) return [];
    const next: StoredMessagePart = {
      type: "tool-call",
      toolCallId,
      toolName: eventText(part, "toolName") || "tool",
      args: "args" in part ? part.args : {},
      argsText: eventText(part, "argsText"),
    };
    if ("result" in part) next.result = part.result;
    if (typeof part.isError === "boolean") next.isError = part.isError;
    const durationMs = eventNumber(part, "durationMs");
    if (durationMs !== undefined) next.durationMs = durationMs;
    return [next];
  });
}

function withMessageParts(message: ChatMessage, parts: StoredMessagePart[]) {
  const metadata = isRecord(message.metadata_json) ? { ...message.metadata_json } : {};
  metadata.message_parts = parts;
  return { ...message, metadata_json: metadata };
}

function appendStreamPart(parts: StoredMessagePart[], type: "text" | "reasoning", delta: string) {
  if (!delta) return parts;
  const next = [...parts];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    const part = next[index];
    if (part.type === type) {
      next[index] = { ...part, text: part.text + delta };
      return next;
    }
    if (part.type !== "text" && part.type !== "reasoning") break;
  }
  next.push({ type, text: delta });
  return next;
}

function replaceReasoningPart(parts: StoredMessagePart[], text: string) {
  if (!text) return parts;
  const next = [...parts];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    const part = next[index];
    if (part.type === "reasoning") {
      next[index] = { ...part, text };
      return next;
    }
    if (part.type !== "text") break;
  }
  next.push({ type: "reasoning", text });
  return next;
}

function upsertToolPart(parts: StoredMessagePart[], event: Record<string, unknown>) {
  const id = eventText(event, "id") || eventText(event, "tool_call_id");
  const eventType = eventText(event, "type");
  const patch: StoredMessagePart = {
    type: "tool-call",
    toolCallId: id || `tool:${parts.length}`,
    toolName: eventText(event, "name") || "tool",
    args: "args" in event ? event.args : {},
    argsText: eventText(event, "verboseArgs"),
  };
  if ("result" in event && eventType !== "tool.start") patch.result = event.result;
  if (typeof event.isError === "boolean") patch.isError = event.isError;
  if (typeof event.error === "boolean") patch.isError = event.error;
  const durationMs = eventNumber(event, "durationMs");
  if (durationMs !== undefined) patch.durationMs = durationMs;

  const index = parts.findIndex(part => part.type === "tool-call" && id && part.toolCallId === id);
  if (index < 0) return [...parts, patch];
  const next = [...parts];
  next[index] = { ...next[index], ...patch };
  return next;
}

function replaceByRequestRole(messages: ChatMessage[], message: ChatMessage) {
  const requestId = messageRequestId(message);
  const matchingIndex = messages.findIndex(current => (
    current.id === message.id
    || (requestId && current.role === message.role && messageRequestId(current) === requestId)
  ));
  if (matchingIndex < 0) return [...messages, message];
  const next = [...messages];
  next[matchingIndex] = message;
  return next.filter((current, index) => (
    index === matchingIndex
    || (
      current.id !== message.id
      && !(requestId && current.role === message.role && messageRequestId(current) === requestId)
    )
  ));
}

function assistantForRequest(messages: ChatMessage[], requestId: string) {
  return messages.find(message => message.role === "assistant" && messageRequestId(message) === requestId);
}

function updateAssistant(messages: ChatMessage[], requestId: string, update: (message: ChatMessage) => ChatMessage) {
  const index = messages.findIndex(message => message.role === "assistant" && messageRequestId(message) === requestId);
  if (index < 0) return messages;
  const next = [...messages];
  next[index] = update(next[index]);
  return next;
}

export function applyChatStreamEvent(messages: ChatMessage[], streamEvent: ChatStreamEvent): ChatMessage[] {
  if (!isRecord(streamEvent.data)) return messages;
  const event = streamEvent.data;
  const eventType = streamEvent.event || eventText(event, "type");
  const requestId = eventText(event, "request_id");

  if (eventType === "message.start") {
    let next = messages;
    if (isRecord(event.user_message)) next = replaceByRequestRole(next, normalizeChatMessage(event.user_message as unknown as ChatMessage));
    const completedAssistant = requestId ? assistantForRequest(next, requestId) : undefined;
    if (isRecord(event.assistant_message) && completedAssistant?.status !== "completed" && completedAssistant?.status !== "failed") {
      next = replaceByRequestRole(next, normalizeChatMessage(event.assistant_message as unknown as ChatMessage));
    }
    return next;
  }

  if (eventType === "message.complete" && "id" in event) {
    return replaceByRequestRole(messages, normalizeChatMessage(event as unknown as ChatMessage));
  }

  if (!requestId) return messages;
  const assistant = assistantForRequest(messages, requestId);
  if (!assistant || assistant.status === "completed" || assistant.status === "failed") return messages;

  if (eventType === "message.delta") {
    const delta = eventText(event, "text") || eventText(event, "delta");
    if (!delta) return messages;
    return updateAssistant(messages, requestId, message => withMessageParts({
      ...message,
      content: message.content + delta,
      status: "streaming",
    }, appendStreamPart(messageParts(message), "text", delta)));
  }

  if (eventType === "reasoning.delta" || eventType === "reasoning.available") {
    const text = eventText(event, "text") || eventText(event, "delta");
    if (!text) return messages;
    return updateAssistant(messages, requestId, message => withMessageParts({ ...message, status: "streaming" }, (
      eventType === "reasoning.available"
        ? replaceReasoningPart(messageParts(message), text)
        : appendStreamPart(messageParts(message), "reasoning", text)
    )));
  }

  if (eventType === "tool.start" || eventType === "tool.complete") {
    return updateAssistant(messages, requestId, message => withMessageParts({
      ...message,
      status: eventType === "tool.start" ? "tool_running" : "streaming",
    }, upsertToolPart(messageParts(message), event)));
  }

  if (eventType === "message.cancelled") {
    return updateAssistant(messages, requestId, message => ({
      ...message,
      status: "completed",
      metadata_json: { ...(isRecord(message.metadata_json) ? message.metadata_json : {}), cancelled: true },
    }));
  }

  if (eventType === "error") {
    const failure = chatFailureFromDetail(event, requestId, 502);
    return updateAssistant(messages, requestId, message => ({
      ...message,
      status: "failed",
      error_message: JSON.stringify(failure),
    }));
  }

  return messages;
}

function chatSessionTime(session: ChatSession) {
  const time = Date.parse(session.last_message_at);
  return Number.isNaN(time) ? 0 : time;
}

export function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((a, b) => chatSessionTime(b) - chatSessionTime(a));
}

export function mobileViewportMatches() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
}

export function pendingProgressMetadata(requestId: string, content: string, artifactCount: number, startedAt: string) {
  return {
    request_id: requestId,
    status: "streaming",
    progress_context: {
      summary: content.trim().replace(/\s+/g, " ").slice(0, 120),
      has_artifacts: artifactCount > 0,
      started_at: startedAt,
    },
    message_parts: [],
  };
}

export function patchChatSession(session: ChatSession, patch: Partial<ChatSession>) {
  const updated = { ...session, ...patch };
  return JSON.stringify(updated) === JSON.stringify(session) ? session : updated;
}

export function chatFailureFromDetail(detail: unknown, requestId: string, httpStatus: number): ChatFailurePayload {
  if (isRecord(detail)) {
    return {
      requestId: detailString(detail.request_id, requestId),
      errorType: detailString(detail.error_type, "server_error"),
      errorMessage: detailString(detail.error_message, "Something went wrong while generating the response."),
      httpStatus,
    };
  }
  return { requestId, errorType: "server_error", errorMessage: "Something went wrong while generating the response.", httpStatus };
}

export async function chatFailureFromResponse(res: Response, requestId: string): Promise<ChatFailurePayload> {
  const body = await res.json().catch(() => null);
  return chatFailureFromDetail(isRecord(body) && "detail" in body ? body.detail : body, requestId, res.status);
}

export async function uploadFailureFromResponse(res: Response, defaultMessage: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = isRecord(body) && "detail" in body ? body.detail : body;
  if (isRecord(detail)) return detailString(detail.error_message || detail.message || detail.error || detail, defaultMessage);
  return detailString(detail, defaultMessage);
}

export function messageRequestId(message: ChatMessage): string | null {
  const metadata = message.metadata_json;
  return isRecord(metadata) && typeof metadata.request_id === "string" ? metadata.request_id : null;
}

export function normalizeChatMessage(message: ChatMessage): ChatMessage {
  const metadata = message.metadata_json;
  if (!isRecord(metadata)) return message;
  const metadataStatus = typeof metadata.status === "string" ? metadata.status : "";
  if (metadata.failed !== true) return metadataStatus ? { ...message, status: metadataStatus as ChatMessage["status"] } : message;
  const failure = chatFailureFromDetail(metadata, messageRequestId(message) || "", 502);
  return { ...message, status: "failed", error_message: JSON.stringify(failure) };
}

export function parseSseChunk(buffer: string) {
  const events: ChatStreamEvent[] = [];
  const blocks = buffer.split(/\n\n/);
  const rest = blocks.pop() || "";
  for (const block of blocks) {
    let event = "message";
    let id: number | null = null;
    const dataLines: string[] = [];
    for (const line of block.split(/\n/)) {
      if (line.startsWith("id:")) {
        const value = Number(line.slice(3).trim());
        id = Number.isFinite(value) ? value : null;
      } else if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    const rawData = dataLines.join("\n");
    let data: unknown;
    try {
      data = rawData ? JSON.parse(rawData) : null;
    } catch {
      data = rawData;
    }
    events.push({ id, event, data });
  }
  return { events, rest };
}
