import { Compass } from "lucide-react";
import type { ChatSession, ActiveTab } from "../../types";

interface MainHeaderProps {
  activeTab: ActiveTab;
  activeSession: ChatSession | null;
  workflowTitle?: string;
  isSidebarCollapsed: boolean;
}

export function MainHeader({
  activeTab,
  activeSession,
  workflowTitle,
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
      className={`h-14 flex items-center justify-between px-6 select-none shrink-0 border-b border-default ${
        isSidebarCollapsed ? "pl-16" : ""
      }`}
    >
      <div className="flex items-center gap-3">
        <span className="text-xs uppercase tracking-widest text-muted font-extrabold">
          {tabLabels[activeTab] || activeTab}
        </span>

        {activeTab === "chat" && activeSession?.workflow_context && workflowTitle && (
          <span className="flex items-center gap-1.5 px-3 py-1 bg-surface border border-default text-muted rounded-full text-xs font-semibold">
            <Compass className="w-3.5 h-3.5" />
            Active Context: {workflowTitle}
          </span>
        )}
      </div>
    </header>
  );
}
