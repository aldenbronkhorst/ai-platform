import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { ChatSession, UserProfile, ActiveTab } from "../../types";
import { cn } from "../../lib/utils";
import { Codicon } from "../ui/Codicon";
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

const SIDEBAR_NAV: ReadonlyArray<{ tab: ActiveTab; icon: string; label: string }> = [
  { tab: "connected-accounts", icon: "plug", label: "Connectors" },
  { tab: "ai-providers", icon: "symbol-misc", label: "AI Providers" },
];

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

  const handleClickOutside = useCallback((event: MouseEvent) => {
    if (
      profileMenuRef.current &&
      !profileMenuRef.current.contains(event.target as Node) &&
      profileButtonRef.current &&
      !profileButtonRef.current.contains(event.target as Node)
    ) {
      onToggleProfileMenu();
    }
  }, [onToggleProfileMenu]);

  const handleEscape = useCallback((event: KeyboardEvent) => {
    if (event.key === "Escape") {
      onToggleProfileMenu();
    }
  }, [onToggleProfileMenu]);

  useEffect(() => {
    if (!isProfileMenuOpen) return;
    const controller = new AbortController();
    document.addEventListener("mousedown", handleClickOutside, { signal: controller.signal });
    document.addEventListener("keydown", handleEscape, { signal: controller.signal });
    return () => controller.abort();
  }, [isProfileMenuOpen, handleClickOutside, handleEscape]);

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
      <div className="fixed left-2 top-2 z-40">
        <button
          aria-label="Expand sidebar"
          className="sidebar-icon-button size-7"
          onClick={() => onToggleCollapse(false)}
          title="Expand sidebar"
          type="button"
        >
          <Codicon name="layout-sidebar-right" size="0.875rem" />
        </button>
      </div>
    );
  }

  return (
    <aside className="sidebar-shell">
      <div className="sidebar-main">
        <div className="sidebar-titlebar">
          <h1 className="sidebar-product-title">AI Platform</h1>
          <button
            aria-label="Collapse sidebar"
            className="sidebar-icon-button size-6"
            onClick={() => onToggleCollapse(true)}
            title="Collapse sidebar"
            type="button"
          >
            <Codicon name="chevron-left" size="0.875rem" />
          </button>
        </div>

        <nav aria-label="Primary" className="sidebar-nav-block">
          <button className="sidebar-nav-row" onClick={onNewChat} type="button">
            <span className="sidebar-row-lead">
              <Codicon name="robot" size="0.875rem" />
            </span>
            <span className="sidebar-row-label">New session</span>
          </button>

          <div className="sidebar-row-stack">
            {SIDEBAR_NAV.map(({ tab, icon, label }) => (
              <button
                aria-current={activeTab === tab ? "page" : undefined}
                className={cn("sidebar-nav-row", activeTab === tab && "is-active")}
                key={tab}
                onClick={() => onTabChange(tab)}
                type="button"
              >
                <span className="sidebar-row-lead">
                  <Codicon name={icon} size="0.875rem" />
                </span>
                <span className="sidebar-row-label">{label}</span>
              </button>
            ))}
          </div>
        </nav>

        <div className="sidebar-session-scroll">
          <section className="sidebar-section sidebar-section-fill" aria-label="Sessions">
            <SidebarSectionLabel meta={String(chatSessions.length)}>Sessions</SidebarSectionLabel>
            {isSessionsLoading && chatSessions.length === 0 ? (
              <div className="sidebar-empty-state">Loading...</div>
            ) : chatSessions.length === 0 ? (
              <div className="sidebar-empty-state">No recent conversations.</div>
            ) : (
              <div className="sidebar-row-stack">
                {chatSessions.map((session) => (
                  <SidebarSessionRow
                    active={activeSession?.id === session.id && activeTab === "chat"}
                    editing={editingSessionId === session.id}
                    editingTitle={editingTitle}
                    inputRef={editInputRef}
                    key={session.id}
                    onCancelEditing={cancelEditing}
                    onCommitEditing={() => commitEditing(session)}
                    onDelete={() => onDeleteSession(session.id)}
                    onEditingTitleChange={setEditingTitle}
                    onResume={() => onSelectSession(session)}
                    onStartEditing={() => startEditing(session)}
                    session={session}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      <div className="sidebar-footer">
        {isProfileMenuOpen && (
          <div ref={profileMenuRef} className="sidebar-profile-menu">
            <div className="sidebar-profile-menu-header">
              <p>{activeUser?.displayName}</p>
              <span>{activeUser?.email}</span>
            </div>
            <button className="sidebar-profile-menu-item is-danger" onClick={onSignOut} type="button">
              <Codicon name="sign-out" size="0.875rem" />
              <span>Sign Out</span>
            </button>
          </div>
        )}

        <button
          ref={profileButtonRef}
          aria-expanded={isProfileMenuOpen}
          className="sidebar-profile-row"
          data-state={isProfileMenuOpen ? "open" : "closed"}
          onClick={onToggleProfileMenu}
          type="button"
        >
          <span className="sidebar-profile-avatar">
            <Codicon name="account" size="0.875rem" />
          </span>
          <span className="sidebar-profile-copy">
            <span>{activeUser?.displayName}</span>
            <small>Microsoft ID Active</small>
          </span>
          <Codicon className="sidebar-profile-caret" name="chevron-down" size="0.875rem" />
        </button>
      </div>
    </aside>
  );
}

function SidebarSectionLabel({
  children,
  meta,
}: {
  children: string;
  meta: string | null;
}) {
  return (
    <div className="sidebar-section-label">
      <span aria-hidden="true" className="sidebar-section-dot dither" />
      <span className="sidebar-section-name">{children}</span>
      {meta ? <span className="sidebar-section-meta">{meta}</span> : null}
    </div>
  );
}

function SidebarSessionRow({
  active,
  editing,
  editingTitle,
  inputRef,
  onCancelEditing,
  onCommitEditing,
  onDelete,
  onEditingTitleChange,
  onResume,
  onStartEditing,
  session,
}: {
  active: boolean;
  editing: boolean;
  editingTitle: string;
  inputRef: RefObject<HTMLInputElement | null>;
  onCancelEditing: () => void;
  onCommitEditing: () => void;
  onDelete: () => void;
  onEditingTitleChange: (title: string) => void;
  onResume: () => void;
  onStartEditing: () => void;
  session: ChatSession;
}) {
  return (
    <div className={cn("sidebar-session-row group", active && "is-active")}>
      {editing ? (
        <form
          className="sidebar-session-body"
          onClick={(event) => event.stopPropagation()}
          onSubmit={(event) => {
            event.preventDefault();
            onCommitEditing();
          }}
        >
          <input
            className="sidebar-rename-input"
            maxLength={80}
            onBlur={onCommitEditing}
            onChange={(event) => onEditingTitleChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                onCancelEditing();
              }
            }}
            ref={inputRef}
            value={editingTitle}
          />
          <button
            aria-label="Save title"
            className="sidebar-action-button is-visible"
            onMouseDown={(event) => event.preventDefault()}
            type="submit"
          >
            <Codicon name="check" size="0.875rem" />
          </button>
        </form>
      ) : (
        <>
          <button className="sidebar-session-body" onClick={onResume} type="button">
            <span className="sidebar-row-label">{session.title}</span>
          </button>
          <div className="sidebar-session-actions">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  aria-label={`Actions for ${session.title}`}
                  className="sidebar-action-button"
                  onClick={(event) => event.stopPropagation()}
                  title="Session actions"
                  type="button"
                >
                  <Codicon name="kebab-vertical" size="0.875rem" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" aria-label={`Actions for ${session.title}`} className="w-36" sideOffset={6}>
                <DropdownMenuItem onSelect={onStartEditing}>
                  <Codicon name="edit" size="0.875rem" />
                  <span>Rename</span>
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={onDelete} variant="destructive">
                  <Codicon name="trash" size="0.875rem" />
                  <span>Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </>
      )}
    </div>
  );
}
