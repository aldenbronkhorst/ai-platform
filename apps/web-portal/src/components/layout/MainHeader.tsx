import type { ActiveTab } from "../../types";

interface MainHeaderProps {
  activeTab: ActiveTab;
  isSidebarCollapsed: boolean;
}

export function MainHeader({
  activeTab,
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

  return (
    <header
      className={`h-12 flex items-center justify-between px-6 select-none shrink-0 ${
        isSidebarCollapsed ? "pl-16" : ""
      }`}
    >
      <div className="flex items-center gap-3">
        <span className="text-xs uppercase tracking-widest text-muted font-extrabold">
          {tabLabels[activeTab] || activeTab}
        </span>
      </div>
    </header>
  );
}
