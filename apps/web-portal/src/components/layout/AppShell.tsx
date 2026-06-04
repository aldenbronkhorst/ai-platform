import type { ReactNode } from "react";
import { SidebarPanel } from "./SidebarPanel";
import { MainHeader } from "./MainHeader";
import type { ChatSession, UserProfile, ActiveTab } from "../../types";

interface AppShellProps {
  activeTab: ActiveTab;
  chatSessions: ChatSession[];
  activeSession: ChatSession | null;
  activeUser: UserProfile | null;
  isSessionsLoading: boolean;
  isSidebarCollapsed: boolean;
  isProfileMenuOpen: boolean;
  children: ReactNode;
  onTabChange: (tab: ActiveTab) => void;
  onNewChat: () => void;
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onToggleCollapse: (collapsed: boolean) => void;
  onToggleProfileMenu: () => void;
  onSignOut: () => void;
  hasRole: (roles: string[]) => boolean;
}

export function AppShell({
  activeTab,
  chatSessions,
  activeSession,
  activeUser,
  isSessionsLoading,
  isSidebarCollapsed,
  isProfileMenuOpen,
  children,
  onTabChange,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
  onToggleCollapse,
  onToggleProfileMenu,
  onSignOut,
  hasRole,
}: AppShellProps) {
  return (
    <div className="fixed inset-0 flex h-[100dvh] w-screen bg-canvas text-default antialiased overflow-hidden overscroll-none">
      <div className="flex h-full min-h-0 w-full gap-3 overflow-hidden p-3">
        <SidebarPanel
          activeTab={activeTab}
          chatSessions={chatSessions}
          activeSession={activeSession}
          activeUser={activeUser}
          isSessionsLoading={isSessionsLoading}
          isSidebarCollapsed={isSidebarCollapsed}
          isProfileMenuOpen={isProfileMenuOpen}
          onTabChange={onTabChange}
          onNewChat={onNewChat}
          onSelectSession={onSelectSession}
          onDeleteSession={onDeleteSession}
          onRenameSession={onRenameSession}
          onToggleCollapse={onToggleCollapse}
          onToggleProfileMenu={onToggleProfileMenu}
          onSignOut={onSignOut}
          hasRole={hasRole}
        />

        <main className="flex-1 h-full min-h-0 flex flex-col overflow-hidden min-w-0">
          <MainHeader
            activeTab={activeTab}
            activeSession={activeSession}
            isSidebarCollapsed={isSidebarCollapsed}
          />
          <section className={`flex-1 min-h-0 overscroll-contain ${activeTab === "chat" ? "overflow-hidden" : "overflow-y-auto p-4 sm:p-6"}`}>
            {children}
          </section>
        </main>
      </div>
    </div>
  );
}
