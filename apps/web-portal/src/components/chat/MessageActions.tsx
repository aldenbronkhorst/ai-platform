import { Copy, Check, Pencil } from "lucide-react";
import { useState, useCallback, useEffect } from "react";

interface MessageActionsProps {
  role: "user" | "assistant";
  content: string;
  onCopy?: () => void;
  onEdit?: () => void;
}

export function MessageActions({ role, content, onCopy, onEdit }: MessageActionsProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(content).catch(() => {});
    onCopy?.();
    setCopied(true);
  }, [content, onCopy]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-150">
      <button
        onClick={handleCopy}
        className="p-1 rounded-md text-muted hover:text-default hover-bg-surface transition-all"
        title="Copy"
        aria-label="Copy message"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-accent" /> : <Copy className="w-3.5 h-3.5" />}
      </button>

      {role === "user" && onEdit && (
        <button
          onClick={onEdit}
          className="p-1 rounded-md text-muted hover:text-default hover-bg-surface transition-all"
          title="Edit"
          aria-label="Edit message"
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}
