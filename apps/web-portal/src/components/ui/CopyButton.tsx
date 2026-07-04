import { Check, Copy, X } from "lucide-react";
import * as React from "react";

import { cn } from "../../lib/utils";

type CopyPayload = string | (() => Promise<string> | string);
type CopyStatus = "copied" | "error" | "idle";

const COPIED_RESET_MS = 1_500;

async function writeClipboardText(text: string) {
  if (!text) return;
  if (!navigator.clipboard?.writeText) throw new Error("Clipboard API is unavailable");
  await navigator.clipboard.writeText(text);
}

interface CopyButtonProps {
  className?: string;
  disabled?: boolean;
  iconClassName?: string;
  label?: string;
  showLabel?: boolean;
  text: CopyPayload;
}

export function CopyButton({
  className,
  disabled = false,
  iconClassName,
  label = "Copy",
  showLabel = true,
  text,
}: CopyButtonProps) {
  const [status, setStatus] = React.useState<CopyStatus>("idle");
  const resetRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    return () => {
      if (resetRef.current !== null) {
        window.clearTimeout(resetRef.current);
      }
    };
  }, []);

  const copy = React.useCallback(async () => {
    try {
      const value = typeof text === "function" ? await text() : text;
      if (!value) return;

      await writeClipboardText(value);

      if (resetRef.current !== null) {
        window.clearTimeout(resetRef.current);
      }

      setStatus("copied");
      resetRef.current = window.setTimeout(() => {
        setStatus("idle");
        resetRef.current = null;
      }, COPIED_RESET_MS);
    } catch {
      if (resetRef.current !== null) {
        window.clearTimeout(resetRef.current);
      }

      setStatus("error");
      resetRef.current = window.setTimeout(() => {
        setStatus("idle");
        resetRef.current = null;
      }, COPIED_RESET_MS);
    }
  }, [text]);

  const Icon = status === "copied" ? Check : status === "error" ? X : Copy;
  const feedbackLabel = status === "copied" ? "Copied" : status === "error" ? "Copy failed" : label;

  return (
    <button
      aria-label={feedbackLabel}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[0.75rem] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40",
        className,
      )}
      disabled={disabled}
      onClick={() => void copy()}
      title={feedbackLabel}
      type="button"
    >
      <Icon className={cn("size-3.5", iconClassName)} />
      {showLabel && (status === "copied" ? "Copied" : status === "error" ? "Failed" : label)}
    </button>
  );
}
