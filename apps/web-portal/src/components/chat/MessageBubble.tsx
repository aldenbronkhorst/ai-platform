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
      <div className="flex justify-end">
        <div className="max-w-[70%] p-3.5 rounded-2xl bg-raised border border-default text-xs leading-relaxed whitespace-pre-wrap rounded-tr-none shadow-sm">
          {message.content}
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

  const metadata = message.metadata_json;
  const technicalDetails = metadata?.technical_details;

  return (
    <div className="max-w-[80%]">
      <div className="text-sm leading-relaxed">
        <MarkdownRenderer content={message.content} />
      </div>

      {technicalDetails && (
        <TechnicalDetails data={technicalDetails} />
      )}
    </div>
  );
}
