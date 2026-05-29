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
  const [expandedTraceMsgs, setExpandedTraceMsgs] = useState<Record<string, boolean>>({});
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
  }, [accessToken]);

  useEffect(() => {
    if (activeSession && accessToken) {
      fetchSessionMessages(activeSession.id);
    } else {
      setChatMessages([]);
    }
  }, [activeSession]);

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
  }, []);

  const fetchChatSessions = async () => {
    if (!accessToken) return;
    setIsSessionsLoading(true);
    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setChatSessions(data);
        if (data.length > 0 && !activeSession) setActiveSession(data[0]);
      }
    } catch (err) {
      console.error("Failed to fetch chat sessions:", err);
    } finally {
      setIsSessionsLoading(false);
    }
  };

  const createNewChat = async (workflowContext?: string): Promise<ChatSession | null> => {
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
      }
    } catch (err) {
      console.error("Failed to create new chat:", err);
    }
    return null;
  };

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

    const tempUserMsg: ChatMessage = {
      id: Math.random().toString(),
      chat_session_id: currentSess.id,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    setChatMessages(prev => [...prev, tempUserMsg]);

    try {
      const res = await fetch(`${APIM_BASE_URL}/chat/sessions/${currentSess.id}/messages`, {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ content, artifact_ids: artIds, workflow_context: currentSess.workflow_context }),
      });
      if (res.ok) {
        const botMsg = await res.json();
        setChatMessages(prev => [...prev, botMsg]);
        fetchChatSessions();
      }
    } catch (err) {
      console.error("Failed to send message:", err);
    } finally {
      setIsChatSending(false);
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
            expandedTraceMsgs={expandedTraceMsgs}
            displayName={activeUser.displayName}
            onInputChange={setChatInput}
            onSend={handleSendMessage}
            onFileUpload={handleFileUpload}
            onRemoveFile={handleRemoveFile}
            onTriggerUpload={() => fileInputRef.current?.click()}
            onToggleVoice={handleToggleVoice}
            onToggleTrace={(id) => setExpandedTraceMsgs(prev => ({ ...prev, [id]: !prev[id] }))}
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
