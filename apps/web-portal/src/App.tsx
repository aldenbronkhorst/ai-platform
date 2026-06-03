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

interface ChatFailurePayload {
  requestId: string;
  errorType: string;
  errorMessage: string;
  technicalDetail: string;
  httpStatus: number;
}

async function chatFailureFromResponse(res: Response, requestId: string): Promise<ChatFailurePayload> {
  const body = await res.json().catch(() => null);
  const respRequestId = res.headers.get("X-Request-ID") || requestId;
  if (body && body.detail) {
    const detail = body.detail;
    return {
      requestId: respRequestId,
      errorType: detail.error_type || "server_error",
      errorMessage: detail.error_message || `Server returned ${res.status}`,
      technicalDetail: detail.technical_detail || "",
      httpStatus: res.status,
    };
  }
  return {
    requestId: respRequestId,
    errorType: "server_error",
    errorMessage: `Server returned ${res.status}`,
    technicalDetail: typeof body === "string" ? body : JSON.stringify(body),
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
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [isChatSending, setIsChatSending] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleTranscript = useCallback((transcript: string) => {
    setChatInput(prev => (prev ? prev + " " + transcript : transcript));
  }, []);
  const { voiceState, toggleVoice: handleToggleVoice } = useSpeechRecognition(handleTranscript);
  const activeUserEmail = activeUser?.email || "";

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
      if (res.ok) setChatMessages(await res.json());
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      setIsMessagesLoading(false);
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
      if (activeSession && accessToken) {
        void fetchSessionMessages(activeSession.id);
      } else {
        setChatMessages([]);
      }
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [activeSession, accessToken, fetchSessionMessages]);

  const markAssistantFailed = (pendingMessageId: string, failure: ChatFailurePayload) => {
    setChatMessages(prev => prev.map(m =>
      m.id === pendingMessageId
        ? { ...m, status: "failed" as const, error_message: JSON.stringify(failure) }
        : m
    ));
  };

  const postChatMessage = async (
    session: ChatSession,
    content: string,
    artifactIds: string[],
    pendingMessageId: string,
  ) => {
    const requestId = crypto.randomUUID();
    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), CHAT_REQUEST_TIMEOUT_MS);
    setIsChatSending(true);

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
        const botMsg: ChatMessage = await res.json();
        botMsg.status = "completed";
        setChatMessages(prev => prev.map(m => m.id === pendingMessageId ? botMsg : m));
        fetchChatSessions();
      } else {
        markAssistantFailed(pendingMessageId, await chatFailureFromResponse(res, requestId));
      }
    } catch (err: unknown) {
      markAssistantFailed(pendingMessageId, chatFailureFromNetwork(err, requestId));
    } finally {
      clearTimeout(timeoutId);
      setIsChatSending(false);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken) return;

    const content = chatInput;
    const artifactIds = attachedFiles.filter(f => !f.uploading && f.id).map(f => f.id as string);
    setChatInput("");
    setAttachedFiles([]);

    const currentSess = activeSession || await createNewChat();
    if (!currentSess) return;

    const pendingMsgId = crypto.randomUUID();
    setChatMessages(prev => [
      ...prev,
      {
        id: crypto.randomUUID(),
        chat_session_id: currentSess.id,
        role: "user",
        content,
        created_at: new Date().toISOString(),
        status: "completed",
      },
      {
        id: pendingMsgId,
        chat_session_id: currentSess.id,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        status: "pending",
      },
    ]);

    await postChatMessage(currentSess, content, artifactIds, pendingMsgId);
  };

  const handleRetryMessage = async (messageId: string) => {
    if (!chatMessages.find(m => m.id === messageId) || !activeSession) return;

    const failedIdx = chatMessages.findIndex(m => m.id === messageId);
    const userMessage = [...chatMessages.slice(0, failedIdx)].reverse()
      .find(m => m.role === "user" && m.status === "completed");
    if (!userMessage) return;

    const pendingMsgId = crypto.randomUUID();
    setChatMessages(prev => [
      ...prev.filter(m => m.id !== messageId),
      {
        id: pendingMsgId,
        chat_session_id: activeSession.id,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        status: "pending",
      },
    ]);

    await postChatMessage(activeSession, userMessage.content, [], pendingMsgId);
  };

  const handleCopyMessage = (content: string) => {
    navigator.clipboard.writeText(content).catch(() => {});
  };

  const handleEditResend = async (originalMessageId: string, newContent: string) => {
    if (!activeSession || !newContent.trim()) return;

    const editIndex = chatMessages.findIndex(m => m.id === originalMessageId);
    if (editIndex === -1) return;

    const pendingMsgId = crypto.randomUUID();
    const updatedUserMsg: ChatMessage = {
      ...chatMessages[editIndex],
      content: newContent,
    };

    setChatMessages(prev => {
      const idx = prev.findIndex(m => m.id === originalMessageId);
      if (idx === -1) return prev;
      return [
        ...prev.slice(0, idx),
        updatedUserMsg,
        {
          id: pendingMsgId,
          chat_session_id: activeSession.id,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
          status: "pending",
        },
      ];
    });

    await postChatMessage(activeSession, newContent, [], pendingMsgId);
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

  const handleTabChange = (tab: ActiveTab) => {
    setActiveTab(tab);
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
            isChatSending={isChatSending}
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
        onNewChat={() => createNewChat()}
        onSelectSession={(sess) => {
          setActiveSession(sess);
          setActiveTab("chat");
        }}
        onDeleteSession={deleteChatSession}
        onToggleCollapse={setIsSidebarCollapsed}
        onToggleProfileMenu={() => setIsProfileMenuOpen(!isProfileMenuOpen)}
        onSignOut={signOut}
        hasRole={hasRole}
      >
        {renderContent()}
      </AppShell>
    </>
  );
}
