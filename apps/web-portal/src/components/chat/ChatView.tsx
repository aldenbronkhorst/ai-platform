import { useRef, useState, useCallback, useLayoutEffect, type CSSProperties } from "react";
import type { ChatMessage, ChatSession, AttachedFile, VoiceState } from "../../types";
import { chatMessageParts } from "../../chat/runtime";
import { AssistantMessages, ResponseLoadingIndicator } from "./AssistantMessages";
import { ChatComposer } from "./ChatComposer";
import { ChatEmptyState } from "./ChatEmptyState";
import { ThreadTimeline } from "./ThreadTimeline";

interface ChatViewProps {
  activeSession: ChatSession | null;
  chatMessages: ChatMessage[];
  chatInput: string;
  attachedFiles: AttachedFile[];
  voiceInterimTranscript: string;
  voiceState: VoiceState;
  isMessagesLoading: boolean;
  isChatSending: boolean;
  displayName: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onStop: () => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
  onRetryMessage: (messageId: string) => void;
  onCopyMessage: (content: string) => void;
  onEditResend: (originalMessageId: string, newContent: string) => void;
  onOpenAttachment?: (attachment: { id: string; filename: string; mime_type: string }) => void;
  placeholder?: string;
}

export function ChatView({
  activeSession,
  chatMessages,
  chatInput,
  attachedFiles,
  voiceInterimTranscript,
  voiceState,
  isMessagesLoading,
  isChatSending,
  displayName,
  onInputChange,
  onSend,
  onStop,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
  onRetryMessage,
  onCopyMessage,
  onEditResend,
  onOpenAttachment,
  placeholder,
}: ChatViewProps) {
  const composerRef = useRef<HTMLDivElement>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [isEditSaving, setIsEditSaving] = useState(false);
  const [composerMetrics, setComposerMetrics] = useState({ height: 0, surfaceHeight: 0 });

  const handleEditSave = useCallback(async (messageId: string, newContent: string) => {
    if (!newContent.trim()) return;
    setIsEditSaving(true);
    setEditingMessageId(null);
    await onEditResend(messageId, newContent);
    setIsEditSaving(false);
  }, [onEditResend]);

  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    const surface = el.querySelector<HTMLElement>("[data-slot='composer-surface']");
    const syncComposerMetrics = () => {
      const height = el.getBoundingClientRect().height;
      const surfaceHeight = surface?.getBoundingClientRect().height || 0;
      setComposerMetrics(previous => (
        previous.height === height && previous.surfaceHeight === surfaceHeight
          ? previous
          : { height, surfaceHeight }
      ));
    };
    const observer = new ResizeObserver(syncComposerMetrics);
    observer.observe(el);
    if (surface) observer.observe(surface);
    syncComposerMetrics();
    return () => observer.disconnect();
  }, []);

  const hasMessages = chatMessages.length > 0;
  const hasThread = Boolean(activeSession && (hasMessages || isMessagesLoading));
  const lastMessage = chatMessages.at(-1);
  const awaitingFirstResponsePart = Boolean(
    isChatSending
    && lastMessage?.role === "assistant"
    && chatMessageParts(lastMessage).length === 0,
  );
  const shellStyle = composerMetrics.height
    ? ({
      "--composer-measured-height": `${composerMetrics.height}px`,
      "--composer-surface-measured-height": `${composerMetrics.surfaceHeight || composerMetrics.height}px`,
    } as CSSProperties)
    : undefined;

  return (
    <div
      className="conversation-shell relative isolate flex h-full min-w-0 flex-col overflow-hidden"
      data-slot="composer-bounds"
      style={shellStyle}
    >
      {hasThread ? (
        <AssistantMessages
          editingMessageId={editingMessageId}
          isEditSaving={isEditSaving}
          isRunning={isChatSending}
          loadingIndicator={isMessagesLoading ? (
            <div className="conversation-turn-group" role="status" aria-live="polite">
              <div className="flex items-center gap-2 text-sm text-muted-foreground/75">
                <span aria-hidden="true" className="dither inline-block size-3 rounded-[2px] text-midground/80 animate-pulse" />
                Loading messages...
              </div>
            </div>
          ) : awaitingFirstResponsePart ? <ResponseLoadingIndicator /> : undefined}
          messages={chatMessages}
          sessionKey={activeSession?.id ?? null}
          onCopyMessage={onCopyMessage}
          onEdit={setEditingMessageId}
          onEditCancel={() => setEditingMessageId(null)}
          onEditSave={handleEditSave}
          onOpenAttachment={onOpenAttachment}
          onRetryMessage={onRetryMessage}
          onStop={onStop}
        />
      ) : (
        <div className="relative z-10 flex-1 min-h-0 flex flex-col overflow-y-auto overscroll-contain">
          <ChatEmptyState displayName={displayName} />
        </div>
      )}

      {hasMessages && <ThreadTimeline messages={chatMessages} />}

      <div ref={composerRef} className="conversation-composer-wrap z-30 overflow-visible">
        <ChatComposer
          chatInput={chatInput}
          attachedFiles={attachedFiles}
          voiceInterimTranscript={voiceInterimTranscript}
          voiceState={voiceState}
          isChatSending={isChatSending}
          focusKey={activeSession?.id ?? "new"}
          placeholder={placeholder}
          onInputChange={onInputChange}
          onSend={onSend}
          onStop={onStop}
          onRemoveFile={onRemoveFile}
          onTriggerUpload={onTriggerUpload}
          onToggleVoice={onToggleVoice}
        />
      </div>
    </div>
  );
}
