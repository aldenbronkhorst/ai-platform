import type { ChatMessage } from "../../types";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { TechnicalDetails } from "./TechnicalDetails";
import { PendingAssistant } from "./PendingAssistant";
import { FailedMessage } from "./FailedMessage";
import { MessageActions } from "./MessageActions";
import { EditMessage } from "./EditMessage";

interface MessageBubbleProps {
  message: ChatMessage;
  onRetry: () => void;
  onCopy?: (content: string) => void;
  onEdit?: (messageId: string) => void;
  isEditing?: boolean;
  editedContent?: string;
  onEditedContentChange?: (content: string) => void;
  onEditSave?: (newContent: string) => void;
  onEditCancel?: () => void;
  isEditSaving?: boolean;
}

export function MessageBubble({
  message,
  onRetry,
  onCopy,
  onEdit,
  isEditing,
  onEditSave,
  onEditCancel,
  isEditSaving,
}: MessageBubbleProps) {
  if (message.role === "user") {
    if (isEditing) {
      return (
        <div className="flex justify-end">
          <EditMessage
            initialContent={message.content}
            onSave={(c) => onEditSave?.(c)}
            onCancel={() => onEditCancel?.()}
            isSaving={isEditSaving}
          />
        </div>
      );
    }

    return (
      <div className="group flex justify-end">
        <div className="flex flex-col items-end max-w-[70%]">
          <div className="w-fit p-3.5 rounded-2xl bg-raised border border-default text-xs leading-relaxed whitespace-pre-wrap rounded-tr-none shadow-sm">
            {message.content}
          </div>
          <div className="mt-1">
            <MessageActions
              role="user"
              content={message.content}
              onCopy={() => onCopy?.(message.content)}
              onEdit={() => onEdit?.(message.id)}
            />
          </div>
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
    <div className="group max-w-[80%]">
      <div className="text-sm leading-relaxed">
        <MarkdownRenderer content={message.content} />
      </div>

      <div className="mt-1">
        <MessageActions
          role="assistant"
          content={message.content}
          onCopy={() => onCopy?.(message.content)}
        />
      </div>

      {technicalDetails && (
        <TechnicalDetails data={technicalDetails} />
      )}
    </div>
  );
}
