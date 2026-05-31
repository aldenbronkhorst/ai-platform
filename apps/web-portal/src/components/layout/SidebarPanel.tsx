import { useEffect, useRef, useCallback } from "react";
import {
  Layers,
  FileText,
  Database,
  ClipboardList,
  Plus,
  X,
  ChevronLeft,
  Menu,
  User,
  ChevronDown,
  ShieldAlert,
  Settings,
  LogOut,
} from "lucide-react";
import type { ChatSession, UserProfile, ActiveTab } from "../../types";

interface SidebarPanelProps {
  activeTab: ActiveTab;
  chatSessions: ChatSession[];
  activeSession: ChatSession | null;
  activeUser: UserProfile | null;
  isSessionsLoading: boolean;
  isSidebarCollapsed: boolean;
  isProfileMenuOpen: boolean;
  onTabChange: (tab: ActiveTab) => void;
  onNewChat: () => void;
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (id: string) => void;
  onToggleCollapse: (collapsed: boolean) => void;
  onToggleProfileMenu: () => void;
  onSignOut: () => void;
  hasRole: (roles: string[]) => boolean;
}

export function SidebarPanel({
  activeTab,
  chatSessions,
  activeSession,
  activeUser,
  isSessionsLoading,
  isSidebarCollapsed,
  isProfileMenuOpen,
  onTabChange,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onToggleCollapse,
  onToggleProfileMenu,
  onSignOut,
  hasRole,
}: SidebarPanelProps) {
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const profileButtonRef = useRef<HTMLButtonElement>(null);

  const handleClickOutside = useCallback((e: MouseEvent) => {
    if (
      profileMenuRef.current &&
      !profileMenuRef.current.contains(e.target as Node) &&
      profileButtonRef.current &&
      !profileButtonRef.current.contains(e.target as Node)
    ) {
      onToggleProfileMenu();
    }
  }, [onToggleProfileMenu]);

  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") {
      onToggleProfileMenu();
    }
  }, [onToggleProfileMenu]);

  useEffect(() => {
    if (!isProfileMenuOpen) return;
    const ac = new AbortController();
    document.addEventListener("mousedown", handleClickOutside, { signal: ac.signal });
    document.addEventListener("keydown", handleEscape, { signal: ac.signal });
    return () => ac.abort();
  }, [isProfileMenuOpen, handleClickOutside, handleEscape]);

  const navItems: { tab: ActiveTab; icon: any; label: string }[] = [
    { tab: "workflows", icon: Layers, label: "Workflows" },
    { tab: "tasks", icon: ClipboardList, label: "Tasks Tracker" },
    { tab: "artifacts", icon: FileText, label: "Documents Vault" },
    { tab: "connected-accounts", icon: Database, label: "Connected Accounts" },
  ];

  if (hasRole(["AIPlatform.Admin", "AIPlatform.Developer"])) {
    navItems.push({ tab: "admin", icon: ShieldAlert, label: "Admin" });
  }

  if (isSidebarCollapsed) {
    return (
      <div className="fixed top-[22px] left-4 z-40">
        <button
          onClick={() => onToggleCollapse(false)}
          className="h-11 w-11 flex items-center justify-center bg-sidebar border border-default text-muted hover:text-default rounded-2xl transition-all shadow-lg"
          title="Expand Sidebar"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>
    );
  }

  return (
    <aside className="w-72 flex flex-col justify-between select-none shrink-0 animate-fade-in bg-sidebar border border-default rounded-3xl overflow-hidden">
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="h-16 px-4 border-b border-default flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <h1 className="font-extrabold text-sm tracking-wide text-default">
              AI Platform
            </h1>
          </div>
          <button
            onClick={() => onToggleCollapse(true)}
            className="p-1.5 text-muted hover:text-default rounded-lg hover-bg-surface transition-all"
            title="Collapse Sidebar"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        </div>

        <div className="p-3 border-b border-default space-y-2">
          <button
            onClick={onNewChat}
            className="w-full py-2.5 glass-btn rounded-xl text-xs font-bold tracking-wide transition-all flex items-center justify-center gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" />
            New Chat
          </button>

          <div className="space-y-0.5">
            {navItems.map(({ tab, icon: Icon, label }) => (
              <button
                key={tab}
                onClick={() => onTabChange(tab)}
                className={`w-full flex items-center gap-3 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all border ${
                  activeTab === tab
                    ? "glass-active"
                    : "border-transparent text-muted hover-text-default hover-bg-surface"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-0.5">
          <span className="px-3 py-1 block text-[10px] font-bold text-muted uppercase tracking-widest">
            Conversations
          </span>
          {isSessionsLoading ? (
            <div className="text-center py-6 text-xs text-muted">Loading...</div>
          ) : chatSessions.length === 0 ? (
            <div className="text-center py-8 text-xs text-soft font-medium">
              No recent conversations.
            </div>
          ) : (
            chatSessions.map((sess) => (
              <div
                key={sess.id}
                onClick={() => onSelectSession(sess)}
                className={`group p-2.5 rounded-xl cursor-pointer transition-all flex items-center justify-between border ${
                  activeSession?.id === sess.id && activeTab === "chat"
                    ? "glass-active"
                    : "border-transparent text-muted hover-text-default hover-bg-surface"
                }`}
              >
                <div className="overflow-hidden flex-1 pr-2">
                  <p className="text-xs font-semibold truncate leading-tight text-default">
                    {sess.title}
                  </p>
                  {sess.workflow_context && (
                    <span className="text-[8px] text-soft font-bold block truncate mt-0.5 uppercase tracking-wider">
                      {sess.workflow_context.split("_").join(" ")}
                    </span>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(sess.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 text-soft hover:text-[var(--color-danger)] p-1 rounded hover-bg-surface transition-all shrink-0"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="p-3 border-t border-default relative">
        {isProfileMenuOpen && (
          <div ref={profileMenuRef} className="absolute bottom-16 left-3 right-3 bg-raised border border-default rounded-2xl shadow-2xl p-2 py-3 space-y-1 z-50 animate-fade-in text-left">
            <div className="px-3 py-1">
              <p className="text-xs font-bold text-default truncate">
                {activeUser?.displayName}
              </p>
              <p className="text-[10px] text-muted truncate mt-0.5">
                {activeUser?.email}
              </p>
            </div>
            <div className="border-t border-default my-1" />

            {hasRole(["AIPlatform.Admin", "AIPlatform.Developer", "AIPlatform.Auditor"]) && (
              <>
                {hasRole(["AIPlatform.Admin", "AIPlatform.Auditor"]) && (
                  <button
                    onClick={() => {
                      onTabChange("audit");
                      onToggleProfileMenu();
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-muted hover-text-default hover-bg-surface rounded-xl text-left transition-all"
                  >
                    <ShieldAlert className="w-3.5 h-3.5" />
                    Audit Logs
                  </button>
                )}
                <button
                  onClick={() => {
                    onTabChange("settings");
                    onToggleProfileMenu();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-muted hover-text-default hover-bg-surface rounded-xl text-left transition-all"
                >
                  <Settings className="w-3.5 h-3.5" />
                  System Settings
                </button>
                <div className="border-t border-default my-1" />
              </>
            )}

            <button
              onClick={onSignOut}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-danger)] hover:text-[var(--color-danger)] hover-bg-surface rounded-xl text-left transition-all"
            >
              <LogOut className="w-3.5 h-3.5" />
              Sign Out
            </button>
          </div>
        )}

        <button
          ref={profileButtonRef}
          onClick={onToggleProfileMenu}
          className="w-full flex items-center justify-between p-2 rounded-xl bg-surface hover-bg-surface border border-default transition-all"
        >
          <div className="flex items-center gap-2.5 overflow-hidden">
            <div className="w-8 h-8 rounded-lg bg-surface border border-default flex items-center justify-center shrink-0">
              <User className="w-4 h-4 text-muted" />
            </div>
            <div className="text-left overflow-hidden">
              <p className="text-xs font-bold text-default truncate">
                {activeUser?.displayName}
              </p>
              <span className="text-[9px] text-muted truncate block">
                Microsoft ID Active
              </span>
            </div>
          </div>
          <ChevronDown
            className={`w-3.5 h-3.5 text-muted transition-all ${
              isProfileMenuOpen ? "rotate-180" : ""
            }`}
          />
        </button>
      </div>
    </aside>
  );
}
