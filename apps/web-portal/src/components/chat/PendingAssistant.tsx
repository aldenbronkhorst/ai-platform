import { useEffect, useState } from "react";

const pendingMessages = [
  "Working on it…",
  "Checking that for you…",
  "Thinking…",
  "Reviewing the request…",
];

const toolMessages: Record<string, string> = {
  odoo: "Checking connected systems…",
  search: "Looking up the relevant records…",
  default: "Preparing the response…",
};

interface PendingAssistantProps {
  toolHint?: string;
}

export function PendingAssistant({ toolHint }: PendingAssistantProps) {
  const [msgIndex, setMsgIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setMsgIndex((i) => (i + 1) % pendingMessages.length);
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  const message = toolHint && toolMessages[toolHint]
    ? toolMessages[toolHint]
    : pendingMessages[msgIndex];

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-none min-w-0">
        <div className="flex items-center gap-2 text-muted text-sm">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          <span className="font-medium">{message}</span>
          <span className="flex gap-0.5">
            <span className="w-1 h-1 rounded-full bg-muted animate-bounce" style={{ animationDelay: "0ms" }} />
            <span className="w-1 h-1 rounded-full bg-muted animate-bounce" style={{ animationDelay: "150ms" }} />
            <span className="w-1 h-1 rounded-full bg-muted animate-bounce" style={{ animationDelay: "300ms" }} />
          </span>
        </div>
      </div>
    </div>
  );
}
