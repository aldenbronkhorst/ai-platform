import { useState, useEffect, useRef, useCallback } from "react";
import { useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";
import type { UserProfile, ChatSession, ChatMessage } from "./types";
import type { VoiceState, AttachedFile } from "./types";
import { AppShell } from "./components/layout/AppShell";
import { LoginPage } from "./components/auth/LoginPage";
import { ChatView } from "./components/chat/ChatView";
import { WorkflowsPage } from "./pages/WorkflowsPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { TasksPage } from "./pages/TasksPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { AuditPage } from "./pages/AuditPage";
import { AiConfigView } from "./AiConfigView";
import { AdminPage } from "./pages/AdminPage";
import type { ActiveTab } from "./types";

const APIM_BASE_URL = import.meta.env.VITE_APIM_BASE_URL || "https://apim-ai-platform-prod-san-001.azure-api.net";

const ENABLE_LOCAL_MOCK = 
  import.meta.env.VITE_ENABLE_LOCAL_MOCK_AUTH === "true" && 
  (typeof window !== "undefined" && 
    (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"));

export default function App({ startupAuthError }: { startupAuthError: string | null }) {
  const { instance, accounts, inProgress } = useMsal();

  const [activeTab, setActiveTab] = useState<ActiveTab>("workflows");
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [localMockAuthenticated, setLocalMockAuthenticated] = useState(false);
  const [localMockUser, setLocalMockUser] = useState<UserProfile | null>(null);
  const [activeUser, setActiveUser] = useState<UserProfile | null>(null);
  const [accessToken, setAccessToken] = useState("");

  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [isSessionsLoading, setIsSessionsLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [isChatSending, setIsChatSending] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const recognitionRef = useRef<any>(null);

  const getHeaders = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  }), [accessToken]);

  const hasRole = (allowedRoles: string[]) => {
    if (!activeUser) return false;
    if (activeUser.roles.includes("AIPlatform.Admin")) return true;
    return activeUser.roles.some(r => allowedRoles.includes(r));
  };

  useEffect(() => {
    const activeAccount = instance.getActiveAccount() || (accounts.length > 0 ? accounts[0] : null);
    if (activeAccount) {
      const idTokenClaims = activeAccount.idTokenClaims as any;
      const roles = idTokenClaims?.roles || ["AIPlatform.User"];
      setActiveUser({
        email: activeAccount.username,
        displayName: activeAccount.name || activeAccount.username,
        roles,
      });
      setAuthError(null);
      instance.acquireTokenSilent({ ...loginRequest, account: activeAccount })
        .then(response => setAccessToken(response.accessToken))
        .catch(() => setAuthError("Token acquisition failed. Please sign in again."));
      const refreshInterval = setInterval(() => {
        instance.acquireTokenSilent({ ...loginRequest, account: activeAccount })
          .then(response => setAccessToken(response.accessToken))
          .catch(() => {});
      }, 30 * 60 * 1000);
      return () => clearInterval(refreshInterval);
    } else if (ENABLE_LOCAL_MOCK && localMockAuthenticated && localMockUser) {
      setActiveUser(localMockUser);
      setAccessToken("mock-local-token");
      setAuthError(null);
    } else {
      setActiveUser(null);
      setAccessToken("");
    }
  }, [accounts, localMockAuthenticated, localMockUser, instance]);

  useEffect(() => {
    if (accessToken) {
      fetchChatSessions();
    }
  }, [accessToken, fetchChatSessions]);

  useEffect(() => {
    if (activeSession && accessToken) {
      fetchSessionMessages(activeSession.id);
    } else {
      setChatMessages([]);
    }
  }, [activeSession, accessToken]);

  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setVoiceState("unsupported");
    } else {
      const rec = new SpeechRecognition();
      rec.continuous = false;
      rec.interimResults = false;
      rec.lang = "en-US";
      rec.onstart = () => setVoiceState("listening");
      rec.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        setChatInput(prev => (prev ? prev + " " + transcript : transcript));
        setVoiceState("processing");
      };
      rec.onerror = (e: any) => {
        setVoiceState(e.error === "not-allowed" ? "denied" : "idle");
      };
      rec.onend = () => {
        setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
      };
      recognitionRef.current = rec;
    }
    return () => {
      if (recognitionRef.current) {
        try { recognitionRef.current.abort(); } catch {}
        recognitionRef.current = null;
      }
    };
  }, []);

  const fetchChatSessions = useCallback(async () => {
    if (!accessToken) return;
    setIsSessionsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setChatSessions(data);
        setActiveSession(prev => {
          if (prev) return prev;
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
  }, [accessToken, getHeaders]);

  const createNewChat = useCallback(async (workflowContext?: string): Promise<ChatSession | null> => {
    if (!accessToken) return null;
    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ title: "New Chat", workflow_context: workflowContext }),
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

  const fetchSessionMessages = async (sid: string) => {
    setIsMessagesLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${sid}/messages`, { headers: getHeaders() });
      if (res.ok) setChatMessages(await res.json());
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      setIsMessagesLoading(false);
    }
  };

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

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!chatInput.trim() && attachedFiles.length === 0) || !accessToken) return;

    const content = chatInput;
    setChatInput("");
    setIsChatSending(true);

    const artIds = attachedFiles.filter(f => !f.uploading && f.id).map(f => f.id as string);
    setAttachedFiles([]);

    let currentSess = activeSession;
    if (!currentSess) {
      currentSess = await createNewChat();
      if (!currentSess) {
        setIsChatSending(false);
        return;
      }
    }

    const requestId = crypto.randomUUID();
    const userMsgId = crypto.randomUUID();
    const pendingMsgId = crypto.randomUUID();

    const tempUserMsg: ChatMessage = {
      id: userMsgId,
      chat_session_id: currentSess.id,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      status: "completed",
    };

    const pendingAssistantMsg: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: currentSess.id,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      status: "pending",
    };

    setChatMessages(prev => [...prev, tempUserMsg, pendingAssistantMsg]);

    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), 180_000);

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${currentSess.id}/messages`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({ content, artifact_ids: artIds, workflow_context: currentSess.workflow_context }),
        signal: abortController.signal,
      });
      if (res.ok) {
        const botMsg: ChatMessage = await res.json();
        botMsg.status = "completed";
        setChatMessages(prev => prev.map(m => m.id === pendingMsgId ? botMsg : m));
        fetchChatSessions();

        // Non-blocking memory extraction
        fetch(`${APIM_BASE_URL}/memories/extract?conversation_id=${currentSess.id}`, {
          method: "POST",
          headers: getHeaders(),
        }).catch(() => {});
      } else {
        const body = await res.json().catch(() => null);
        const respRequestId = res.headers.get("X-Request-ID") || requestId;
        if (body && body.detail) {
          const d = body.detail;
          setChatMessages(prev => prev.map(m =>
            m.id === pendingMsgId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: d.error_type || "server_error",
                    errorMessage: d.error_message || `Server returned ${res.status}`,
                    technicalDetail: d.technical_detail || "",
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        } else {
          setChatMessages(prev => prev.map(m =>
            m.id === pendingMsgId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: "server_error",
                    errorMessage: `Server returned ${res.status}`,
                    technicalDetail: typeof body === "string" ? body : JSON.stringify(body),
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        }
      }
    } catch (err: any) {
      const isTimeout = err?.name === "AbortError";
      setChatMessages(prev => prev.map(m =>
        m.id === pendingMsgId
          ? {
              ...m,
              status: "failed" as const,
              error_message: JSON.stringify({
                requestId,
                errorType: isTimeout ? "timeout" : "network",
                errorMessage: isTimeout
                  ? "The request took too long to complete. Please try again or narrow the question."
                  : "The AI service could not be reached. Please check your connection and try again.",
                technicalDetail: isTimeout
                  ? "Request timed out after 180 seconds"
                  : err instanceof Error ? err.message : "Network error",
                httpStatus: 0,
              }),
            }
          : m
      ));
    } finally {
      clearTimeout(timeoutId);
      setIsChatSending(false);
    }
  };

  const handleRetryMessage = async (messageId: string) => {
    const msg = chatMessages.find(m => m.id === messageId);
    if (!msg || !activeSession) return;

    setIsChatSending(true);
    const currentSess = activeSession;

    const failedIdx = chatMessages.findIndex(m => m.id === messageId);
    let userContent = "";
    for (let i = failedIdx - 1; i >= 0; i--) {
      if (chatMessages[i].role === "user" && chatMessages[i].status === "completed") {
        userContent = chatMessages[i].content;
        break;
      }
    }

    const newPendingId = crypto.randomUUID();
    const pendingAssistantMsg: ChatMessage = {
      id: newPendingId,
      chat_session_id: activeSession.id,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      status: "pending",
    };

    setChatMessages(prev => [
      ...prev.filter(m => m.id !== messageId),
      pendingAssistantMsg,
    ]);

    const requestId = crypto.randomUUID();
    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), 180_000);

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${currentSess.id}/messages`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({
          content: userContent,
          artifact_ids: [],
          workflow_context: currentSess.workflow_context,
        }),
        signal: abortController.signal,
      });
      if (res.ok) {
        const botMsg: ChatMessage = await res.json();
        botMsg.status = "completed";
        setChatMessages(prev => prev.map(m => m.id === newPendingId ? botMsg : m));
        fetchChatSessions();
      } else {
        const body = await res.json().catch(() => null);
        const respRequestId = res.headers.get("X-Request-ID") || requestId;
        if (body && body.detail) {
          const d = body.detail;
          setChatMessages(prev => prev.map(m =>
            m.id === newPendingId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: d.error_type || "server_error",
                    errorMessage: d.error_message || `Server returned ${res.status}`,
                    technicalDetail: d.technical_detail || "",
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        } else {
          setChatMessages(prev => prev.map(m =>
            m.id === newPendingId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: "server_error",
                    errorMessage: `Server returned ${res.status}`,
                    technicalDetail: typeof body === "string" ? body : JSON.stringify(body),
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        }
      }
    } catch (err: any) {
      const isTimeout = err?.name === "AbortError";
      setChatMessages(prev => prev.map(m =>
        m.id === newPendingId
          ? {
              ...m,
              status: "failed" as const,
              error_message: JSON.stringify({
                requestId,
                errorType: isTimeout ? "timeout" : "network",
                errorMessage: isTimeout
                  ? "The request took too long to complete. Please try again or narrow the question."
                  : "The AI service could not be reached. Please check your connection and try again.",
                technicalDetail: isTimeout
                  ? "Request timed out after 180 seconds"
                  : err instanceof Error ? err.message : "Network error",
                httpStatus: 0,
              }),
            }
          : m
      ));
    } finally {
      clearTimeout(timeoutId);
      setIsChatSending(false);
    }
  };

  const handleCopyMessage = (content: string) => {
    navigator.clipboard.writeText(content).catch(() => {});
  };

  const handleEditResend = async (originalMessageId: string, newContent: string) => {
    if (!activeSession || !newContent.trim()) return;

    const currentSess = activeSession;

    const editIndex = chatMessages.findIndex(m => m.id === originalMessageId);
    if (editIndex === -1) return;

    setIsChatSending(true);
    const beforeEdit = chatMessages.slice(0, editIndex);

    const updatedUserMsg: ChatMessage = {
      ...chatMessages[editIndex],
      content: newContent,
    };

    const pendingMsgId = crypto.randomUUID();
    const pendingAssistantMsg: ChatMessage = {
      id: pendingMsgId,
      chat_session_id: currentSess.id,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      status: "pending",
    };

    setChatMessages(prev => {
      const idx = prev.findIndex(m => m.id === originalMessageId);
      if (idx === -1) return prev;
      return [...prev.slice(0, idx), updatedUserMsg, pendingAssistantMsg];
    });

    const requestId = crypto.randomUUID();
    const abortController = new AbortController();
    const timeoutId = setTimeout(() => abortController.abort(), 180_000);

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${currentSess.id}/messages`, {
        method: "POST",
        headers: { ...getHeaders(), "X-Request-ID": requestId },
        body: JSON.stringify({
          content: newContent,
          artifact_ids: [],
          workflow_context: currentSess.workflow_context,
        }),
        signal: abortController.signal,
      });
      if (res.ok) {
        const botMsg: ChatMessage = await res.json();
        botMsg.status = "completed";
        setChatMessages(prev => prev.map(m => m.id === pendingMsgId ? botMsg : m));
        fetchChatSessions();
      } else {
        const body = await res.json().catch(() => null);
        const respRequestId = res.headers.get("X-Request-ID") || requestId;
        if (body && body.detail) {
          const d = body.detail;
          setChatMessages(prev => prev.map(m =>
            m.id === pendingMsgId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: d.error_type || "server_error",
                    errorMessage: d.error_message || `Server returned ${res.status}`,
                    technicalDetail: d.technical_detail || "",
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        } else {
          setChatMessages(prev => prev.map(m =>
            m.id === pendingMsgId
              ? {
                  ...m,
                  status: "failed" as const,
                  error_message: JSON.stringify({
                    requestId: respRequestId,
                    errorType: "server_error",
                    errorMessage: `Server returned ${res.status}`,
                    technicalDetail: typeof body === "string" ? body : JSON.stringify(body),
                    httpStatus: res.status,
                  }),
                }
              : m
          ));
        }
      }
    } catch (err: any) {
      const isTimeout = err?.name === "AbortError";
      setChatMessages(prev => prev.map(m =>
        m.id === pendingMsgId
          ? {
              ...m,
              status: "failed" as const,
              error_message: JSON.stringify({
                requestId,
                errorType: isTimeout ? "timeout" : "network",
                errorMessage: isTimeout
                  ? "The request took too long to complete. Please try again or narrow the question."
                  : "The AI service could not be reached. Please check your connection and try again.",
                technicalDetail: isTimeout
                  ? "Request timed out after 180 seconds"
                  : err instanceof Error ? err.message : "Network error",
                httpStatus: 0,
              }),
            }
          : m
      ));
    } finally {
      clearTimeout(timeoutId);
      setIsChatSending(false);
    }
  };

  const handleSuggestionClick = (prompt: string) => {
    setChatInput(prompt);
    if (!activeSession) {
      createNewChat();
      setActiveTab("chat");
    }
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

  const handleSignOut = () => {
    if (localMockAuthenticated) {
      setLocalMockAuthenticated(false);
      setLocalMockUser(null);
    } else {
      instance.logoutRedirect();
    }
  };

  const handleToggleVoice = () => {
    if (voiceState === "unsupported") return;
    if (voiceState === "listening") {
      recognitionRef.current?.stop();
    } else {
      try { recognitionRef.current?.start(); } catch {}
    }
  };

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
        enableLocalMock={ENABLE_LOCAL_MOCK}
        onSignIn={() => instance.loginRedirect(loginRequest)}
        onLocalMockSignIn={() => {
          setLocalMockUser({
            email: "alden@lotslotsmore.com",
            displayName: "Alden Bronkhorst (Local Mock)",
            roles: ["AIPlatform.Admin", "AIPlatform.User", "AIPlatform.Developer", "AIPlatform.Auditor"],
          });
          setLocalMockAuthenticated(true);
        }}
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
            onSuggestionClick={handleSuggestionClick}
            onCopyMessage={handleCopyMessage}
            onEditResend={handleEditResend}
          />
        );
      case "workflows":
        return (
          <WorkflowsPage
            accessToken={accessToken}
            onLaunchChat={(workflowId) => createNewChat(workflowId)}
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
        onSignOut={handleSignOut}
        hasRole={hasRole}
      >
        {renderContent()}
      </AppShell>
    </>
  );
}
