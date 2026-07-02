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
      <div className="composer-surface" data-slot="composer-surface">
        <div className={isExpanded ? "flex flex-col gap-1 px-2 py-1.5" : "flex items-center gap-1 px-2 py-1.5"}>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isSaving}
            rows={1}
            data-slot="composer-rich-input"
            className={`${isExpanded ? "w-full" : "flex-1"} min-h-[1.625rem] bg-transparent border-0 focus:outline-none focus:ring-0 text-base sm:text-[0.8125rem] text-foreground placeholder:text-[var(--ui-text-tertiary)] px-1 py-1 resize-none max-h-28 sm:max-h-[9.375rem] leading-normal disabled:opacity-70`}
          />

          <div className={isExpanded ? "flex min-h-[1.625rem] items-center justify-between gap-2" : "flex items-center gap-1 shrink-0"}>
            <button
              type="button"
              onClick={onCancel}
              disabled={isSaving}
              className="h-6 inline-flex items-center justify-center rounded-md px-2 text-[0.75rem] font-medium text-[var(--ui-text-secondary)] transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              title="Cancel"
            >
              <span className="hidden sm:inline">Cancel</span>
              <X className="w-4 h-4 sm:hidden" />
            </button>
            <button
              type="submit"
              disabled={!canSave}
              className={`h-[1.625rem] w-[1.625rem] inline-flex items-center justify-center rounded-full transition-colors ${
                canSave
                  ? "border border-[var(--ui-stroke-tertiary)] bg-[var(--dt-card)] text-[var(--ui-text-secondary)] hover:border-[var(--ui-stroke-secondary)] hover:text-foreground"
                  : "border border-[var(--ui-stroke-tertiary)] bg-[var(--dt-card)] text-[var(--ui-text-tertiary)]"
              } disabled:cursor-not-allowed`}
              title="Send edited message"
            >
              <ArrowUp className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
