import { useRef, useCallback, useEffect, useLayoutEffect, useState } from "react";
import { Plus, Mic, ArrowUp, Square } from "lucide-react";
import type { AttachedFile, VoiceState } from "../../types";
import { FileAttachmentTile } from "./FileAttachmentTile";

const COMPOSER_STACK_BREAKPOINT_PX = 320;
const COMPOSER_SINGLE_LINE_MAX_PX = 36;

interface ChatComposerProps {
  chatInput: string;
  attachedFiles: AttachedFile[];
  voiceInterimTranscript: string;
  voiceState: VoiceState;
  isChatSending: boolean;
  focusKey?: string | null;
  placeholder?: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onStop: () => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
  isThreadScrolledUp?: boolean;
}

export function ChatComposer({
  chatInput,
  attachedFiles,
  voiceInterimTranscript,
  voiceState,
  isChatSending,
  focusKey,
  placeholder = "Ask anything...",
  onInputChange,
  onSend,
  onStop,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
  isThreadScrolledUp = false,
}: ChatComposerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isComposerExpanded, setIsComposerExpanded] = useState(false);
  const [isComposerTight, setIsComposerTight] = useState(false);
  const lastTightRef = useRef<boolean | null>(null);

  const formRef = useRef<HTMLFormElement>(null);
  const hasPendingUpload = attachedFiles.some(file => file.uploading);
  const hasFailedUpload = attachedFiles.some(file => Boolean(file.error));
  const cleanVoiceInterim = voiceInterimTranscript.trim();
  const hasComposerPreview = attachedFiles.length > 0 || cleanVoiceInterim.length > 0;
  const canSubmit = !isChatSending
    && !hasPendingUpload
    && !hasFailedUpload
    && (chatInput.trim() || attachedFiles.length > 0);

  const focusInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;

    const focus = () => el.focus({ preventScroll: true });
    focus();
    window.requestAnimationFrame(focus);
    window.setTimeout(focus, 0);
  }, []);

  useEffect(() => {
    focusInput();
  }, [focusInput, focusKey]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSubmit) {
        formRef.current?.requestSubmit();
      }
    }
  }, [canSubmit]);

  const handleInputChange = useCallback((value: string) => {
    if (!value || !value.trimEnd().includes("\n")) {
      setIsComposerExpanded(false);
    }
    onInputChange(value);
  }, [onInputChange]);

  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const maxHeight = window.innerWidth < 640 ? 112 : 160;
    const newHeight = Math.min(ta.scrollHeight, maxHeight);
    ta.style.height = `${newHeight}px`;
  }, [chatInput]);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const surface = surfaceRef.current;
    const textarea = textareaRef.current;
    if (!root || !surface || !textarea) return undefined;

    const syncComposerMetrics = () => {
      const { width } = root.getBoundingClientRect();
      if (width > 0) {
        const nextTight = width < COMPOSER_STACK_BREAKPOINT_PX;
        if (nextTight !== lastTightRef.current) {
          lastTightRef.current = nextTight;
          setIsComposerTight(nextTight);
        }
      }
      const nextExpanded = Boolean(chatInput) && (
        chatInput.trimEnd().includes("\n") || textarea.scrollHeight > COMPOSER_SINGLE_LINE_MAX_PX
      );
      setIsComposerExpanded(previous => previous === nextExpanded ? previous : nextExpanded);
    };

    syncComposerMetrics();
    const observer = new ResizeObserver(syncComposerMetrics);
    observer.observe(root);
    observer.observe(surface);
    observer.observe(textarea);
    return () => observer.disconnect();
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
  const controlButtonClass = "h-6 w-6 inline-flex items-center justify-center rounded-md transition-colors shrink-0 [&>svg]:block";
  const composerIconClass = "h-4 w-4";
  const openStrokeIconClass = "h-[18px] w-[18px]";
  const idleControlClass = "text-[var(--ui-text-secondary)] hover:text-foreground";
  const canUseActionButton = isChatSending || canSubmit;
  const isStacked = isComposerExpanded || isComposerTight;
  const sendButtonClass = `h-[1.625rem] w-[1.625rem] inline-flex shrink-0 items-center justify-center rounded-full transition-colors [&>svg]:block ${
    isChatSending
      ? "border border-[var(--ui-stroke-secondary)] bg-[var(--dt-card)] text-foreground hover:border-[var(--ui-stroke-primary)]"
      : canSubmit
      ? "border border-[var(--ui-stroke-tertiary)] bg-[var(--dt-card)] text-[var(--ui-text-secondary)] hover:border-[var(--ui-stroke-secondary)] hover:text-foreground"
      : "border border-[var(--ui-stroke-tertiary)] bg-[var(--dt-card)] text-[var(--ui-text-tertiary)]"
  } disabled:cursor-not-allowed`;
  const textareaClass = "block w-full min-w-0 min-h-[1.625rem] bg-transparent border-0 focus:outline-none focus:ring-0 text-base sm:text-[0.8125rem] text-foreground placeholder:text-[var(--ui-text-tertiary)] px-1 py-1 resize-none max-h-28 sm:max-h-[9.375rem] leading-normal align-middle";
  const formClass = `${isStacked ? "items-center gap-x-1 gap-y-1" : "items-center gap-1"} grid grid-cols-[auto_minmax(0,1fr)_auto] px-2 py-1.5`;
  const uploadSlotClass = isStacked ? "col-start-1 row-start-2 flex items-center" : "col-start-1 row-start-1 flex items-center";
  const textareaSlotClass = isStacked ? "col-span-3 row-start-1 flex min-w-0 items-center px-1" : "col-start-2 row-start-1 flex min-w-0 items-center";
  const actionSlotClass = isStacked
    ? "col-start-3 row-start-2 flex items-center justify-self-end gap-1"
    : "col-start-3 row-start-1 flex items-center gap-1";

  const uploadButton = (
    <button
      type="button"
      onClick={onTriggerUpload}
      className={`${controlButtonClass} ${idleControlClass}`}
      title="Attach files"
    >
      <Plus className={openStrokeIconClass} />
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
          : "text-[var(--ui-text-secondary)] hover:bg-[var(--ui-control-hover-background)] hover:text-foreground"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
      title={isVoiceDisabled ? "Voice not supported" : isVoiceProcessing ? "Transcribing voice input" : isListening ? "Stop listening" : "Voice input"}
    >
      <Mic className={composerIconClass} />
    </button>
  );

  const sendButton = (
    <button
      type={isChatSending ? "button" : "submit"}
      disabled={!canUseActionButton}
      onClick={isChatSending ? onStop : undefined}
      className={sendButtonClass}
      title={isChatSending ? "Stop generating" : hasPendingUpload ? "Waiting for file upload" : hasFailedUpload ? "Remove failed upload" : "Send message"}
    >
      {isChatSending ? <Square className="h-3.5 w-3.5 fill-current" /> : <ArrowUp className={openStrokeIconClass} />}
    </button>
  );

  const textarea = (
    <textarea
      ref={textareaRef}
      value={chatInput}
      onChange={(e) => handleInputChange(e.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={composerPlaceholder}
      rows={1}
      data-slot="composer-rich-input"
      className={textareaClass}
    />
  );

  return (
    <div
      ref={rootRef}
      className="conversation-composer-root pointer-events-auto select-none"
      data-slot="composer-root"
      data-stacked={isStacked ? "" : undefined}
      data-thread-scrolled-up={isThreadScrolledUp ? "" : undefined}
    >
      <div aria-hidden className="pointer-events-none absolute inset-0 rounded-[inherit]" data-slot="composer-underlay" />
      <div ref={surfaceRef} className="composer-surface" data-slot="composer-surface">
        {hasComposerPreview && (
          <div className="flex flex-wrap gap-2 px-3 pt-2 pb-1">
            {cleanVoiceInterim && (
              <div
                className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-2.5 py-1 text-xs font-medium text-foreground"
                title={cleanVoiceInterim}
              >
                <Mic className="h-3.5 w-3.5 shrink-0 text-[var(--color-warning)]" />
                <span className="max-w-[260px] truncate sm:max-w-[420px]">{cleanVoiceInterim}</span>
              </div>
            )}
            {attachedFiles.map((chip) => (
              <FileAttachmentTile
                attachment={chip}
                key={chip.id || `${chip.file.name}-${chip.file.lastModified}`}
                onRemove={onRemoveFile}
                variant="composer"
              />
            ))}
          </div>
        )}

        <form
          ref={formRef}
          onSubmit={onSend}
          className={formClass}
        >
          <div className={uploadSlotClass}>
            {uploadButton}
          </div>
          <div className={textareaSlotClass}>
            {textarea}
          </div>
          <div className={actionSlotClass}>
            {voiceButton}
            {sendButton}
          </div>
        </form>
      </div>
    </div>
  );
}
