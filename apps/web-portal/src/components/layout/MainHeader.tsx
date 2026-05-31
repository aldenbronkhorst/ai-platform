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
    admin: "Admin",
  };

  const label = tabLabels[activeTab] || activeTab;
  const subtitle = activeTab === "chat" && activeSession
    ? activeSession.title
    : undefined;

  return (
    <div className={`h-16 px-6 border-b border-default flex items-center shrink-0 ${isSidebarCollapsed ? "pl-16" : ""}`}>
      <div className="h-11 inline-flex items-center gap-2 px-5 rounded-2xl bg-sidebar border border-default select-none shadow-sm">
        <span className="text-sm font-extrabold text-default tracking-wide">
          {label}
        </span>
        {subtitle && (
          <>
            <span className="w-1 h-1 rounded-full bg-border-subtle" />
            <span className="text-xs text-muted font-medium truncate max-w-[200px]">
              {subtitle}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
