import { useRef, useCallback, useLayoutEffect, useState } from "react";
import { Plus, Mic, CornerDownLeft, FileText, RefreshCw } from "lucide-react";
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
  const [isComposerExpanded, setIsComposerExpanded] = useState(false);

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

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const maxHeight = window.innerWidth < 640 ? 112 : 160;
    const newHeight = Math.min(ta.scrollHeight, maxHeight);
    ta.style.height = `${newHeight}px`;
    const stackThreshold = window.innerWidth < 640 ? 44 : 96;
    setIsComposerExpanded(chatInput.includes("\n") || chatInput.length > stackThreshold);
  }, [chatInput, isComposerExpanded]);

  const isListening = voiceState === "listening";
  const isVoiceDisabled = voiceState === "unsupported";
  const composerPlaceholder = isListening
    ? "Listening..."
    : voiceState === "denied"
      ? "Microphone access blocked"
      : placeholder;
  const controlButtonClass = "h-9 w-9 inline-flex items-center justify-center rounded-lg transition-all shrink-0 [&>svg]:block";
  const idleControlClass = "text-muted hover-text-default hover-bg-surface";
  const textareaClass = `${isComposerExpanded ? "w-full" : "flex-1"} min-h-9 bg-transparent border-0 focus:outline-none focus:ring-0 text-base sm:text-sm text-default placeholder-soft px-1 py-2 resize-none max-h-28 sm:max-h-[160px] leading-5`;

  const uploadButton = (
    <button
      type="button"
      onClick={handleTriggerUpload}
      className={`${controlButtonClass} ${idleControlClass}`}
      title="Attach files"
    >
      <Plus className="w-5 h-5" />
    </button>
  );

  const voiceButton = (
    <button
      type="button"
      onClick={onToggleVoice}
      disabled={isVoiceDisabled}
      className={`${controlButtonClass} ${
        isListening
          ? "bg-[var(--color-danger)]/15 text-[var(--color-danger)]"
          : "text-muted hover-text-default hover-bg-surface"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
      title={isVoiceDisabled ? "Voice not supported" : isListening ? "Stop listening" : "Voice input"}
    >
      <Mic className="w-4 h-4" />
    </button>
  );

  const sendButton = (
    <button
      type="submit"
      disabled={isChatSending || (!chatInput.trim() && attachedFiles.length === 0)}
      className={`${controlButtonClass} bg-raised hover-bg-surface text-default border border-default disabled:opacity-40 disabled:cursor-not-allowed`}
      title="Send message"
    >
      <CornerDownLeft className="w-4 h-4" />
    </button>
  );

  const textarea = (
    <textarea
      ref={textareaRef}
      value={chatInput}
      onChange={(e) => onInputChange(e.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={composerPlaceholder}
      rows={1}
      className={textareaClass}
    />
  );

  return (
    <div className="px-3 pb-3 sm:px-4 sm:pb-4 select-none">
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

        <form
          ref={formRef}
          onSubmit={onSend}
          className={isComposerExpanded ? "flex flex-col gap-1 p-1 sm:p-1.5" : "flex items-center gap-1 p-1 sm:p-1.5"}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={onFileUpload}
            className="hidden"
            multiple
          />

          {isComposerExpanded ? (
            <>
              {textarea}
              <div className="flex min-h-9 items-center justify-between gap-2">
                {uploadButton}
                <div className="flex items-center gap-1">
                  {voiceButton}
                  {sendButton}
                </div>
              </div>
            </>
          ) : (
            <>
              {uploadButton}
              {textarea}
              {voiceButton}
              {sendButton}
            </>
          )}
        </form>
      </div>
    </div>
  );
}
