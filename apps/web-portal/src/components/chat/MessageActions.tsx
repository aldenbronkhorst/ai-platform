import { Copy, Check } from "lucide-react";
import { useState, useCallback, useEffect } from "react";

interface MessageActionsProps {
  content: string;
  onCopy?: () => void;
}

export function MessageActions({ content, onCopy }: MessageActionsProps) {
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
    <div
      className="relative flex flex-row items-center justify-end gap-2 py-1.5 opacity-0 pointer-events-none group-hover:pointer-events-auto group-hover:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100"
      data-slot="aui_msg-actions"
    >
      <button
        onClick={handleCopy}
        title="Copy"
        aria-label="Copy message"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-foreground" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}
