import type { ActiveTab, ChatSession } from "../../types";

interface MainHeaderProps {
  activeTab: ActiveTab;
  activeSession: ChatSession | null;
  isSidebarCollapsed: boolean;
}

export function MainHeader({
  activeTab,
  activeSession,
  isSidebarCollapsed,
}: MainHeaderProps) {
  const tabLabels: Record<ActiveTab, string> = {
    workflows: "Workflows",
    chat: "Chat",
    tasks: "Tasks",
    artifacts: "Documents",
    "connected-accounts": "Connected Accounts",
    audit: "Audit Logs",
    settings: "Settings",
  };

  const label = tabLabels[activeTab] || activeTab;
  const subtitle = activeTab === "chat" && activeSession
    ? activeSession.title
    : undefined;

  return (
    <div className={`pt-5 pb-3 px-6 ${isSidebarCollapsed ? "pl-16" : ""}`}>
      <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-sidebar border border-default select-none shadow-sm">
        <span className="text-xs font-extrabold text-default tracking-wide">
          {label}
        </span>
        {subtitle && (
          <>
            <span className="w-1 h-1 rounded-full bg-border-subtle" />
            <span className="text-[11px] text-muted font-medium truncate max-w-[180px]">
              {subtitle}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
