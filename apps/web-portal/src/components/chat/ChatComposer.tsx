import { useRef, useCallback, useLayoutEffect, useState } from "react";
import { AlertCircle, Plus, Mic, CornerDownLeft, FileText, RefreshCw, X } from "lucide-react";
import type { AttachedFile, VoiceState } from "../../types";

interface ChatComposerProps {
  chatInput: string;
  attachedFiles: AttachedFile[];
  voiceInterimTranscript: string;
  voiceState: VoiceState;
  isChatSending: boolean;
  placeholder?: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
}

export function ChatComposer({
  chatInput,
  attachedFiles,
  voiceInterimTranscript,
  voiceState,
  isChatSending,
  placeholder = "Ask anything...",
  onInputChange,
  onSend,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
}: ChatComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isComposerExpanded, setIsComposerExpanded] = useState(false);

  const formRef = useRef<HTMLFormElement>(null);
  const hasPendingUpload = attachedFiles.some(file => file.uploading);
  const hasFailedUpload = attachedFiles.some(file => Boolean(file.error));
  const cleanVoiceInterim = voiceInterimTranscript.trim();
  const hasComposerPreview = attachedFiles.length > 0 || cleanVoiceInterim.length > 0;
  const canSubmit = !isChatSending
    && !hasPendingUpload
    && !hasFailedUpload
    && (chatInput.trim() || attachedFiles.length > 0);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSubmit) {
        formRef.current?.requestSubmit();
      }
    }
  }, [canSubmit]);

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const maxHeight = window.innerWidth < 640 ? 112 : 160;
    const newHeight = Math.min(ta.scrollHeight, maxHeight);
    ta.style.height = `${newHeight}px`;
    const stackThreshold = window.innerWidth < 640 ? 44 : 96;
    const shouldExpand = chatInput.includes("\n") || chatInput.length > stackThreshold || ta.scrollHeight > 48;
    setIsComposerExpanded(prev => prev === shouldExpand ? prev : shouldExpand);
  }, [chatInput]);

  const isListening = voiceState === "listening";
  const isVoiceProcessing = voiceState === "processing";
  const isVoiceDisabled = voiceState === "unsupported";
  const composerPlaceholder = isListening
    ? "Listening..."
    : isVoiceProcessing
      ? "Transcribing..."
    : voiceState === "denied"
      ? "Microphone access blocked"
      : placeholder;
  const controlButtonClass = "h-9 w-9 inline-flex items-center justify-center rounded-lg transition-all shrink-0 [&>svg]:block";
  const idleControlClass = "text-muted hover-text-default hover-bg-surface";
  const textareaClass = `${isComposerExpanded ? "w-full" : "flex-1"} min-w-0 min-h-9 bg-transparent border-0 focus:outline-none focus:ring-0 text-base sm:text-sm text-default placeholder-soft px-1 py-2 resize-none max-h-28 sm:max-h-[160px] leading-5`;

  const uploadButton = (
    <button
      type="button"
      onClick={onTriggerUpload}
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
        isListening || isVoiceProcessing
          ? "bg-[var(--color-warning)] text-white shadow-sm"
          : "text-muted hover-text-default hover-bg-surface"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
      title={isVoiceDisabled ? "Voice not supported" : isVoiceProcessing ? "Transcribing voice input" : isListening ? "Stop listening" : "Voice input"}
    >
      <Mic className="w-4 h-4" />
    </button>
  );

  const sendButton = (
    <button
      type="submit"
      disabled={!canSubmit}
      className={`${controlButtonClass} bg-raised hover-bg-surface text-default border border-default disabled:opacity-40 disabled:cursor-not-allowed`}
      title={hasPendingUpload ? "Waiting for file upload" : hasFailedUpload ? "Remove failed upload" : "Send message"}
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
        {hasComposerPreview && (
          <div className="flex flex-wrap gap-2 px-3 pt-3 pb-1">
            {cleanVoiceInterim && (
              <div
                className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-2.5 py-1 text-xs font-medium text-default"
                title={cleanVoiceInterim}
              >
                <Mic className="h-3.5 w-3.5 shrink-0 text-[var(--color-warning)]" />
                <span className="max-w-[260px] truncate sm:max-w-[420px]">{cleanVoiceInterim}</span>
              </div>
            )}
            {attachedFiles.map((chip) => (
              <div
                key={chip.id || `${chip.file.name}-${chip.file.lastModified}`}
                className={`flex items-center gap-1.5 px-3 py-1 bg-surface border rounded-full text-xs font-semibold ${
                  chip.error
                    ? "border-[var(--color-danger)]/60 text-[var(--color-danger)]"
                    : "border-default text-default"
                }`}
                title={chip.error || chip.file.name}
              >
                {chip.error ? (
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                ) : (
                  <FileText className="w-3.5 h-3.5 shrink-0 text-muted" />
                )}
                <span className="truncate max-w-[120px]">{chip.file.name}</span>
                {chip.uploading ? (
                  <RefreshCw className="w-3 h-3 animate-spin shrink-0 ml-1 text-soft" />
                ) : (
                  <>
                    {chip.error && <span className="text-[10px] uppercase tracking-wide">Failed</span>}
                  <button
                    type="button"
                    onClick={() => chip.id && onRemoveFile(chip.id)}
                    className="text-soft hover:text-default ml-1 shrink-0"
                    title="Remove file"
                  >
                    <X className="w-3 h-3" />
                  </button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        <form
          ref={formRef}
          onSubmit={onSend}
          className={`${isComposerExpanded ? "flex flex-col gap-1.5" : "flex items-center gap-1"} p-1 sm:p-1.5`}
        >
          {isComposerExpanded ? (
            <>
              <div className="w-full px-1">
                {textarea}
              </div>
              <div className="flex w-full items-center justify-between">
                <div className="flex items-center gap-1">
                  {uploadButton}
                </div>
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
