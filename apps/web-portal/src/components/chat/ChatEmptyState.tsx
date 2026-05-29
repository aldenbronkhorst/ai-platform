import { FileText, Search, Clock, HelpCircle } from "lucide-react";
import type { SuggestedAction } from "../../types";

interface ChatEmptyStateProps {
  displayName: string;
  onSuggestion: (prompt: string) => void;
}

const suggestedActions: SuggestedAction[] = [
  { label: "Review a credit note", prompt: "Show me the latest credit notes that need review.", icon: "file" },
  { label: "Check attendance", prompt: "Check attendance records for today.", icon: "clock" },
  { label: "Upload a document", prompt: "I want to upload a document for processing.", icon: "upload" },
  { label: "Ask about processes", prompt: "What operational processes can you help me with?", icon: "help" },
];

const iconMap: Record<string, React.ReactNode> = {
  file: <FileText className="w-4 h-4" />,
  clock: <Clock className="w-4 h-4" />,
  upload: <Search className="w-4 h-4" />,
  help: <HelpCircle className="w-4 h-4" />,
};

export function ChatEmptyState({ displayName, onSuggestion }: ChatEmptyStateProps) {
  const firstName = displayName.split(" ")[0];

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 select-none">
      <div className="max-w-lg w-full text-center space-y-8">
        <div className="space-y-2">
          <h2 className="text-2xl font-bold text-default">
            What would you like to work on, {firstName}?
          </h2>
          <p className="text-sm text-muted">
            Ask about business operations, run audits, or check connected systems.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {suggestedActions.map((action) => (
            <button
              key={action.label}
              onClick={() => onSuggestion(action.prompt)}
              className="flex items-center gap-3 p-4 rounded-xl bg-surface border border-default hover:border-accent hover:bg-subtle transition-all text-left group"
            >
              <span className="w-8 h-8 rounded-lg bg-canvas border border-default flex items-center justify-center shrink-0 text-muted group-hover:text-accent transition-colors">
                {iconMap[action.icon]}
              </span>
              <span className="text-sm font-semibold text-default leading-snug">
                {action.label}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
