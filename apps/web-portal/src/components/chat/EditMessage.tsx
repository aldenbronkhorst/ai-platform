import { useRef, useCallback, useEffect } from "react";
import { Check, X } from "lucide-react";

interface EditMessageProps {
  initialContent: string;
  onSave: (newContent: string) => void;
  onCancel: () => void;
  isSaving?: boolean;
}

export function EditMessage({ initialContent, onSave, onCancel, isSaving }: EditMessageProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const valueRef = useRef(initialContent);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }, []);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      const val = (e.target as HTMLTextAreaElement).value;
      if (val.trim()) {
        onSave(val);
      }
    }
  }, [onSave, onCancel]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    valueRef.current = e.target.value;
  }, []);

  return (
    <div className="max-w-[70%]">
      <div className="p-3 rounded-2xl border bg-raised border-accent/40 shadow-sm">
        <textarea
          ref={textareaRef}
          defaultValue={initialContent}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={isSaving}
          rows={2}
          className="w-full bg-transparent border-0 focus:outline-none focus:ring-0 text-xs text-default placeholder-soft resize-none max-h-[200px] leading-relaxed"
        />
      </div>

      <div className="flex items-center gap-1.5 mt-1.5 justify-end">
        <button
          onClick={onCancel}
          disabled={isSaving}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-muted hover:text-default hover-bg-surface transition-all"
          title="Cancel"
          aria-label="Cancel edit"
        >
          <X className="w-3.5 h-3.5" />
          Cancel
        </button>
        <button
          onClick={() => {
            const val = textareaRef.current?.value;
            if (val && val.trim()) onSave(val);
          }}
          disabled={isSaving}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-accent text-white text-[11px] font-semibold hover:opacity-90 transition-all"
          title="Save and resend"
          aria-label="Save and resend"
        >
          {isSaving ? (
            <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : (
            <Check className="w-3.5 h-3.5" />
          )}
          Save & resend
        </button>
      </div>
    </div>
  );
}
