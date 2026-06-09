import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";
import { loginRequestWithAuthHint, readStoredAuthHint } from "./authSession";
import type { ChatAttachment, ChatSession, ChatMessage, AttachedFile } from "./types";
import { AppShell } from "./components/layout/AppShell";
import { LoginPage } from "./components/auth/LoginPage";
import { ChatView } from "./components/chat/ChatView";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { TasksPage } from "./pages/TasksPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { AuditPage } from "./pages/AuditPage";
import { AiConfigView } from "./AiConfigView";
import { AdminPage } from "./pages/AdminPage";
import type { ActiveTab } from "./types";
import { APIM_BASE_URL, fetchWithTimeout, isAbortError } from "./hooks/useApi";
import { usePortalAuth } from "./hooks/usePortalAuth";
import { useSpeechRecognition } from "./hooks/useSpeechRecognition";

function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

const CHAT_REQUEST_TIMEOUT_MS = 180_000;
const CHAT_SESSIONS_CACHE_PREFIX = "ai-platform.chatSessions.";

function chatSessionsCacheKey(email: string) {
  return `${CHAT_SESSIONS_CACHE_PREFIX}${email.toLowerCase()}`;
}

function readCachedChatSessions(email: string): ChatSession[] {
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

function writeCachedChatSessions(email: string, sessions: ChatSession[]) {
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

function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((a, b) => chatSessionTime(b) - chatSessionTime(a));
}

function mergeFetchedChatSessions(fetched: ChatSession[], existing: ChatSession[], activeSessionId: string | null) {
  const byId = new Map(fetched.map(session => [session.id, session]));
  if (activeSessionId && !byId.has(activeSessionId)) {
    const activeLocal = existing.find(session => session.id === activeSessionId);
    if (activeLocal) byId.set(activeLocal.id, activeLocal);
  }
  return sortChatSessions(Array.from(byId.values()));
}

const CONNECTOR_PROGRESS_HINTS = [
  { label: "Azure", keywords: ["azure", "subscription", "resource group", "container app", "key vault", "foundry"] },
  { label: "GitHub", keywords: ["github", "repo", "pull request", "commit", "branch", "workflow", "actions"] },
  { label: "Odoo", keywords: ["odoo", "invoice", "credit note", "customer", "sale order", "profit and loss", "turnover"] },
];

function mobileViewportMatches() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
}

function connectorProgressHints(content: string) {
  const normalized = content.toLowerCase();
  return CONNECTOR_PROGRESS_HINTS
    .filter(({ keywords }) => keywords.some(keyword => normalized.includes(keyword)))
    .map(({ label }) => label);
}

function pendingProgressMetadata(requestId: string, content: string, artifactCount: number, startedAt: string) {
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

function patchChatSession(session: ChatSession, patch: Partial<ChatSession>) {
  const updated = { ...session, ...patch };
  return (
    updated.title === session.title &&
    updated.status === session.status &&
    updated.created_at === session.created_at &&
    updated.last_message_at === session.last_message_at
  ) ? session : updated;
}

interface ChatFailurePayload {
  requestId: string;
  errorType: string;
  errorMessage: string;
  technicalDetail: string;
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

function chatFailureFromDetail(detail: unknown, requestId: string, httpStatus: number): ChatFailurePayload {
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    return {
      requestId: detailString(record.request_id, requestId),
      errorType: detailString(record.error_type, "server_error"),
      errorMessage: detailString(record.error_message, "Something went wrong while generating the response."),
      technicalDetail: detailString(record.technical_detail, detailString(detail)),
      httpStatus,
    };
  }
  return {
    requestId,
    errorType: "server_error",
    errorMessage: "Something went wrong while generating the response.",
    technicalDetail: detailString(detail),
    httpStatus,
  };
}

async function chatFailureFromResponse(res: Response, requestId: string): Promise<ChatFailurePayload> {
  const body = await res.json().catch(() => null);
  const respRequestId = res.headers.get("X-Request-ID") || requestId;
  if (body && body.detail) {
    return chatFailureFromDetail(body.detail, respRequestId, res.status);
  }
  return {
    requestId: respRequestId,
    errorType: "server_error",
    errorMessage: `Server returned ${res.status}`,
    technicalDetail: detailString(body),
    httpStatus: res.status,
  };
}

async function uploadFailureFromResponse(res: Response, fallback: string): Promise<string> {
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

function appendActivityEvent(message: ChatMessage, event: unknown): ChatMessage {
  const metadata = isRecord(message.metadata_json) ? { ...message.metadata_json } : {};
  const current = Array.isArray(metadata.activity_events) ? metadata.activity_events : [];
  metadata.activity_events = [...current, event];
  return { ...message, metadata_json: metadata };
}

function parseSseChunk(buffer: string) {
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

function chatFailureFromNetwork(err: unknown, requestId: string): ChatFailurePayload {
  const timeout = isAbortError(err);
  return {
    requestId,
    errorType: timeout ? "timeout" : "network",
    errorMessage: timeout
      ? "The request took too long to complete. Please try again or narrow the question."
      : "The AI service could not be reached. Please check your connection and try again.",
    technicalDetail: timeout
      ? `Request timed out after ${CHAT_REQUEST_TIMEOUT_MS / 1000} seconds`
      : errorMessage(err),
    httpStatus: 0,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function messageRequestId(message: ChatMessage): string | null {
  const metadata = message.metadata_json;
  if (!isRecord(metadata)) return null;
  if (typeof metadata.request_id === "string") return metadata.request_id;
  const technicalDetails = metadata.technical_details;
  if (isRecord(technicalDetails) && typeof technicalDetails.request_id === "string") {
    return technicalDetails.request_id;
  }
  return null;
}

function normalizeChatMessage(message: ChatMessage): ChatMessage {
  const metadata = message.metadata_json;
  if (!isRecord(metadata) || metadata.failed !== true) return message;

  const requestId = typeof metadata.request_id === "string" ? metadata.request_id : "";
  const errorType = typeof metadata.error_type === "string" ? metadata.error_type : "server_error";
  const errorText = typeof metadata.error_message === "string"
    ? metadata.error_message
    : "The model service could not generate a response right now.";
  const traceId = typeof metadata.trace_id === "string" ? metadata.trace_id : "";

  return {
    ...message,
    status: "failed",
    error_message: JSON.stringify({
      requestId,
      errorType,
      errorMessage: errorText,
      technicalDetail: traceId ? `Trace ID: ${traceId}` : "",
      httpStatus: 502,
    }),
  };
}

function mergeChatMessages(persistedMessages: ChatMessage[], localMessages: ChatMessage[]) {
  const normalizedPersisted = persistedMessages.map(normalizeChatMessage);
  if (localMessages.length === 0) return normalizedPersisted;

  const persistedIds = new Set(normalizedPersisted.map(message => message.id));
  const persistedRequestRoles = new Set(
    normalizedPersisted
      .map(message => {
        const requestId = messageRequestId(message);
        return requestId ? `${message.role}:${requestId}` : null;
      })
      .filter((value): value is string => Boolean(value))
  );

  const localOnly = localMessages.filter(message => {
    if (persistedIds.has(message.id)) return false;
    const requestId = messageRequestId(message);
    return !requestId || !persistedRequestRoles.has(`${message.role}:${requestId}`);
  });

  return [...normalizedPersisted, ...localOnly];
}

function removeRequestMessages(messages: ChatMessage[], requestId: string) {
  return messages.filter(message => messageRequestId(message) !== requestId);
}

export default function App({ startupAuthError }: { startupAuthError: string | null }) {
  const {
    accessToken,
    accounts,
    activeUser,
    authError,
    enableLocalMock,
    inProgress,
    instance,
    isTokenLoading,
    signInLocalMock,
    signOut,
  } = usePortalAuth();

  const [activeTab, setActiveTab] = useState<ActiveTab>("chat");
  const [isMobileViewport, setIsMobileViewport] = useState(mobileViewportMatches);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(mobileViewportMatches);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const hintedLoginRequest = useMemo(
    () => loginRequestWithAuthHint(loginRequest, readStoredAuthHint()),
    [],
  );

  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [localMessagesBySession, setLocalMessagesBySession] = useState<Record<string, ChatMessage[]>>({});
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeSessionId = activeSession?.id ?? null;
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  const localMessagesBySessionRef = useRef<Record<string, ChatMessage[]>>({});

  const handleTranscript = useCallback((transcript: string) => {
    setChatInput(prev => (prev ? prev + " " + transcript : transcript));
  }, []);
  const {
    voiceState,
    toggleVoice: handleToggleVoice,
    interimTranscript: voiceInterimTranscript,
  } = useSpeechRecognition(handleTranscript);
  const activeUserEmail = activeUser?.email || "";

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const syncViewport = () => {
      const isMobile = query.matches;
      setIsMobileViewport(isMobile);
      if (isMobile) {
        setIsSidebarCollapsed(true);
        setIsProfileMenuOpen(false);
      }
    };

    syncViewport();
    query.addEventListener("change", syncViewport);
    return () => query.removeEventListener("change", syncViewport);
  }, []);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    localMessagesBySessionRef.current = localMessagesBySession;
  }, [localMessagesBySession]);

  const isActiveChatSending = activeSessionId ? sendingSessionIds.includes(activeSessionId) : false;

  const getHeaders = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

  const hasRole = (allowedRoles: string[]) => {
    if (!activeUser) return false;
    if (activeUser.roles.includes("AIPlatform.Admin")) return true;
    return activeUser.roles.some(r => allowedRoles.includes(r));
  };

  const fetchChatSessions = useCallback(async () => {
    if (!accessToken || !activeUserEmail) return;
    setIsSessionsLoading(true);
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/chat/sessions`, { headers: getHeaders() });
      if (res.ok) {
        const data = sortChatSessions(await res.json() as ChatSession[]);
        setChatSessions(prev => {
          const merged = mergeFetchedChatSessions(data, prev, activeSessionIdRef.current);
          writeCachedChatSessions(activeUserEmail, merged);
          return merged;
        });
        setActiveSession(prev => {
          if (prev) {
            const updatedActive = data.find(session => session.id === prev.id);
            if (updatedActive) return updatedActive;
            return prev;
          }
          return data.length > 0 ? data[0] : null;
        });
      } else {
        console.error("Failed to fetch sessions:", res.status, await res.text().catch(() => ""));
      }
    } catch (err) {
      console.error("Failed to fetch chat sessions:", err);
    } finally {
      setIsSessionsLoading(false);
    }
  }, [accessToken, activeUserEmail, getHeaders]);

  const updateLocalChatSession = useCallback((sessionId: string, patch: Partial<ChatSession>) => {
    setChatSessions(prev => {
      let changed = false;
      const next = prev.map(session => {
        if (session.id !== sessionId) return session;
        const updated = patchChatSession(session, patch);
        if (updated !== session) changed = true;
        return updated;
      });

      if (!changed) return prev;
      return patch.last_message_at ? sortChatSessions(next) : next;
    });

    setActiveSession(prev => {
      if (!prev || prev.id !== sessionId) return prev;
      return patchChatSession(prev, patch);
    });
  }, []);

  const upsertChatSession = useCallback((session: ChatSession) => {
    setChatSessions(prev => {
      const exists = prev.some(item => item.id === session.id);
      const next = exists
        ? prev.map(item => item.id === session.id ? session : item)
        : [session, ...prev];
      const sorted = sortChatSessions(next);
      writeCachedChatSessions(activeUserEmail, sorted);
      return sorted;
    });

    setActiveSession(prev => prev?.id === session.id ? session : prev);
  }, [activeUserEmail]);

  const refreshChatSession = useCallback(async (sessionId: string) => {
    if (!accessToken) return;
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/chat/sessions/${sessionId}`, { headers: getHeaders() });
      if (res.ok) {
        upsertChatSession(await res.json() as ChatSession);
      }
    } catch (err) {
      console.error("Failed to refresh chat session:", err);
    }
  }, [accessToken, getHeaders, upsertChatSession]);

  const touchChatSessionForMessage = useCallback((session: ChatSession) => {
    updateLocalChatSession(session.id, {
      last_message_at: new Date().toISOString(),
    });
  }, [updateLocalChatSession]);

  const renameChatSession = useCallback(async (sessionId: string, title: string) => {
    const cleanTitle = title.trim().replace(/\s+/g, " ");
    if (!cleanTitle) return;

    const previous = chatSessions.find(session => session.id === sessionId);
    updateLocalChatSession(sessionId, { title: cleanTitle });

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${sessionId}`, {
        method: "PATCH",
        headers: getHeaders(),
        body: JSON.stringify({ title: cleanTitle }),
      });
      if (!res.ok) throw new Error(`Rename failed with HTTP ${res.status}`);
      upsertChatSession(await res.json() as ChatSession);
    } catch (err) {
      console.error("Rename session failed:", err);
      if (previous) updateLocalChatSession(sessionId, { title: previous.title });
      alert("Failed to rename chat. Please try again.");
    }
  }, [chatSessions, getHeaders, updateLocalChatSession, upsertChatSession]);

  const addLocalMessages = useCallback((sessionId: string, messages: ChatMessage[]) => {
    setLocalMessagesBySession(prev => ({
      ...prev,
      [sessionId]: [...(prev[sessionId] || []), ...messages],
    }));
  }, []);

  const updateLocalMessage = useCallback((sessionId: string, messageId: string, patch: Partial<ChatMessage>) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      if (current.length === 0) return prev;
      return {
        ...prev,
        [sessionId]: current.map(message => message.id === messageId ? { ...message, ...patch } : message),
      };
    });
  }, []);

  const clearLocalRequestMessages = useCallback((sessionId: string, requestId: string) => {
    setLocalMessagesBySession(prev => {
      const current = prev[sessionId] || [];
      if (current.length === 0) return prev;
      const nextMessages = removeRequestMessages(current, requestId);
      if (nextMessages.length === current.length) return prev;

      const next = { ...prev };
      if (nextMessages.length > 0) next[sessionId] = nextMessages;
      else delete next[sessionId];
      return next;
    });
  }, []);

  const markSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.includes(sessionId) ? prev : [...prev, sessionId]);
  }, []);

  const unmarkSessionSending = useCallback((sessionId: string) => {
    setSendingSessionIds(prev => prev.filter(id => id !== sessionId));
  }, []);

  const createNewChat = useCallback(async (): Promise<ChatSession | null> => {
    if (!accessToken) return null;
    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ title: "New Chat" }),
      });
      if (res.ok) {
        const newSess = await res.json();
        upsertChatSession(newSess);
        setActiveSession(newSess);
        setActiveTab("chat");
        return newSess;
      } else {
        const errBody = await res.text().catch(() => "");
        console.error("Failed to create chat:", res.status, errBody);
        alert(`Failed to create new chat (HTTP ${res.status}). The API may be unavailable.`);
      }
    } catch (err) {
      console.error("Failed to create new chat:", err);
      alert("Failed to create new chat. Please check your connection.");
    }
    return null;
  }, [accessToken, getHeaders, upsertChatSession]);

  const fetchSessionMessages = useCallback(async (sid: string, showLoading = true) => {
    if (showLoading) setIsMessagesLoading(true);
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/chat/sessions/${sid}/messages`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json() as ChatMessage[];
        if (activeSessionIdRef.current === sid) {
          setChatMessages(mergeChatMessages(data, localMessagesBySessionRef.current[sid] || []));
        }
      }
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      if (showLoading && activeSessionIdRef.current === sid) {
        setIsMessagesLoading(false);
      }
    }
  }, [getHeaders]);

  const deleteChatSession = async (sid: string) => {
    if (!confirm("Archive/delete this chat session?")) return;
    try {
      await fetch(`${APIM_BASE_URL}/chat/sessions/${sid}`, { method: "DELETE", headers: getHeaders() });
      setChatSessions(prev => prev.filter(s => s.id !== sid));
      if (activeSession?.id === sid) setActiveSession(null);
      fetchChatSessions();
    } catch (err) {
      console.error("Delete session failed:", err);
    }
  };

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      if (!activeUserEmail) {
        setChatSessions([]);
        setActiveSession(null);
        setChatMessages([]);
        return;
      }

      const cached = readCachedChatSessions(activeUserEmail);
      setChatSessions(cached);
      setActiveSession(cached[0] || null);
      setChatMessages([]);
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [activeUserEmail]);

  useEffect(() => {
    if (!accessToken || !activeUserEmail) return;
    const timerId = window.setTimeout(() => {
      void fetchChatSessions();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [accessToken, activeUserEmail, fetchChatSessions]);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      if (activeSessionId && accessToken) {
        void fetchSessionMessages(activeSessionId);
      } else {
        setChatMessages([]);
        setIsMessagesLoading(false);
      }
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [activeSessionId, accessToken, fetchSessionMessages]);

  const markAssistantFailed = (sessionId: string, pendingMessageId: string, failure: ChatFailurePayload) => {
    const patch = { status: "failed" as const, error_message: JSON.stringify(failure) };
    updateLocalMessage(sessionId, pendingMessageId, patch);
    if (activeSessionIdRef.current === sessionId) {
      setChatMessages(prev => prev.map(m =>
        m.id === pendingMessageId ? { ...m, ...patch } : m
      ));
    }
  };

  const postChatMessage = async (
    session: ChatSession,
    content: string,
    artifactIds: string[],
    pendingMessageId: string,
    requestId: string,
  ) => {
    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), CHAT_REQUEST_TIMEOUT_MS);
    markSessionSending(session.id);

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${session.id}/messages/stream`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({
          content,
          artifact_ids: artifactIds,
        }),
        signal: abortController.signal,
      });

      if (res.ok) {
        if (!res.body) {
          throw new Error("Streaming response did not include a body");
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finalMessage: ChatMessage | null = null;
        let streamFailure: ChatFailurePayload | null = null;

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;

          for (const item of parsed.events) {
            if (item.event === "activity") {
              updateLocalMessage(session.id, pendingMessageId, appendActivityEvent(
                (localMessagesBySessionRef.current[session.id] || []).find(m => m.id === pendingMessageId) || {
                  id: pendingMessageId,
                  chat_session_id: session.id,
                  role: "assistant",
                  content: "",
                  created_at: new Date().toISOString(),
                  status: "pending",
                },
                item.data,
              ));
              if (activeSessionIdRef.current === session.id) {
                setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? appendActivityEvent(m, item.data) : m));
              }
            } else if (item.event === "message") {
              finalMessage = normalizeChatMessage(item.data as ChatMessage);
              finalMessage.status = "completed";
            } else if (item.event === "error") {
              streamFailure = chatFailureFromDetail(item.data, requestId, 502);
            }
          }
        }

        if (streamFailure) {
          markAssistantFailed(session.id, pendingMessageId, streamFailure);
        } else if (finalMessage) {
          clearLocalRequestMessages(session.id, requestId);
          if (activeSessionIdRef.current === session.id) {
            setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? finalMessage : m));
          }
        } else {
          markAssistantFailed(session.id, pendingMessageId, {
            requestId,
            errorType: "stream_error",
            errorMessage: "The AI service finished without returning a response.",
            technicalDetail: "Chat stream ended before a final message event was received.",
            httpStatus: 0,
          });
        }
      } else {
        markAssistantFailed(session.id, pendingMessageId, await chatFailureFromResponse(res, requestId));
      }
    } catch (err: unknown) {
      markAssistantFailed(session.id, pendingMessageId, chatFailureFromNetwork(err, requestId));
    } finally {
      clearTimeout(timeoutId);
      unmarkSessionSending(session.id);
      void refreshChatSession(session.id);
      if (activeSessionIdRef.current === session.id) {
        window.setTimeout(() => {
          if (activeSessionIdRef.current === session.id) void fetchSessionMessages(session.id, false);
        }, 750);
        window.setTimeout(() => {
          if (activeSessionIdRef.current === session.id) void fetchSessionMessages(session.id, false);
        }, 15_000);
      }
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken) return;
    if (activeSessionId && sendingSessionIds.includes(activeSessionId)) return;
    if (attachedFiles.some(file => file.uploading || file.error)) return;

    const content = chatInput;
    const attachedArtifacts: ChatAttachment[] = attachedFiles
      .filter(file => !file.uploading && !file.error && file.id)
      .map(file => file.artifact || {
        id: file.id as string,
        filename: file.file.name,
        mime_type: file.file.type || "application/octet-stream",
        artifact_type: "job-file",
      });
    const artifactIds = attachedArtifacts.map(artifact => artifact.id);

    const currentSess = activeSession || await createNewChat();
    if (!currentSess) return;
    setChatInput("");
    setAttachedFiles([]);
    touchChatSessionForMessage(currentSess);

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const localTurn: ChatMessage[] = [
      {
        id: crypto.randomUUID(),
        chat_session_id: currentSess.id,
        role: "user",
        content,
        created_at: createdAt,
        status: "completed",
        metadata_json: { request_id: requestId, attachments: attachedArtifacts },
        attachments: attachedArtifacts,
      },
      {
        id: pendingMsgId,
        chat_session_id: currentSess.id,
        role: "assistant",
        content: "",
        created_at: createdAt,
        status: "pending",
        metadata_json: pendingProgressMetadata(requestId, content, artifactIds.length, createdAt),
      },
    ];
    addLocalMessages(currentSess.id, localTurn);
    setChatMessages(prev => [...prev, ...localTurn]);

    await postChatMessage(currentSess, content, artifactIds, pendingMsgId, requestId);
  };

  const handleRetryMessage = async (messageId: string) => {
    if (!chatMessages.find(m => m.id === messageId) || !activeSession) return;

    const failedIdx = chatMessages.findIndex(m => m.id === messageId);
    const userMessage = [...chatMessages.slice(0, failedIdx)].reverse()
      .find(m => m.role === "user" && m.status === "completed");
    if (!userMessage) return;

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const pendingMessage: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: activeSession.id,
      role: "assistant",
      content: "",
      created_at: createdAt,
      status: "pending",
      metadata_json: pendingProgressMetadata(requestId, userMessage.content, 0, createdAt),
    };
    addLocalMessages(activeSession.id, [pendingMessage]);
    setChatMessages(prev => [
      ...prev.filter(m => m.id !== messageId),
      pendingMessage,
    ]);

    await postChatMessage(activeSession, userMessage.content, [], pendingMsgId, requestId);
  };

  const handleCopyMessage = (content: string) => {
    navigator.clipboard.writeText(content).catch(() => {});
  };

  const handleEditResend = async (originalMessageId: string, newContent: string) => {
    if (!activeSession || !newContent.trim()) return;

    const editIndex = chatMessages.findIndex(m => m.id === originalMessageId);
    if (editIndex === -1) return;

    const requestId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    const updatedUserMsg: ChatMessage = {
      ...chatMessages[editIndex],
      content: newContent,
    };
    const pendingMessage: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: activeSession.id,
      role: "assistant",
      content: "",
      created_at: createdAt,
      status: "pending",
      metadata_json: pendingProgressMetadata(requestId, newContent, 0, createdAt),
    };
    addLocalMessages(activeSession.id, [pendingMessage]);

    setChatMessages(prev => {
      const idx = prev.findIndex(m => m.id === originalMessageId);
      if (idx === -1) return prev;
      return [
        ...prev.slice(0, idx),
        updatedUserMsg,
        pendingMessage,
      ];
    });

    await postChatMessage(activeSession, newContent, [], pendingMsgId, requestId);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.currentTarget.value = "";
    if (files.length === 0) return;
    if (!accessToken) {
      alert("Please sign in again before uploading files.");
      return;
    }
    for (const file of files) {
      if (file.size > 15 * 1024 * 1024) { alert(`File ${file.name} exceeds 15MB limit.`); continue; }
      const tempId = crypto.randomUUID();
      setAttachedFiles(prev => [...prev, { file, id: tempId, uploading: true }]);
      const formData = new FormData();
      formData.append("file", file);
      formData.append("artifact_type", "job-file");
      formData.append("filename", file.name);
      formData.append("mime_type", file.type || "application/octet-stream");
      try {
        const r = await fetch(`${APIM_BASE_URL}/artifacts`, {
          method: "POST",
          headers: { Authorization: `Bearer ${accessToken}` },
          body: formData,
        });
        if (r.ok) {
          const art = await r.json() as ChatAttachment;
          const attachment: ChatAttachment = {
            id: art.id,
            filename: art.filename || file.name,
            mime_type: art.mime_type || file.type || "application/octet-stream",
            artifact_type: art.artifact_type || "job-file",
          };
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? {
            file,
            id: attachment.id,
            artifact: attachment,
            uploading: false,
          } : f));
        } else {
          const error = await uploadFailureFromResponse(r, `Upload failed with HTTP ${r.status}.`);
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? { ...f, uploading: false, error } : f));
        }
      } catch (err) {
        setAttachedFiles(prev => prev.map(f => f.id === tempId ? {
          ...f,
          uploading: false,
          error: `Upload failed: ${errorMessage(err)}`,
        } : f));
      }
    }
  };

  const handleRemoveFile = (id: string) => setAttachedFiles(prev => prev.filter(f => f.id !== id));

  const closeMobileSidebar = useCallback(() => {
    if (!isMobileViewport) return;
    setIsSidebarCollapsed(true);
    setIsProfileMenuOpen(false);
  }, [isMobileViewport]);

  const handleTabChange = (tab: ActiveTab) => {
    setActiveTab(tab);
    closeMobileSidebar();
  };

  if (inProgress !== InteractionStatus.None) {
    return (
      <div className="flex h-screen bg-canvas text-default items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-10 h-10 border-2 border-muted border-t-default rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-muted">Completing Microsoft sign-in...</p>
        </div>
      </div>
    );
  }

  if (!activeUser || (activeUser && !accessToken && authError)) {
    return (
      <LoginPage
        inProgress={inProgress}
        authError={authError}
        startupAuthError={startupAuthError}
        showDiagnostics={showDiagnostics}
        enableLocalMock={enableLocalMock}
        onSignIn={() => instance.loginRedirect(hintedLoginRequest)}
        onLocalMockSignIn={signInLocalMock}
        onToggleDiagnostics={() => setShowDiagnostics(!showDiagnostics)}
        instance={instance}
        loginRequest={hintedLoginRequest}
        accounts={accounts}
      />
    );
  }

  if (!accessToken) {
    return (
      <div className="flex h-screen bg-canvas text-default items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-10 h-10 border-2 border-muted border-t-default rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-muted">
            {isTokenLoading ? "Restoring Microsoft session..." : "Preparing Microsoft session..."}
          </p>
        </div>
      </div>
    );
  }

  const renderContent = () => {
    switch (activeTab) {
      case "chat":
        return (
          <ChatView
            activeSession={activeSession}
            chatMessages={chatMessages}
            chatInput={chatInput}
            attachedFiles={attachedFiles}
            voiceInterimTranscript={voiceInterimTranscript}
            voiceState={voiceState}
            isMessagesLoading={isMessagesLoading}
            isChatSending={isActiveChatSending}
            displayName={activeUser.displayName}
            onInputChange={setChatInput}
            onSend={handleSendMessage}
            onRemoveFile={handleRemoveFile}
            onTriggerUpload={() => fileInputRef.current?.click()}
            onToggleVoice={handleToggleVoice}
            onRetryMessage={handleRetryMessage}
            onCopyMessage={handleCopyMessage}
            onEditResend={handleEditResend}
          />
        );
      case "tasks":
        return <TasksPage accessToken={accessToken} />;
      case "artifacts":
        return <DocumentsPage accessToken={accessToken} />;
      case "connected-accounts":
        return <ConnectionsPage accessToken={accessToken} />;
      case "audit":
        return hasRole(["AIPlatform.Admin", "AIPlatform.Auditor"]) ? (
          <AuditPage accessToken={accessToken} />
        ) : null;
      case "admin":
        return hasRole(["AIPlatform.Admin", "AIPlatform.Developer"]) ? (
          <AdminPage accessToken={accessToken} />
        ) : null;
      case "settings":
        return hasRole(["AIPlatform.Admin", "AIPlatform.Developer"]) ? (
          <AiConfigView accessToken={accessToken} activeUser={activeUser} />
        ) : null;
      default:
        return null;
    }
  };

  return (
    <>
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileUpload}
        className="hidden"
        multiple
      />
      <AppShell
        activeTab={activeTab}
        chatSessions={chatSessions}
        activeSession={activeSession}
        activeUser={activeUser}
        isSessionsLoading={isSessionsLoading}
        isSidebarCollapsed={isSidebarCollapsed}
        isProfileMenuOpen={isProfileMenuOpen}
        onTabChange={handleTabChange}
        onNewChat={() => {
          closeMobileSidebar();
          void createNewChat();
        }}
        onSelectSession={(sess) => {
          setActiveSession(sess);
          setActiveTab("chat");
          closeMobileSidebar();
        }}
        onDeleteSession={deleteChatSession}
        onRenameSession={renameChatSession}
        onToggleCollapse={(collapsed) => {
          setIsSidebarCollapsed(collapsed);
          if (collapsed) setIsProfileMenuOpen(false);
        }}
        onToggleProfileMenu={() => setIsProfileMenuOpen(!isProfileMenuOpen)}
        onSignOut={signOut}
        hasRole={hasRole}
      >
        {renderContent()}
      </AppShell>
    </>
  );
}
