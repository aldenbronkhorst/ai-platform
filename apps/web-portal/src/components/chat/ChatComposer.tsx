import { useRef, useCallback, useEffect } from "react";
import { Plus, Mic, MicOff, CornerDownLeft, FileText, RefreshCw } from "lucide-react";
import type { AttachedFile, VoiceState } from "../../types";

interface ChatComposerProps {
  chatInput: string;
  attachedFiles: AttachedFile[];
  voiceState: VoiceState;
  isChatSending: boolean;
  placeholder?: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
}

export function ChatComposer({
  chatInput,
  attachedFiles,
  voiceState,
  isChatSending,
  placeholder = "Ask anything...",
  onInputChange,
  onSend,
  onFileUpload,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
}: ChatComposerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleTriggerUpload = () => {
    fileInputRef.current?.click();
    onTriggerUpload();
  };

  const formRef = useRef<HTMLFormElement>(null);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!isChatSending && (chatInput.trim() || attachedFiles.length > 0)) {
        formRef.current?.requestSubmit();
      }
    }
  }, [isChatSending, chatInput, attachedFiles]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const newHeight = Math.min(ta.scrollHeight, 160);
    ta.style.height = `${newHeight}px`;
  }, [chatInput]);

  return (
    <div className="px-4 pb-4 select-none">
      <div className="glass-composer">
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3 pb-1">
            {attachedFiles.map((chip, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 px-3 py-1 bg-surface border border-default rounded-full text-xs text-default font-semibold"
              >
                <FileText className="w-3.5 h-3.5 shrink-0 text-muted" />
                <span className="truncate max-w-[120px]">{chip.file.name}</span>
                {chip.uploading ? (
                  <RefreshCw className="w-3 h-3 animate-spin shrink-0 ml-1 text-soft" />
                ) : (
                  <button
                    type="button"
                    onClick={() => chip.id && onRemoveFile(chip.id)}
                    className="text-soft hover:text-default ml-1 text-xs shrink-0"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        <form ref={formRef} onSubmit={onSend} className="flex items-end gap-1 p-1.5">
          <input
            type="file"
            ref={fileInputRef}
            onChange={onFileUpload}
            className="hidden"
            multiple
          />

          <button
            type="button"
            onClick={handleTriggerUpload}
            className="p-2 rounded-lg text-muted hover-text-default hover-bg-surface transition-all shrink-0 self-end"
            title="Attach files"
          >
            <Plus className="w-5 h-5" />
          </button>

          <textarea
            ref={textareaRef}
            value={chatInput}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            rows={1}
            className="flex-1 bg-transparent border-0 focus:outline-none focus:ring-0 text-sm text-default placeholder-soft px-1 py-2 resize-none max-h-[160px] leading-relaxed"
          />

          <button
            type="button"
            onClick={onToggleVoice}
            className={`p-2 rounded-lg transition-all shrink-0 self-end ${
              voiceState === "listening"
                ? "bg-[var(--color-danger)]/15 text-[var(--color-danger)] animate-pulse"
                : "text-muted hover-text-default hover-bg-surface"
            }`}
            title={voiceState === "unsupported" ? "Voice not supported" : "Voice input"}
          >
            {voiceState === "listening" ? (
              <Mic className="w-4 h-4" />
            ) : (
              <MicOff className="w-4 h-4" />
            )}
          </button>

          <button
            type="submit"
            disabled={isChatSending || (!chatInput.trim() && attachedFiles.length === 0)}
            className="p-2 rounded-lg bg-raised hover-bg-surface text-default transition-all shrink-0 self-end border border-default"
          >
            <CornerDownLeft className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
