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
  onToggleCollapse,
  onToggleProfileMenu,
  onSignOut,
  hasRole,
}: AppShellProps) {
  return (
    <div className="flex h-screen bg-canvas text-default antialiased overflow-hidden">
      <div className="flex w-full gap-3 p-3">
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
          onToggleCollapse={onToggleCollapse}
          onToggleProfileMenu={onToggleProfileMenu}
          onSignOut={onSignOut}
          hasRole={hasRole}
        />

        <main className="flex-1 flex flex-col overflow-hidden min-w-0">
          <MainHeader
            activeTab={activeTab}
            activeSession={activeSession}
            isSidebarCollapsed={isSidebarCollapsed}
          />
          <section className={`flex-1 overflow-y-auto ${activeTab === "chat" ? "" : "p-6"}`}>
            {children}
          </section>
        </main>
      </div>
    </div>
  );
}
