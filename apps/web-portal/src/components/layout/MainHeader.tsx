import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Pencil, Trash2, X } from "lucide-react";
import type { ActiveTab, ChatSession } from "../../types";

interface MainHeaderProps {
  activeTab: ActiveTab;
  activeSession: ChatSession | null;
  isSidebarCollapsed: boolean;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
}

export function MainHeader({
  activeTab,
  activeSession,
  isSidebarCollapsed,
  onDeleteSession,
  onRenameSession,
}: MainHeaderProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [openMenuKey, setOpenMenuKey] = useState<string | null>(null);
  const [isRenaming, setIsRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");

  const tabLabels: Record<ActiveTab, string> = {
    chat: "Chat",
    "connected-accounts": "Connectors",
    "ai-providers": "AI Providers",
  };

  const canOpenMenu = activeTab === "chat" && Boolean(activeSession);
  const menuKey = `${activeTab}:${activeSession?.id ?? "none"}`;
  const isMenuOpen = openMenuKey === menuKey;
  const label = activeTab === "chat"
    ? activeSession?.title || "New session"
    : tabLabels[activeTab] || activeTab;

  useEffect(() => {
    if (!isMenuOpen) return;
    const ac = new AbortController();
    document.addEventListener("mousedown", (event) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setOpenMenuKey(null);
      setIsRenaming(false);
    }, { signal: ac.signal });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      setOpenMenuKey(null);
      setIsRenaming(false);
    }, { signal: ac.signal });
    return () => ac.abort();
  }, [isMenuOpen]);

  useEffect(() => {
    if (!isRenaming) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [isRenaming]);

  const beginRename = () => {
    if (!activeSession) return;
    setDraftTitle(activeSession.title);
    setIsRenaming(true);
  };

  const commitRename = () => {
    if (!activeSession) return;
    const nextTitle = draftTitle.trim().replace(/\s+/g, " ");
    if (nextTitle && nextTitle !== activeSession.title) {
      onRenameSession(activeSession.id, nextTitle);
    }
    setIsRenaming(false);
    setOpenMenuKey(null);
  };

  const deleteSession = () => {
    if (!activeSession) return;
    onDeleteSession(activeSession.id);
    setIsRenaming(false);
    setOpenMenuKey(null);
  };

  return (
    <header className={`h-[var(--titlebar-height)] px-2 border-b border-[var(--ui-stroke-tertiary)] bg-[var(--ui-chat-surface-background)] flex items-center shrink-0 ${isSidebarCollapsed ? "pl-14 sm:pl-16" : ""}`}>
      <div className="relative min-w-0" ref={menuRef}>
        <button
          type="button"
          aria-expanded={canOpenMenu ? isMenuOpen : undefined}
          className="inline-flex h-6 min-w-0 max-w-full items-center gap-1 rounded-md border border-transparent bg-transparent px-2 text-muted transition-colors hover:border-subtle hover-bg-subtle hover-text-default data-[state=open]:border-subtle data-[state=open]:bg-[var(--ui-control-active-background)] data-[state=open]:text-default"
          data-state={isMenuOpen ? "open" : "closed"}
          onClick={() => {
            if (!canOpenMenu) return;
            setOpenMenuKey(open => open === menuKey ? null : menuKey);
            setIsRenaming(false);
          }}
          title={label}
        >
          <span className="min-w-0 truncate text-[0.75rem] font-medium leading-none">
            {label}
          </span>
          {canOpenMenu && <ChevronDown className="h-3 w-3 shrink-0 text-soft" />}
        </button>

        {isMenuOpen && activeSession && (
          <div className="absolute left-0 top-7 z-50 w-56 overflow-hidden rounded-md border border-[var(--ui-stroke-secondary)] bg-[color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent)] p-1 text-xs text-default shadow-sm backdrop-blur-sm">
            {isRenaming ? (
              <form
                className="grid gap-1.5 p-1"
                onSubmit={(event) => {
                  event.preventDefault();
                  commitRename();
                }}
              >
                <input
                  ref={inputRef}
                  value={draftTitle}
                  onChange={(event) => setDraftTitle(event.target.value)}
                  className="h-7 min-w-0 rounded-[4px] border border-[var(--ui-stroke-tertiary)] bg-[var(--dt-card)] px-2 text-xs text-default outline-none focus:border-[var(--ui-stroke-secondary)]"
                  maxLength={80}
                />
                <div className="flex justify-end gap-1">
                  <button
                    className="grid h-6 w-6 place-items-center rounded-[4px] text-muted transition-colors hover:bg-[var(--ui-bg-tertiary)] hover:text-default"
                    onClick={() => setIsRenaming(false)}
                    title="Cancel"
                    type="button"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                  <button
                    className="grid h-6 w-6 place-items-center rounded-[4px] text-muted transition-colors hover:bg-[var(--ui-bg-tertiary)] hover:text-default"
                    title="Save"
                    type="submit"
                  >
                    <Check className="h-3.5 w-3.5" />
                  </button>
                </div>
              </form>
            ) : (
              <>
                <button
                  className="flex w-full items-center gap-2 rounded-[4px] px-2 py-1.5 text-left transition-colors hover:bg-[var(--ui-bg-tertiary)]"
                  onClick={beginRename}
                  type="button"
                >
                  <Pencil className="h-3.5 w-3.5 text-muted" />
                  <span>Rename</span>
                </button>
                <button
                  className="flex w-full items-center gap-2 rounded-[4px] px-2 py-1.5 text-left text-[var(--color-danger)] transition-colors hover:bg-[var(--ui-bg-tertiary)]"
                  onClick={deleteSession}
                  type="button"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span>Delete</span>
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
