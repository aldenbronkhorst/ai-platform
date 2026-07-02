import { useEffect, useRef, useCallback, useState } from "react";
import {
  Plug,
  BrainCircuit,
  Plus,
  X,
  Check,
  ChevronLeft,
  Menu,
  User,
  ChevronDown,
  Pencil,
  LogOut,
  MoreVertical,
  Trash2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ChatSession, UserProfile, ActiveTab } from "../../types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

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
    { tab: "connected-accounts", icon: Plug, label: "Connectors" },
    { tab: "ai-providers", icon: BrainCircuit, label: "AI Providers" },
  ];

  const startEditing = (session: ChatSession) => {
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
          className="flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--sidebar-edge-border)] bg-[var(--ui-sidebar-surface-background)] text-muted transition-colors hover:text-default"
          title="Expand Sidebar"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>
    );
  }

  return (
    <aside className="fixed inset-0 z-50 h-[100dvh] w-full flex flex-col justify-between select-none shrink-0 animate-fade-in bg-[var(--ui-sidebar-surface-background)] border-0 rounded-none overflow-hidden overscroll-none md:relative md:inset-auto md:z-auto md:h-full md:w-[var(--sidebar-width)] md:border-r md:border-[var(--sidebar-edge-border)]">
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="h-[var(--titlebar-height)] px-3 border-b border-[var(--ui-stroke-tertiary)] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <h1 className="font-semibold text-[0.8125rem] text-default">
              AI Platform
            </h1>
          </div>
          <button
            onClick={() => onToggleCollapse(true)}
            className="rounded-md p-1 text-muted transition-colors hover-bg-surface hover:text-default"
            title="Collapse Sidebar"
          >
            <ChevronLeft className="hidden md:block w-4 h-4" />
            <X className="md:hidden w-4 h-4" />
          </button>
        </div>

        <div className="px-2.5 py-2 border-b border-[var(--ui-stroke-tertiary)] space-y-1">
          <button
            onClick={onNewChat}
            className="nav-item"
          >
            <Plus className="w-3.5 h-3.5" />
            <span className="nav-item-label">New session</span>
          </button>

          <div className="nav-list">
            {navItems.map(({ tab, icon: Icon, label }) => (
              <button
                key={tab}
                onClick={() => onTabChange(tab)}
                className={`nav-item ${activeTab === tab ? "nav-item-active" : ""}`}
              >
                <Icon className="nav-item-icon" />
                <span className="nav-item-label">{label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-2.5 py-2 space-y-0.5">
          <span className="px-2 py-1.5 block text-[0.64rem] font-semibold text-[var(--theme-primary)] uppercase tracking-[0.16em]">
            Sessions
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
                className={`nav-item group cursor-pointer justify-between ${
                  activeSession?.id === sess.id && activeTab === "chat" ? "nav-item-active" : ""
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
                  <div className="relative z-[2] grid w-[1.375rem] shrink-0 place-items-center">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          aria-label={`Actions for ${sess.title}`}
                          className="grid h-5 w-5 place-items-center rounded-[4px] bg-transparent text-transparent transition-colors duration-100 hover:bg-[var(--ui-control-active-background)] hover:text-foreground focus-visible:bg-[var(--ui-control-active-background)] focus-visible:text-foreground data-[state=open]:bg-[var(--ui-control-active-background)] data-[state=open]:text-foreground group-hover:text-[var(--ui-text-tertiary)]"
                          onClick={(e) => e.stopPropagation()}
                          title="Session actions"
                          type="button"
                        >
                          <MoreVertical className="h-3.5 w-3.5" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent
                        align="end"
                        aria-label={`Actions for ${sess.title}`}
                        className="w-36"
                        sideOffset={6}
                      >
                        <DropdownMenuItem onSelect={() => startEditing(sess)}>
                          <Pencil className="h-3.5 w-3.5 text-muted" />
                          <span>Rename</span>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => onDeleteSession(sess.id)}
                          variant="destructive"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          <span>Delete</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )}
              </div>
              );
            })
          )}
        </div>
      </div>

      <div className="p-2 border-t border-[var(--ui-stroke-tertiary)] relative">
        {isProfileMenuOpen && (
          <div
            ref={profileMenuRef}
            className="absolute bottom-16 left-3 right-3 z-50 space-y-1 rounded-md border border-[var(--ui-stroke-secondary)] bg-[color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent)] p-2 py-3 text-left shadow-sm backdrop-blur-sm animate-fade-in"
          >
            <div className="px-3 py-1">
              <p className="text-xs font-bold text-default truncate">
                {activeUser?.displayName}
              </p>
              <p className="text-[10px] text-muted truncate mt-0.5">
                {activeUser?.email}
              </p>
            </div>
            <div className="border-t border-[var(--ui-stroke-tertiary)] my-1" />

            <button
              onClick={onSignOut}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-danger)] hover:bg-[var(--ui-row-hover-background)] hover:text-[var(--color-danger)] rounded-[4px] text-left transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
              Sign Out
            </button>
          </div>
        )}

        <button
          ref={profileButtonRef}
          onClick={onToggleProfileMenu}
          data-state={isProfileMenuOpen ? "open" : "closed"}
          className="w-full flex items-center justify-between p-1.5 rounded-md bg-transparent transition-colors hover:bg-[var(--ui-row-hover-background)] data-[state=open]:bg-[var(--ui-control-active-background)]"
        >
          <div className="flex items-center gap-2.5 overflow-hidden">
            <div className="w-7 h-7 rounded-md bg-[var(--ui-bg-tertiary)] border border-[var(--ui-stroke-tertiary)] flex items-center justify-center shrink-0">
              <User className="w-3.5 h-3.5 text-muted" />
            </div>
            <div className="text-left overflow-hidden">
              <p className="text-[0.75rem] font-medium text-default truncate">
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
