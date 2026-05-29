import { User, Sparkles } from "lucide-react";
import type { ChatMessage } from "../../types";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TechnicalDetails } from "./TechnicalDetails";
import { PendingAssistant } from "./PendingAssistant";
import { FailedMessage } from "./FailedMessage";

interface MessageBubbleProps {
  message: ChatMessage;
  onRetry: () => void;
}

export function MessageBubble({ message, onRetry }: MessageBubbleProps) {
  if (message.role === "user") {
    return (
      <div className="flex gap-3 justify-end">
        <div className="max-w-[70%] p-3.5 rounded-2xl border bg-surface border-default text-xs leading-relaxed whitespace-pre-wrap rounded-tr-none shadow-sm">
          {message.content}
        </div>
        <div className="w-8 h-8 rounded-lg bg-surface border border-default flex items-center justify-center shrink-0">
          <User className="w-4 h-4 text-muted" />
        </div>
      </div>
    );
  }

  if (message.status === "pending" || message.status === "sending") {
    return <PendingAssistant toolHint={message.model_name} />;
  }

  if (message.status === "failed") {
    return <FailedMessage errorMessage={message.error_message} onRetry={onRetry} />;
  }

  // Completed assistant message
  const metadata = message.metadata_json;
  const technicalDetails = metadata?.technical_details;

  return (
    <div className="flex gap-3 justify-start">
      <div className="w-8 h-8 rounded-lg bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0">
        <Sparkles className="w-4 h-4 text-accent" />
      </div>

      <div className="flex-1 min-w-0 max-w-[80%]">
        <div className="p-4 rounded-2xl border bg-canvas border-default text-sm leading-relaxed rounded-tl-none">
          <MarkdownRenderer content={message.content} />
        </div>

        {technicalDetails && (
          <TechnicalDetails data={technicalDetails} />
        )}
      </div>
    </div>
  );
}
