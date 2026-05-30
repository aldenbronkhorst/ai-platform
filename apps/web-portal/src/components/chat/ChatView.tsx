import { useRef, useState, useCallback, useLayoutEffect } from "react";
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
  voiceState: VoiceState;
  isMessagesLoading: boolean;
  isChatSending: boolean;
  displayName: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
  onRetryMessage: (messageId: string) => void;
  onSuggestionClick: (prompt: string) => void;
  onCopyMessage: (content: string) => void;
  onEditResend: (originalMessageId: string, newContent: string) => void;
  placeholder?: string;
}

export function ChatView({
  activeSession,
  chatMessages,
  chatInput,
  attachedFiles,
  voiceState,
  isMessagesLoading,
  isChatSending,
  displayName,
  onInputChange,
  onSend,
  onFileUpload,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
  onRetryMessage,
  onSuggestionClick,
  onCopyMessage,
  onEditResend,
  placeholder,
}: ChatViewProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const prevSessionIdRef = useRef<string | null>(null);
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [isEditSaving, setIsEditSaving] = useState(false);
  const [composerHeight, setComposerHeight] = useState(0);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    setIsUserScrolledUp(!isNearBottom);
  }, []);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    setIsUserScrolledUp(false);
  }, []);

  // useLayoutEffect fires synchronously before paint — no visible jump
  useLayoutEffect(() => {
    if (chatMessages.length === 0) return;

    const isNewSession = activeSession?.id && activeSession.id !== prevSessionIdRef.current;
    if (isNewSession) {
      prevSessionIdRef.current = activeSession.id;
    }

    if (isNewSession) {
      // Opening a new chat — instant scroll, no animation
      messagesEndRef.current?.scrollIntoView({ behavior: "instant" });
      setIsUserScrolledUp(false);
    } else if (!isUserScrolledUp) {
      // New message while at bottom — smooth scroll
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [chatMessages, isUserScrolledUp, activeSession?.id]);

  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setComposerHeight(entry.contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const handleEditSave = useCallback(async (newContent: string) => {
    if (!editingMessageId || !newContent.trim()) return;
    setIsEditSaving(true);
    setEditingMessageId(null);
    await onEditResend(editingMessageId, newContent);
    setIsEditSaving(false);
  }, [editingMessageId, onEditResend]);

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

  if (!activeSession || chatMessages.length === 0) {
    return (
      <div className="h-full flex flex-col">
        <div className="flex-1 flex flex-col">
          <ChatEmptyState displayName={displayName} onSuggestion={onSuggestionClick} />
        </div>
        <div ref={composerRef}>
          <ChatComposer
            chatInput={chatInput}
            attachedFiles={attachedFiles}
            voiceState={voiceState}
            isChatSending={isChatSending}
            placeholder={placeholder}
            onInputChange={onInputChange}
            onSend={onSend}
            onFileUpload={onFileUpload}
            onRemoveFile={onRemoveFile}
            onTriggerUpload={onTriggerUpload}
            onToggleVoice={onToggleVoice}
          />
        </div>
        {voiceState === "listening" && (
          <div className="text-center pb-2 text-xs text-danger font-semibold flex items-center justify-center gap-1 animate-pulse">
            Speak now…
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col max-w-4xl mx-auto relative">
      {/* 1. Messages scroll viewport */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto space-y-4 px-2 py-4 scroll-smooth"
      >
        {chatMessages.map((msg) => (
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
        <div ref={messagesEndRef} />
      </div>

      {/* 2. Jump-to-latest floating control – sits above composer dock */}
      {isUserScrolledUp && (
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

      {/* 3. Composer dock – always at bottom */}
      <div ref={composerRef} className="relative z-10">
        <ChatComposer
          chatInput={chatInput}
          attachedFiles={attachedFiles}
          voiceState={voiceState}
          isChatSending={isChatSending}
          placeholder={placeholder}
          onInputChange={onInputChange}
          onSend={onSend}
          onFileUpload={onFileUpload}
          onRemoveFile={onRemoveFile}
          onTriggerUpload={onTriggerUpload}
          onToggleVoice={onToggleVoice}
        />
      </div>

      {voiceState === "listening" && (
        <div className="text-center pb-2 text-xs text-danger font-semibold flex items-center justify-center gap-1 animate-pulse">
          Speak now…
        </div>
      )}
    </div>
  );
}
