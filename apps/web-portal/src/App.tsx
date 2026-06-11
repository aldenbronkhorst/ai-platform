import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "./authConfig";
import { loginRequestWithAuthHint, readStoredAuthHint } from "./authSession";
import { useChatController } from "./chat/useChatController";
import { mobileViewportMatches } from "./chat/runtime";
import { AppShell } from "./components/layout/AppShell";
import { LoginPage } from "./components/auth/LoginPage";
import { ChatView } from "./components/chat/ChatView";
import type { ActiveTab } from "./types";
import { usePortalAuth } from "./hooks/usePortalAuth";

const ConnectionsPage = lazy(() =>
  import("./pages/ConnectionsPage").then(module => ({ default: module.ConnectionsPage }))
);
const AdminPage = lazy(() =>
  import("./pages/AdminPage").then(module => ({ default: module.AdminPage }))
);

function PageLoader() {
  return (
    <div className="flex min-h-[240px] items-center justify-center" aria-label="Loading page" role="status">
      <div className="h-8 w-8 rounded-full border-2 border-muted border-t-default animate-spin" />
    </div>
  );
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

  const activeUserEmail = activeUser?.email || "";
  const openChatTab = useCallback(() => setActiveTab("chat"), []);
  const {
    activeSession,
    attachedFiles,
    chatInput,
    chatMessages,
    chatSessions,
    createNewChat,
    deleteChatSession,
    fileInputRef,
    handleCopyMessage,
    handleEditResend,
    handleFileUpload,
    handleRemoveFile,
    handleRetryMessage,
    handleSendMessage,
    handleToggleVoice,
    isActiveChatSending,
    isMessagesLoading,
    isSessionsLoading,
    renameChatSession,
    selectSession,
    setChatInput,
    voiceInterimTranscript,
    voiceState,
  } = useChatController({
    accessToken,
    activeUserEmail,
    onOpenChat: openChatTab,
  });

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

  const closeMobileSidebar = useCallback(() => {
    if (!isMobileViewport) return;
    setIsSidebarCollapsed(true);
    setIsProfileMenuOpen(false);
  }, [isMobileViewport]);

  const handleTabChange = (tab: ActiveTab) => {
    setActiveTab(tab);
    closeMobileSidebar();
  };

  const hasRole = (allowedRoles: string[]) => {
    if (!activeUser) return false;
    if (activeUser.roles.includes("AIPlatform.Admin")) return true;
    return activeUser.roles.some(r => allowedRoles.includes(r));
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
      case "connected-accounts":
        return (
          <Suspense fallback={<PageLoader />}>
            <ConnectionsPage accessToken={accessToken} />
          </Suspense>
        );
      case "admin":
        return hasRole(["AIPlatform.Admin", "AIPlatform.Developer"]) ? (
          <Suspense fallback={<PageLoader />}>
            <AdminPage accessToken={accessToken} />
          </Suspense>
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
        onSelectSession={(session) => {
          selectSession(session);
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
