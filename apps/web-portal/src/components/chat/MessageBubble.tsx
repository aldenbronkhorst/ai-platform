import { FileText } from "lucide-react";
import type { ChatAttachment, ChatMessage } from "../../types";
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isChatAttachment(value: unknown): value is ChatAttachment {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.filename === "string"
    && typeof value.mime_type === "string"
    && typeof value.artifact_type === "string";
}

function messageAttachments(message: ChatMessage): ChatAttachment[] {
  if (Array.isArray(message.attachments)) {
    return message.attachments.filter(isChatAttachment);
  }

  const metadata = isRecord(message.metadata_json) ? message.metadata_json : {};
  return Array.isArray(metadata.attachments) ? metadata.attachments.filter(isChatAttachment) : [];
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
    const attachments = messageAttachments(message);
    const hasContent = message.content.trim().length > 0;

    if (isEditing) {
      return (
        <div className="w-full flex justify-center">
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
      <div className="w-full flex justify-end">
        <div className="flex flex-col max-w-[84%] sm:max-w-[70%] min-w-0">
          <div className="group relative flex flex-col items-end gap-1">
            {hasContent && (
              <div className="w-fit max-w-full p-3.5 rounded-2xl bg-raised border border-default text-xs leading-relaxed whitespace-pre-wrap break-words rounded-tr-none shadow-sm">
                {message.content}
              </div>
            )}
            {attachments.length > 0 && (
              <div className="flex max-w-full flex-wrap justify-end gap-1.5">
                {attachments.map(attachment => (
                  <div
                    key={attachment.id}
                    className="flex min-w-0 items-center gap-1.5 rounded-lg border border-default bg-surface px-2.5 py-1 text-[11px] font-semibold text-muted"
                    title={attachment.filename}
                  >
                    <FileText className="h-3.5 w-3.5 shrink-0 text-soft" />
                    <span className="max-w-[160px] truncate">{attachment.filename}</span>
                  </div>
                ))}
              </div>
            )}
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
    return <PendingAssistant message={message} />;
  }

  if (message.status === "failed") {
    return <FailedMessage errorMessage={message.error_message} onRetry={onRetry} />;
  }

  const metadata = message.metadata_json && typeof message.metadata_json === "object"
    ? message.metadata_json as Record<string, unknown>
    : null;
  const technicalDetails = metadata?.technical_details;

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-none min-w-0">
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

        {Boolean(technicalDetails) && (
          <TechnicalDetails data={technicalDetails} />
        )}
      </div>
    </div>
  );
}
