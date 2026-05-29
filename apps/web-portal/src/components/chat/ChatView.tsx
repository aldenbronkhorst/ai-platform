import { useEffect, useRef, useState, useCallback } from "react";
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
  placeholder,
}: ChatViewProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    setIsUserScrolledUp(false);
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    setIsUserScrolledUp(!isNearBottom);
  }, []);

  useEffect(() => {
    if (!isUserScrolledUp) {
      scrollToBottom();
    }
  }, [chatMessages, isUserScrolledUp, scrollToBottom]);

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
        {voiceState === "listening" && (
          <div className="text-center pb-2 text-xs text-danger font-semibold flex items-center justify-center gap-1 animate-pulse">
            Speak now…
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col max-w-4xl mx-auto">
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
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {isUserScrolledUp && (
        <div className="flex justify-center -mb-2 relative z-10">
          <button
            onClick={scrollToBottom}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-surface border border-default text-xs font-semibold text-muted hover:text-default shadow-sm transition-all"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Jump to latest
          </button>
        </div>
      )}

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

      {voiceState === "listening" && (
        <div className="text-center pb-2 text-xs text-danger font-semibold flex items-center justify-center gap-1 animate-pulse">
          Speak now…
        </div>
      )}
    </div>
  );
}
