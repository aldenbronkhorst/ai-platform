import { useState, useEffect, useRef, useCallback } from "react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";
import type { ChatSession, ChatMessage, AttachedFile } from "./types";
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

function chatTitleAfterMessage(currentTitle: string, content: string) {
  if (currentTitle !== "New Chat") return currentTitle;
  const cleanContent = content.trim();
  if (!cleanContent) return currentTitle;
  return cleanContent.slice(0, 35) + (cleanContent.length > 35 ? "..." : "");
}

function chatSessionTime(session: ChatSession) {
  const time = Date.parse(session.last_message_at);
  return Number.isNaN(time) ? 0 : time;
}

function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((a, b) => chatSessionTime(b) - chatSessionTime(a));
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

async function chatFailureFromResponse(res: Response, requestId: string): Promise<ChatFailurePayload> {
  const body = await res.json().catch(() => null);
  const respRequestId = res.headers.get("X-Request-ID") || requestId;
  if (body && body.detail) {
    const detail = body.detail;
    return {
      requestId: respRequestId,
      errorType: typeof detail.error_type === "string" ? detail.error_type : "server_error",
      errorMessage: detailString(detail.error_message, `Server returned ${res.status}`),
      technicalDetail: detailString(detail.technical_detail),
      httpStatus: res.status,
    };
  }
  return {
    requestId: respRequestId,
    errorType: "server_error",
    errorMessage: `Server returned ${res.status}`,
    technicalDetail: detailString(body),
    httpStatus: res.status,
  };
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
    signInLocalMock,
    signOut,
  } = usePortalAuth();

  const [activeTab, setActiveTab] = useState<ActiveTab>("chat");
  const [isMobileViewport, setIsMobileViewport] = useState(mobileViewportMatches);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(mobileViewportMatches);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

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
  const { voiceState, toggleVoice: handleToggleVoice } = useSpeechRecognition(handleTranscript);
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
    if (!accessToken) return;
    setIsSessionsLoading(true);
    try {
      const res = await fetchWithTimeout(`${APIM_BASE_URL}/chat/sessions`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json() as ChatSession[];
        setChatSessions(data);
        writeCachedChatSessions(activeUserEmail, data);
        setActiveSession(prev => {
          if (prev && data.some(session => session.id === prev.id)) return prev;
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

  const touchChatSessionForMessage = useCallback((session: ChatSession, content: string) => {
    updateLocalChatSession(session.id, {
      title: chatTitleAfterMessage(session.title, content),
      last_message_at: new Date().toISOString(),
    });
  }, [updateLocalChatSession]);

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
        setChatSessions(prev => [newSess, ...prev]);
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
  }, [accessToken, getHeaders]);

  const fetchSessionMessages = useCallback(async (sid: string) => {
    setIsMessagesLoading(true);
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
      if (activeSessionIdRef.current === sid) {
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
    if (!accessToken) return;
    const timerId = window.setTimeout(() => {
      const cached = readCachedChatSessions(activeUserEmail);
      if (cached.length > 0) {
        setChatSessions(cached);
        setActiveSession(prev => prev || cached[0]);
      }
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
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${session.id}/messages`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({
          content,
          artifact_ids: artifactIds,
        }),
        signal: abortController.signal,
      });

      if (res.ok) {
        const botMsg = normalizeChatMessage(await res.json() as ChatMessage);
        botMsg.status = "completed";
        clearLocalRequestMessages(session.id, requestId);
        if (activeSessionIdRef.current === session.id) {
          setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? botMsg : m));
        }
      } else {
        markAssistantFailed(session.id, pendingMessageId, await chatFailureFromResponse(res, requestId));
      }
    } catch (err: unknown) {
      markAssistantFailed(session.id, pendingMessageId, chatFailureFromNetwork(err, requestId));
    } finally {
      clearTimeout(timeoutId);
      unmarkSessionSending(session.id);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken) return;
    if (activeSessionId && sendingSessionIds.includes(activeSessionId)) return;

    const content = chatInput;
    const artifactIds = attachedFiles.filter(f => !f.uploading && f.id).map(f => f.id as string);
    setChatInput("");
    setAttachedFiles([]);

    const currentSess = activeSession || await createNewChat();
    if (!currentSess) return;
    touchChatSessionForMessage(currentSess, content);

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
        metadata_json: { request_id: requestId },
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
    const files = e.target.files;
    if (!files || !accessToken) return;
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (file.size > 15 * 1024 * 1024) { alert(`File ${file.name} exceeds 15MB limit.`); continue; }
      const tempId = Math.random().toString();
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
          const art = await r.json();
          setAttachedFiles(prev => prev.map(f => f.id === tempId ? { file, id: art.id, uploading: false } : f));
        } else {
          setAttachedFiles(prev => prev.filter(f => f.id !== tempId));
        }
      } catch {
        setAttachedFiles(prev => prev.filter(f => f.id !== tempId));
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

  if (!activeUser) {
    return (
      <LoginPage
        inProgress={inProgress}
        authError={authError}
        startupAuthError={startupAuthError}
        showDiagnostics={showDiagnostics}
        enableLocalMock={enableLocalMock}
        onSignIn={() => instance.loginRedirect(loginRequest)}
        onLocalMockSignIn={signInLocalMock}
        onToggleDiagnostics={() => setShowDiagnostics(!showDiagnostics)}
        instance={instance}
        loginRequest={loginRequest}
        accounts={accounts}
      />
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
            voiceState={voiceState}
            isMessagesLoading={isMessagesLoading}
            isChatSending={isActiveChatSending}
            displayName={activeUser.displayName}
            onInputChange={setChatInput}
            onSend={handleSendMessage}
            onFileUpload={handleFileUpload}
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
      <input type="file" ref={fileInputRef} onChange={handleFileUpload} className="hidden" multiple />
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
