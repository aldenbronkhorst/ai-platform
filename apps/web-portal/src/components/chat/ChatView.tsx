import { useRef, useState, useCallback, useEffect, useLayoutEffect } from "react";
import { ChevronDown } from "lucide-react";
import type { ChatMessage, ChatSession, AttachedFile, VoiceState } from "../../types";
import { ChatComposer } from "./ChatComposer";
import { ChatEmptyState } from "./ChatEmptyState";
import { MessageBubble } from "./MessageBubble";

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
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
  onRetryMessage: (messageId: string) => void;
  onCopyMessage: (content: string) => void;
  onEditResend: (originalMessageId: string, newContent: string) => void;
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
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
  onRetryMessage,
  onCopyMessage,
  onEditResend,
  placeholder,
}: ChatViewProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [isEditSaving, setIsEditSaving] = useState(false);
  const [composerHeight, setComposerHeight] = useState(0);
  const [isScrollbarVisible, setIsScrollbarVisible] = useState(false);
  const scrollbarTimerRef = useRef<number | null>(null);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isNearBottom = el.scrollTop < 100;
    setIsUserScrolledUp(!isNearBottom);
    setIsScrollbarVisible(true);
    if (scrollbarTimerRef.current) window.clearTimeout(scrollbarTimerRef.current);
    scrollbarTimerRef.current = window.setTimeout(() => {
      setIsScrollbarVisible(false);
      scrollbarTimerRef.current = null;
    }, 900);
  }, []);

  const scrollToBottom = useCallback(() => {
    scrollContainerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    setIsUserScrolledUp(false);
  }, []);

  const handleEditSave = useCallback(async (newContent: string) => {
    if (!editingMessageId || !newContent.trim()) return;
    setIsEditSaving(true);
    setEditingMessageId(null);
    await onEditResend(editingMessageId, newContent);
    setIsEditSaving(false);
  }, [editingMessageId, onEditResend]);

  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setComposerHeight(entry.contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (scrollbarTimerRef.current) window.clearTimeout(scrollbarTimerRef.current);
  }, []);

  if (isMessagesLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="flex items-center gap-2 text-muted text-sm">
          <div className="w-4 h-4 border-2 border-muted border-t-default rounded-full animate-spin" />
          Loading messages…
        </div>
      </div>
    );
  }

  const hasMessages = activeSession && chatMessages.length > 0;

  return (
    <div className="h-full min-h-0 flex flex-col max-w-4xl mx-auto relative w-full overflow-hidden">
      {hasMessages ? (
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className={`flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 sm:px-2 py-3 sm:py-4 flex flex-col-reverse gap-4 scrollbar-transient ${isScrollbarVisible ? "scrollbar-visible" : ""}`}
        >
          <div ref={messagesEndRef} />
          {[...chatMessages].reverse().map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onRetry={() => onRetryMessage(msg.id)}
              onCopy={onCopyMessage}
              onEdit={setEditingMessageId}
              isEditing={editingMessageId === msg.id}
              onEditSave={handleEditSave}
              onEditCancel={() => setEditingMessageId(null)}
              isEditSaving={isEditSaving}
            />
          ))}
        </div>
      ) : (
        <div className="flex-1 min-h-0 flex flex-col overflow-y-auto overscroll-contain">
          <ChatEmptyState displayName={displayName} />
        </div>
      )}

      {isUserScrolledUp && hasMessages && (
        <div
          className="absolute left-0 right-0 flex justify-center pointer-events-none"
          style={{ bottom: composerHeight + 20 }}
        >
          <button
            onClick={scrollToBottom}
            className="pointer-events-auto flex items-center gap-1 px-3 py-1.5 rounded-full bg-surface border border-default text-xs font-semibold text-muted hover:text-default shadow-sm transition-all"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Jump to latest
          </button>
        </div>
      )}

      <div ref={composerRef} className="relative z-10 shrink-0">
        <ChatComposer
          chatInput={chatInput}
          attachedFiles={attachedFiles}
          voiceInterimTranscript={voiceInterimTranscript}
          voiceState={voiceState}
          isChatSending={isChatSending}
          placeholder={placeholder}
          onInputChange={onInputChange}
          onSend={onSend}
          onRemoveFile={onRemoveFile}
          onTriggerUpload={onTriggerUpload}
          onToggleVoice={onToggleVoice}
        />
      </div>
    </div>
  );
}
