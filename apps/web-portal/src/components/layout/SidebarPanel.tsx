import { useEffect, useRef, useCallback, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import {
  FileText,
  Database,
  ClipboardList,
  Plus,
  X,
  Check,
  ChevronLeft,
  Menu,
  User,
  ChevronDown,
  Pencil,
  ShieldAlert,
  Settings,
  LogOut,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
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
  onRenameSession: (id: string, title: string) => void;
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
  onRenameSession,
  onToggleCollapse,
  onToggleProfileMenu,
  onSignOut,
  hasRole,
}: SidebarPanelProps) {
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const profileButtonRef = useRef<HTMLButtonElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  useEffect(() => {
    if (!editingSessionId) return;
    editInputRef.current?.focus();
    editInputRef.current?.select();
  }, [editingSessionId]);

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

  const navItems: { tab: ActiveTab; icon: LucideIcon; label: string }[] = [
    { tab: "tasks", icon: ClipboardList, label: "Tasks Tracker" },
    { tab: "artifacts", icon: FileText, label: "Documents Vault" },
    { tab: "connected-accounts", icon: Database, label: "Connected Accounts" },
  ];

  if (hasRole(["AIPlatform.Admin", "AIPlatform.Developer"])) {
    navItems.push({ tab: "admin", icon: ShieldAlert, label: "Admin" });
  }

  const startEditing = (e: ReactMouseEvent, session: ChatSession) => {
    e.stopPropagation();
    setEditingSessionId(session.id);
    setEditingTitle(session.title);
  };

  const cancelEditing = () => {
    setEditingSessionId(null);
    setEditingTitle("");
  };

  const commitEditing = (session: ChatSession) => {
    const nextTitle = editingTitle.trim().replace(/\s+/g, " ");
    if (nextTitle && nextTitle !== session.title) {
      onRenameSession(session.id, nextTitle);
    }
    cancelEditing();
  };

  if (isSidebarCollapsed) {
    return (
      <div className="fixed top-3 left-3 z-40">
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
    <aside className="fixed inset-0 z-50 h-[100dvh] w-full flex flex-col justify-between select-none shrink-0 animate-fade-in bg-sidebar border-0 rounded-none overflow-hidden overscroll-none md:relative md:inset-auto md:z-auto md:h-full md:w-72 md:border md:border-default md:rounded-3xl">
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
            <ChevronLeft className="hidden md:block w-4 h-4" />
            <X className="md:hidden w-4 h-4" />
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

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-3 space-y-0.5">
          <span className="px-3 py-1 block text-[10px] font-bold text-muted uppercase tracking-widest">
            Conversations
          </span>
          {isSessionsLoading && chatSessions.length === 0 ? (
            <div className="text-center py-6 text-xs text-muted">Loading...</div>
          ) : chatSessions.length === 0 ? (
            <div className="text-center py-8 text-xs text-soft font-medium">
              No recent conversations.
            </div>
          ) : (
            chatSessions.map((sess) => {
              const isEditing = editingSessionId === sess.id;
              return (
              <div
                key={sess.id}
                onClick={() => {
                  if (!isEditing) onSelectSession(sess);
                }}
                className={`group p-2.5 rounded-xl cursor-pointer transition-all flex items-center justify-between border ${
                  activeSession?.id === sess.id && activeTab === "chat"
                    ? "glass-active"
                    : "border-transparent text-muted hover-text-default hover-bg-surface"
                }`}
              >
                <div className="overflow-hidden flex-1 pr-2">
                  {isEditing ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        commitEditing(sess);
                      }}
                    >
                      <input
                        ref={editInputRef}
                        value={editingTitle}
                        onChange={(e) => setEditingTitle(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        onBlur={() => commitEditing(sess)}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            e.preventDefault();
                            cancelEditing();
                          }
                        }}
                        className="w-full bg-transparent text-xs font-semibold leading-tight text-default outline-none"
                        maxLength={80}
                      />
                    </form>
                  ) : (
                    <p className="text-xs font-semibold truncate leading-tight text-default" title={sess.title}>
                      {sess.title}
                    </p>
                  )}
                </div>
                {isEditing ? (
                  <button
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={(e) => {
                      e.stopPropagation();
                      commitEditing(sess);
                    }}
                    className="text-soft hover-text-default p-1 rounded hover-bg-surface transition-all shrink-0"
                    title="Save title"
                  >
                    <Check className="w-3 h-3" />
                  </button>
                ) : (
                  <div className="flex items-center gap-0.5 shrink-0 opacity-80 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 transition-all">
                    <button
                      onClick={(e) => startEditing(e, sess)}
                      className="text-soft hover-text-default p-1 rounded hover-bg-surface transition-all"
                      title="Rename chat"
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSession(sess.id);
                      }}
                      className="text-soft hover:text-[var(--color-danger)] p-1 rounded hover-bg-surface transition-all"
                      title="Delete chat"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )}
              </div>
              );
            })
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
