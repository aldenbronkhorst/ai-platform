import { Bot, User, Shield, Mic } from "lucide-react";
import type { ChatMessage, ChatSession } from "../../types";
import { ChatComposer } from "./ChatComposer";
import type { AttachedFile, VoiceState } from "../../types";

interface ChatViewProps {
  activeSession: ChatSession | null;
  chatMessages: ChatMessage[];
  chatInput: string;
  attachedFiles: AttachedFile[];
  voiceState: VoiceState;
  isMessagesLoading: boolean;
  isChatSending: boolean;
  expandedTraceMsgs: Record<string, boolean>;
  displayName: string;
  onInputChange: (value: string) => void;
  onSend: (e: React.FormEvent) => void;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (id: string) => void;
  onTriggerUpload: () => void;
  onToggleVoice: () => void;
  onToggleTrace: (id: string) => void;
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
  expandedTraceMsgs,
  displayName,
  onInputChange,
  onSend,
  onFileUpload,
  onRemoveFile,
  onTriggerUpload,
  onToggleVoice,
  onToggleTrace,
  placeholder,
}: ChatViewProps) {
  const firstName = displayName.split(" ")[0];

  const renderMessages = () => {
    if (!activeSession) {
      return (
        <div className="text-center py-24 select-none space-y-3">
          <h3 className="text-xl font-extrabold text-default">
            How can I help you today, {firstName}?
          </h3>
          <p className="text-xs text-muted max-w-sm mx-auto leading-relaxed">
            Ask business operational questions, run audits, or check Odoo data.
          </p>
        </div>
      );
    }

    if (isMessagesLoading) {
      return <div className="text-center py-20 text-muted">Loading messages...</div>;
    }

    if (chatMessages.length === 0) {
      return (
        <div className="text-center py-20 text-muted select-none space-y-2">
          <p className="font-semibold text-default">
            This conversation has no messages yet.
          </p>
          <p className="text-xs text-soft max-w-xs mx-auto">
            Ask about credit notes, attendance, or Odoo accounts.
          </p>
        </div>
      );
    }

    return chatMessages.map((msg) => (
      <div
        key={msg.id}
        className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
      >
        {msg.role === "assistant" && (
          <div className="w-8 h-8 rounded-lg bg-surface border border-default flex items-center justify-center shrink-0">
            <Bot className="w-4 h-4 text-muted" />
          </div>
        )}

        <div
          className={`max-w-[75%] p-4 rounded-2xl border text-xs leading-relaxed whitespace-pre-wrap ${
            msg.role === "user"
              ? "bg-surface border-default text-default rounded-tr-none"
              : "bg-canvas border-default text-default rounded-tl-none"
          }`}
        >
          {msg.content}

          {msg.metadata_json?.technical_details && (
            <div className="mt-3 pt-3 border-t border-default">
              <button
                onClick={() => onToggleTrace(msg.id)}
                className="text-[10px] text-muted hover-text-default font-semibold flex items-center gap-1 select-none"
              >
                <Shield className="w-3 h-3" />
                {expandedTraceMsgs[msg.id]
                  ? "Hide technical trail"
                  : "View operational trail"}
              </button>
              {expandedTraceMsgs[msg.id] && (
                <pre className="mt-2.5 p-3 bg-canvas border border-default rounded-xl overflow-x-auto text-[10px] font-mono text-muted max-h-48 overflow-y-auto">
                  {JSON.stringify(msg.metadata_json.technical_details, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>

        {msg.role === "user" && (
          <div className="w-8 h-8 rounded-lg bg-surface border border-default flex items-center justify-center shrink-0">
            <User className="w-4 h-4 text-muted" />
          </div>
        )}
      </div>
    ));
  };

  return (
    <div className="h-full flex flex-col justify-between max-w-4xl mx-auto">
      <div className="flex-1 overflow-y-auto space-y-4 px-2 py-4">
        {renderMessages()}
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
        <div className="text-center pb-2 text-xs text-[var(--color-danger)] font-semibold flex items-center justify-center gap-1 animate-pulse">
          <Mic className="w-3.5 h-3.5" /> Speak now...
        </div>
      )}
    </div>
  );
}
