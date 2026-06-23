import { useRef, useCallback, useLayoutEffect, useState } from "react";
import { ArrowUp, X } from "lucide-react";

interface EditMessageProps {
  initialContent: string;
  onSave: (newContent: string) => void;
  onCancel: () => void;
  isSaving?: boolean;
}

export function EditMessage({ initialContent, onSave, onCancel, isSaving }: EditMessageProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState(initialContent);
  const [isExpanded, setIsExpanded] = useState(false);

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }, []);

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const maxHeight = window.innerWidth < 640 ? 112 : 160;
    ta.style.height = `${Math.min(ta.scrollHeight, maxHeight)}px`;
    const stackThreshold = window.innerWidth < 640 ? 44 : 96;
    setIsExpanded(value.includes("\n") || value.length > stackThreshold);
  }, [value]);

  const canSave = value.trim().length > 0 && !isSaving;

  const submitEdit = useCallback(() => {
    if (canSave) onSave(value);
  }, [canSave, onSave, value]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submitEdit();
    }
  }, [onCancel, submitEdit]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submitEdit();
      }}
      className="w-full"
    >
      <div className="glass-composer">
        <div className={isExpanded ? "flex flex-col gap-1 p-1 sm:p-1.5" : "flex items-center gap-1 p-1 sm:p-1.5"}>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isSaving}
            rows={1}
            className={`${isExpanded ? "w-full" : "flex-1"} min-h-9 bg-transparent border-0 focus:outline-none focus:ring-0 text-base sm:text-sm text-default placeholder-soft px-1 py-[7px] resize-none max-h-28 sm:max-h-[160px] leading-5 disabled:opacity-70`}
          />

          <div className={isExpanded ? "flex min-h-9 items-center justify-between gap-2" : "flex items-center gap-1 shrink-0"}>
            <button
              type="button"
              onClick={onCancel}
              disabled={isSaving}
              className="h-9 inline-flex items-center justify-center rounded-lg px-3 text-sm font-medium text-muted transition-all hover-bg-surface hover-text-default disabled:cursor-not-allowed disabled:opacity-50"
              title="Cancel"
            >
              <span className="hidden sm:inline">Cancel</span>
              <X className="w-4 h-4 sm:hidden" />
            </button>
            <button
              type="submit"
              disabled={!canSave}
              className={`h-9 w-9 inline-flex items-center justify-center rounded-full transition-all ${
                canSave
                  ? "border border-subtle bg-surface text-muted hover-text-default hover-bg-surface hover-border-default"
                  : "border border-subtle bg-surface text-soft"
              } disabled:cursor-not-allowed`}
              title="Send edited message"
            >
              <ArrowUp className="h-[26px] w-[26px]" />
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
