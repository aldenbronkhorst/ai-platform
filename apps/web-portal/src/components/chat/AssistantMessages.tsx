import {
  AssistantRuntimeProvider,
  ExportedMessageRepository,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiEvent,
  useAuiState,
  useMessageRuntime,
  type ReasoningMessagePartProps,
  type ThreadMessage,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { AlertCircle } from "lucide-react";
import {
  Children,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ComponentProps,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useStickToBottom } from "use-stick-to-bottom";

import type { ChatAttachment, ChatMessage } from "../../types";
import { cn } from "../../lib/utils";
import { useEnterAnimation } from "../../lib/use-enter-animation";
import { useIncrementalExternalStoreRuntime } from "../../lib/incremental-external-store-runtime";
import { ActivityTimerText } from "./ActivityTimerText";
import { CompactMarkdown } from "./CompactMarkdown";
import { useElapsedSeconds } from "./activity-timer";
import { DisclosureRow } from "./DisclosureRow";
import { EditMessage } from "./EditMessage";
import { FailedMessage } from "./FailedMessage";
import { FileAttachmentTile } from "./FileAttachmentTile";
import { MarkdownTextContent } from "./MarkdownRenderer";
import { MessageRenderBoundary } from "./MessageRenderBoundary";
import { MessageActions } from "./MessageActions";
import { UserMessageText } from "./UserMessageText";
import { Codicon } from "../ui/Codicon";
import { CopyButton } from "../ui/CopyButton";

interface AssistantMessagesProps {
  messages: ChatMessage[];
  isRunning: boolean;
  editingMessageId: string | null;
  isEditSaving: boolean;
  onCopyMessage?: (content: string) => void;
  onEdit: (messageId: string) => void;
  onEditCancel: () => void;
  onEditSave: (messageId: string, content: string) => void;
  onOpenAttachment?: (attachment: ChatAttachment) => void;
  onRetryMessage: (messageId: string) => void;
  onStop: () => void;
  loadingIndicator?: ReactNode;
  sessionKey?: string | null;
}

type RuntimePart =
  | { type: "text"; text: string }
  | { type: "reasoning"; text: string }
  | {
    type: "tool-call";
    toolCallId: string;
    toolName: string;
    args: unknown;
    argsText: string;
    result?: unknown;
    isError?: boolean;
    durationMs?: number;
  };

type ThreadMessageComponents = ComponentProps<typeof ThreadPrimitive.MessageByIndex>["components"];

type MessageGroup = { id: string; weight: number } & (
  | { index: number; kind: "standalone" }
  | { indices: number[]; kind: "turn" }
);

const RENDER_BUDGET = 300;
const DISCLOSURE_STATE_LIMIT = 240;
const disclosureStates = new Map<string, boolean>();
const disclosureListeners = new Set<() => void>();

function emitDisclosureChange() {
  disclosureListeners.forEach(listener => listener());
}

function subscribeDisclosureState(listener: () => void) {
  disclosureListeners.add(listener);
  return () => {
    disclosureListeners.delete(listener);
  };
}

function setDisclosureState(disclosureId: string, open: boolean) {
  if (disclosureStates.get(disclosureId) === open) return;

  if (!disclosureStates.has(disclosureId) && disclosureStates.size >= DISCLOSURE_STATE_LIMIT) {
    const oldest = disclosureStates.keys().next().value as string | undefined;
    if (oldest) disclosureStates.delete(oldest);
  }

  disclosureStates.set(disclosureId, open);
  emitDisclosureChange();
}

function useDisclosureOpen(disclosureId: string, defaultOpen = false) {
  const getSnapshot = useCallback(
    () => disclosureStates.get(disclosureId) ?? defaultOpen,
    [defaultOpen, disclosureId],
  );
  const open = useSyncExternalStore(subscribeDisclosureState, getSnapshot, getSnapshot);
  const setOpen = useCallback(
    (next: boolean | ((value: boolean) => boolean)) => {
      const current = disclosureStates.get(disclosureId) ?? defaultOpen;
      setDisclosureState(disclosureId, typeof next === "function" ? next(current) : next);
    },
    [defaultOpen, disclosureId],
  );

  return [open, setOpen] as const;
}

function stableDisclosureHash(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(31, hash) + value.charCodeAt(index);
  }
  return Math.abs(hash).toString(36);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown) {
  return typeof value === "string" ? value : "";
}

function runtimePartsFromMetadata(value: unknown): RuntimePart[] {
  if (!Array.isArray(value)) return [];

  return value.filter(isRecord).flatMap((part): RuntimePart[] => {
    if (part.type === "text") {
      const partText = text(part.text);
      return partText ? [{ type: "text", text: partText }] : [];
    }
    if (part.type === "reasoning") {
      const partText = text(part.text);
      return partText ? [{ type: "reasoning", text: partText }] : [];
    }
    if (part.type === "tool-call") {
      const toolCallId = text(part.toolCallId);
      if (!toolCallId) return [];
      const next: RuntimePart = {
        type: "tool-call",
        toolCallId,
        toolName: text(part.toolName) || "tool",
        args: "args" in part ? part.args : {},
        argsText: text(part.argsText),
      };
      if ("result" in part) next.result = part.result;
      if (typeof part.isError === "boolean") next.isError = part.isError;
      if (typeof part.durationMs === "number") next.durationMs = part.durationMs;
      return [next];
    }
    return [];
  });
}

function messageParts(message: ChatMessage): RuntimePart[] {
  const metadata = isRecord(message.metadata_json) ? message.metadata_json : {};
  const parts = runtimePartsFromMetadata(metadata.message_parts);
  const running = message.status === "pending"
    || message.status === "sending"
    || message.status === "streaming"
    || message.status === "tool_running";

  if (!running && message.content.trim()) {
    return [
      ...parts.filter(part => part.type !== "text"),
      { type: "text", text: message.content },
    ];
  }

  const hasText = parts.some(part => part.type === "text" && part.text.trim());
  if (message.content.trim() && !hasText) {
    return [...parts, { type: "text", text: message.content }];
  }
  return parts;
}

function messageAttachments(message: ChatMessage): ChatAttachment[] {
  const direct = Array.isArray(message.attachments) ? message.attachments : [];
  if (direct.length) return direct.filter(isChatAttachment);
  const metadata = isRecord(message.metadata_json) ? message.metadata_json : {};
  return Array.isArray(metadata.attachments) ? metadata.attachments.filter(isChatAttachment) : [];
}

function isChatAttachment(value: unknown): value is ChatAttachment {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.filename === "string"
    && typeof value.mime_type === "string";
}

function toRuntimeMessage(message: ChatMessage): ThreadMessage {
  const createdAt = new Date(message.created_at || Date.now());
  const custom = {
    attachments: messageAttachments(message),
    content: message.content,
    errorMessage: message.error_message,
    sourceStatus: message.status,
  };

  if (message.role === "user") {
    return {
      id: message.id,
      role: "user",
      content: message.content ? [{ type: "text", text: message.content }] : [],
      attachments: [],
      createdAt,
      metadata: { custom },
    } as ThreadMessage;
  }

  if (message.role === "system") {
    return {
      id: message.id,
      role: "system",
      content: [{ type: "text", text: message.content }],
      createdAt,
      metadata: { custom },
    } as ThreadMessage;
  }

  const running = message.status === "pending"
    || message.status === "sending"
    || message.status === "streaming"
    || message.status === "tool_running";

  return {
    id: message.id,
    role: "assistant",
    content: messageParts(message) as Extract<ThreadMessage, { role: "assistant" }>["content"],
    createdAt,
    status: message.status === "failed"
      ? { type: "incomplete", reason: "error", error: message.error_message || "Message failed" }
      : running
        ? { type: "running" }
        : { type: "complete", reason: "stop" },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom,
    },
  } as ThreadMessage;
}

function runtimeText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map(part => (isRecord(part) && part.type === "text" ? text(part.text) : "")).join("");
}

function contentHasVisibleText(content: unknown): boolean {
  if (typeof content === "string") return content.trim().length > 0;
  if (!Array.isArray(content)) return false;

  for (const part of content) {
    if (isRecord(part) && typeof part.text === "string" && part.text.trim().length > 0) {
      return true;
    }
  }
  return false;
}

function buildMessageGroups(signature: string): MessageGroup[] {
  if (!signature) return [];

  const messages = signature.split("\n").map(row => {
    const [index, id, role, weight] = row.split(":");
    return { id, index: Number(index), role, weight: Number(weight) || 1 };
  });

  const groups: MessageGroup[] = [];
  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];

    if (message.role !== "user") {
      groups.push({ id: message.id, index: message.index, kind: "standalone", weight: message.weight });
      continue;
    }

    const indices = [message.index];
    let weight = message.weight;
    while (i + 1 < messages.length && messages[i + 1].role !== "user") {
      weight += messages[++i].weight;
      indices.push(messages[i].index);
    }

    groups.push({ id: message.id, indices, kind: "turn", weight });
  }

  return groups;
}

function ThreadMessageList({
  components,
  editingMessageId,
  loadingIndicator,
  sessionKey,
}: {
  components: ThreadMessageComponents;
  editingMessageId: string | null;
  loadingIndicator?: ReactNode;
  sessionKey?: string | null;
}) {
  const messageSignature = useAuiState(s =>
    s.thread.messages
      .map((message, index) => `${index}:${message.id}:${message.role}:${message.content?.length ?? 1}`)
      .join("\n"),
  );
  const groups = buildMessageGroups(messageSignature);
  const { scrollRef, contentRef, isAtBottom, scrollToBottom, stopScroll } = useStickToBottom({
    initial: "instant",
    resize: "instant",
  });
  const [renderBudget, setRenderBudget] = useState(RENDER_BUDGET);
  const restoreFromBottomRef = useRef<number | null>(null);

  useEffect(() => {
    if (editingMessageId) stopScroll();
  }, [editingMessageId, stopScroll]);

  useAuiEvent("thread.runStart", () => {
    void scrollToBottom();
  });

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    stopScroll();
    el.scrollTop = el.scrollHeight;

    let frame = 0;
    let stableFrames = 0;
    let lastHeight = el.scrollHeight;
    let rafId = 0;

    const settle = () => {
      const node = scrollRef.current;
      if (!node) return;

      const height = node.scrollHeight;
      stableFrames = height === lastHeight ? stableFrames + 1 : 0;
      lastHeight = height;
      node.scrollTop = height;

      if (stableFrames >= 5 || frame >= 90) {
        void scrollToBottom("instant");
        return;
      }

      frame += 1;
      rafId = requestAnimationFrame(settle);
    };

    rafId = requestAnimationFrame(settle);
    return () => cancelAnimationFrame(rafId);
  }, [scrollRef, scrollToBottom, sessionKey, stopScroll]);

  let firstVisible = groups.length;
  for (let i = groups.length - 1, weight = 0; i >= 0; i--) {
    weight += groups[i].weight;
    firstVisible = i;
    if (weight >= renderBudget) break;
  }

  const hiddenCount = firstVisible;
  const visibleGroups = hiddenCount > 0 ? groups.slice(hiddenCount) : groups;
  const jumpButtonState = isAtBottom ? "out" : "in";

  const showEarlier = useCallback(() => {
    const el = scrollRef.current;
    restoreFromBottomRef.current = el ? el.scrollHeight - el.scrollTop : null;
    setRenderBudget(budget => budget + RENDER_BUDGET);
  }, [scrollRef]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && restoreFromBottomRef.current != null) {
      el.scrollTop = el.scrollHeight - restoreFromBottomRef.current;
      restoreFromBottomRef.current = null;
    }
  }, [renderBudget, scrollRef]);

  return (
    <div
      className="relative min-h-0 max-w-full overflow-hidden contain-[layout_paint]"
      style={{ height: "var(--thread-viewport-height)" } as CSSProperties}
    >
      <div
        className="conversation-scroll size-full overflow-x-hidden overflow-y-auto overscroll-contain scrollbar-transient"
        data-editing={editingMessageId ? "true" : undefined}
        data-following={isAtBottom ? "true" : "false"}
        data-slot="aui_thread-viewport"
        ref={scrollRef as React.RefCallback<HTMLDivElement>}
      >
        <div
          className="conversation-thread-content"
          data-slot="aui_thread-content"
          ref={contentRef as React.RefCallback<HTMLDivElement>}
        >
          {hiddenCount > 0 && (
            <button
              className="mx-auto mb-[var(--conversation-turn-gap)] rounded-full border border-border/65 bg-[var(--composer-fill)] px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
              onClick={showEarlier}
              type="button"
            >
              Show earlier
            </button>
          )}
          {visibleGroups.map(group => (
            <div
              className="conversation-turn-group"
              data-slot={group.kind === "turn" ? "aui_turn-group" : "aui_standalone-group"}
              key={group.id}
            >
              <MessageRenderBoundary resetKey={messageSignature}>
                {group.kind === "turn" ? (
                  <div className="conversation-turn-pair" data-slot="aui_turn-pair">
                    {group.indices.map(index => (
                      <ThreadPrimitive.MessageByIndex components={components} index={index} key={index} />
                    ))}
                  </div>
                ) : (
                  <ThreadPrimitive.MessageByIndex components={components} index={group.index} />
                )}
              </MessageRenderBoundary>
            </div>
          ))}
          {loadingIndicator}
          <div data-slot="aui_composer-clearance" />
        </div>
      </div>
      {groups.length > 0 && (
        <button
          aria-hidden={isAtBottom}
          aria-label="Scroll to bottom"
          className="thread-jump-button"
          data-state={jumpButtonState}
          onClick={() => void scrollToBottom()}
          tabIndex={isAtBottom ? -1 : 0}
          type="button"
        >
          <Codicon name="arrow-down" size="1rem" />
        </button>
      )}
    </div>
  );
}

function UserMessageBody({ onEdit, text: value }: { onEdit?: () => void; text: string }) {
  const clampInnerRef = useRef<HTMLDivElement | null>(null);
  const lastClampHeightRef = useRef(-1);
  const lineHeightRef = useRef(0);
  const [bodyClamped, setBodyClamped] = useState(false);

  const measureClamp = useCallback((entries: readonly ResizeObserverEntry[]) => {
    const inner = clampInnerRef.current;
    const outer = inner?.parentElement;
    if (!inner || !outer) return;

    const resizeEntry = entries.find(entry => entry.target === inner);
    const borderBoxSize = resizeEntry?.borderBoxSize as readonly ResizeObserverSize[] | undefined;
    const entryHeight = borderBoxSize?.[0]?.blockSize;
    const fullHeight = Math.ceil(entryHeight ?? inner.scrollHeight);

    if (fullHeight === lastClampHeightRef.current) return;
    lastClampHeightRef.current = fullHeight;

    if (!lineHeightRef.current) {
      const styles = getComputedStyle(inner);
      lineHeightRef.current = parseFloat(styles.lineHeight) || 1.5 * parseFloat(styles.fontSize) || 20;
    }

    outer.style.setProperty("--human-msg-full", `${fullHeight}px`);
    setBodyClamped(fullHeight > lineHeightRef.current * 2 + 1);
  }, []);

  useLayoutEffect(() => {
    const inner = clampInnerRef.current;
    if (!inner) return;

    const observer = new ResizeObserver(entries => measureClamp(entries));
    observer.observe(inner);
    measureClamp([]);

    return () => observer.disconnect();
  }, [measureClamp, value]);

  const content = (
    <div className="sticky-human-clamp" data-clamped={bodyClamped ? "true" : undefined}>
      <div className="min-h-[1.25rem]" ref={clampInnerRef}>
        <UserMessageText className="wrap-anywhere" text={value} />
      </div>
    </div>
  );

  if (!onEdit) return <div className="conversation-user-bubble">{content}</div>;

  return (
    <button aria-label="Edit message" className="conversation-user-bubble" onClick={onEdit} title="Edit message" type="button">
      {content}
    </button>
  );
}

function UserMessageAttachments({
  attachments,
  onOpenAttachment,
}: {
  attachments: ChatAttachment[];
  onOpenAttachment?: (attachment: ChatAttachment) => void;
}) {
  if (attachments.length === 0) return null;

  return (
    <div className="conversation-attachments" data-slot="aui_user-message-attachments">
      {attachments.map(attachment => (
        <FileAttachmentTile
          attachment={attachment}
          key={attachment.id}
          onOpen={onOpenAttachment}
          variant="message"
        />
      ))}
    </div>
  );
}

function UserMessage({
  editingMessageId,
  isEditSaving,
  onEdit,
  onEditCancel,
  onEditSave,
  onOpenAttachment,
}: Pick<AssistantMessagesProps, "editingMessageId" | "isEditSaving" | "onEdit" | "onEditCancel" | "onEditSave" | "onOpenAttachment">) {
  const messageId = useAuiState(s => s.message.id);
  const content = useAuiState(s => runtimeText(s.message.content));
  const rawAttachments = useAuiState(s => s.message.metadata.custom.attachments);
  const attachments = useMemo(
    () => (Array.isArray(rawAttachments) ? rawAttachments.filter(isChatAttachment) : []),
    [rawAttachments],
  );

  if (editingMessageId === messageId) {
    return (
      <div className="w-full flex justify-center" data-message-id={messageId}>
        <EditMessage
          initialContent={content}
          isSaving={isEditSaving}
          onCancel={onEditCancel}
          onSave={newContent => onEditSave(messageId, newContent)}
        />
      </div>
    );
  }

  return (
    <>
      <MessagePrimitive.Root
        className="conversation-turn conversation-user-turn group/user-message sticky z-40 -mx-4 flex w-[calc(100%+2rem)] min-w-0 max-w-none flex-col items-stretch gap-0 self-end overflow-visible bg-[var(--ui-chat-surface-background)] px-4 pb-[var(--conversation-turn-gap)] pt-1"
        data-role="user"
        data-slot="aui_user-message-root"
      >
        <div className="conversation-user-content">
          {content.trim() && <UserMessageBody onEdit={() => onEdit(messageId)} text={content} />}
        </div>
      </MessagePrimitive.Root>
      <UserMessageAttachments attachments={attachments} onOpenAttachment={onOpenAttachment} />
    </>
  );
}

function MarkdownTextPart({ status, text: value }: { status: { type: string }; text: string }) {
  return <MarkdownTextContent isRunning={status.type === "running"} text={value} />;
}

function ThinkingDisclosure({
  children,
  messageRunning,
  pending,
  timerKey,
}: {
  children: ReactNode;
  messageRunning?: boolean;
  pending?: boolean;
  timerKey?: string;
}) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const elapsed = useElapsedSeconds(Boolean(pending), timerKey);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const enterRef = useEnterAnimation(Boolean(messageRunning), timerKey);
  const open = userOpen ?? Boolean(pending);
  const isPreview = Boolean(pending) && userOpen === null;

  useEffect(() => {
    if (!isPreview) return;
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content) return;
    const pin = () => {
      el.scrollTop = el.scrollHeight;
    };
    pin();
    const observer = new ResizeObserver(pin);
    observer.observe(content);
    return () => observer.disconnect();
  }, [isPreview, open]);

  return (
    <div
      className="text-[length:var(--conversation-tool-font-size)] text-[var(--ui-text-tertiary)]"
      data-slot="aui_thinking-disclosure"
      ref={enterRef}
    >
      <DisclosureRow onToggle={() => setUserOpen(!open)} open={open}>
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            className={cn(
              "text-[length:var(--conversation-tool-font-size)] font-medium leading-[var(--conversation-line-height)] text-[var(--ui-text-secondary)]",
              pending && "shimmer text-foreground/55",
            )}
          >
            Thinking
          </span>
          {pending && (
            <ActivityTimerText
              className="text-[length:var(--conversation-caption-font-size)] tabular-nums text-[var(--ui-text-tertiary)]"
              seconds={elapsed}
            />
          )}
        </span>
      </DisclosureRow>
      {open && (
        <div
          className={cn(
            "mt-0.5 w-full min-w-0 max-w-full overflow-hidden wrap-anywhere pb-1",
            isPreview && "thinking-preview max-h-40",
          )}
          ref={scrollRef}
        >
          <div ref={contentRef}>{children}</div>
        </div>
      )}
    </div>
  );
}

function ReasoningGroup({ children, endIndex, startIndex }: { children?: ReactNode; endIndex: number; startIndex: number }) {
  const messageId = useAuiState(s => s.message.id);
  const messageRunning = useAuiState(s => s.message.status?.type === "running");
  const pending = useAuiState(
    s =>
      s.thread.isRunning
      && s.message.status?.type === "running"
      && s.message.parts
        .slice(Math.max(0, startIndex), endIndex + 1)
        .some(part => part?.type === "reasoning" && part.status?.type !== "complete"),
  );
  const hasContent = useAuiState(s =>
    s.message.parts
      .slice(Math.max(0, startIndex), endIndex + 1)
      .some(part => part?.type === "reasoning" && typeof part.text === "string" && part.text.trim().length > 0),
  );

  if (!hasContent) return null;

  return (
    <ThinkingDisclosure
      messageRunning={messageRunning}
      pending={pending}
      timerKey={`reasoning:${messageId}`}
    >
      {children}
    </ThinkingDisclosure>
  );
}

function ReasoningTextPart({ status, text: value }: ReasoningMessagePartProps) {
  const displayText = value.trimStart();
  const messageRunning = useAuiState(s => s.message.status?.type === "running");
  const isRunning = status?.type === "running" || messageRunning;

  return (
    <MarkdownTextContent
      containerClassName="text-xs leading-snug text-muted-foreground/85"
      containerProps={{ "data-slot": "aui_reasoning-text" } as ComponentProps<"div">}
      isRunning={isRunning}
      text={displayText}
    />
  );
}

function safeJson(value: unknown, max = 20_000) {
  if (value === undefined || value === null || value === "") return "";
  const raw = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!raw) return "";
  return raw.length <= max ? raw : `${raw.slice(0, max)}\n\n... ${raw.length - max} more characters truncated.`;
}

function payloadRecord(value: unknown): Record<string, unknown> {
  if (isRecord(value)) return value;
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function compact(value: string, max = 140) {
  const clean = value.replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1).trimEnd()}...`;
}

function languageLabel(value: unknown) {
  const language = text(value).toLowerCase();
  if (language === "sh" || language === "bash" || language === "shell" || language === "terminal") return "Shell";
  if (language === "py" || language === "python") return "Python";
  return language ? language.charAt(0).toUpperCase() + language.slice(1) : "Python";
}

function toolTitle(toolName: string, args: unknown, running: boolean) {
  const argsRecord = payloadRecord(args);
  const purpose = text(argsRecord.purpose) || text(argsRecord.task) || text(argsRecord.query) || text(argsRecord.command);
  if (toolName === "workspace") {
    const label = running ? "Run" : "Ran";
    const language = languageLabel(argsRecord.language);
    return `${label} ${language}${purpose ? `: ${compact(purpose)}` : ""}`;
  }
  const clean = toolName.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  const base = clean ? clean.replace(/\b\w/g, char => char.toUpperCase()) : "Tool";
  return running ? base : base.startsWith("Run ") ? `Ran ${base.slice(4)}` : base;
}

function resultFailed(isError: boolean | undefined, result: unknown) {
  const record = payloadRecord(result);
  const status = text(record.status).toLowerCase();
  return Boolean(isError || record.error === true || record.success === false || record.ok === false || status === "failed" || status === "error");
}

function durationLabel(value: unknown) {
  const ms = typeof value === "number" && Number.isFinite(value) ? value : undefined;
  if (ms === undefined || ms < 0) return "";
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
  const wholeSeconds = Math.round(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  const remainder = wholeSeconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function countLabel(result: unknown) {
  const record = payloadRecord(result);
  const connectorCalls = record.connector_calls;
  if (isRecord(connectorCalls)) {
    const total = Object.values(connectorCalls).reduce<number>((sum, value) => sum + (typeof value === "number" ? value : 0), 0);
    if (total > 0) return `${total} connector call${total === 1 ? "" : "s"}`;
  }
  const toolCalls = typeof record.tool_calls === "number" ? record.tool_calls : 0;
  if (toolCalls > 0) return `${toolCalls} tool call${toolCalls === 1 ? "" : "s"}`;
  return "";
}

function DetailBlock({
  format = "markdown",
  label,
  tone,
  value,
}: {
  format?: "code" | "markdown";
  label: string;
  tone?: "error" | "muted";
  value: string;
}) {
  if (!value) return null;
  return (
    <div className="max-w-full text-[0.6875rem] leading-relaxed">
      <p className="mb-1 text-[0.65rem] font-medium uppercase tracking-[0.08em] text-[var(--ui-text-tertiary)]">{label}</p>
      {format === "code" ? (
        <pre
          className={cn(
            "max-h-56 overflow-auto whitespace-pre-wrap break-words bg-transparent px-2 py-1.5 font-mono text-[0.7rem] leading-[1.55]",
            tone === "error" ? "text-[var(--color-danger)]" : tone === "muted" ? "text-[var(--ui-text-tertiary)]" : "text-[var(--ui-text-secondary)]",
          )}
        >
          {value}
        </pre>
      ) : (
        <CompactMarkdown
          className={cn(
            "max-h-72 overflow-auto px-2 py-1.5",
            tone === "error" ? "text-[var(--color-danger)]" : tone === "muted" ? "text-[var(--ui-text-tertiary)]" : "text-[var(--ui-text-secondary)]",
          )}
          text={value}
        />
      )}
    </div>
  );
}

function ToolGlyph({ error, running, toolName }: { error: boolean; running: boolean; toolName: string }) {
  if (running) {
    return <Codicon className="h-3.5 w-3.5 shrink-0 text-[var(--ui-text-tertiary)]" name="loading" size="0.875rem" spinning />;
  }
  if (error) return <AlertCircle aria-label="Error" className="h-3.5 w-3.5 shrink-0 text-[var(--color-danger)]" />;
  return (
    <Codicon
      className="h-3.5 w-3.5 shrink-0 text-[var(--ui-text-tertiary)]"
      name={toolName.includes("search") ? "search" : toolName === "workspace" ? "terminal" : "tools"}
      size="0.875rem"
    />
  );
}

function ToolFallback({ args, argsText, isError, result, status, toolCallId, toolName }: ToolCallMessagePartProps) {
  const messageId = useAuiState(s => s.message.id);
  const messageRunning = useAuiState(s => s.message.status?.type === "running");
  const running = status?.type === "running" && result === undefined;
  const error = resultFailed(isError, result);
  const elapsed = useElapsedSeconds(running, `tool:${messageId}:${toolCallId}`);
  const animationKey = toolCallId || `${toolName}:${stableDisclosureHash(safeJson(args))}`;
  const disclosureId = `tool-entry:${messageId}:${toolCallId || `${toolName}:${stableDisclosureHash(safeJson(args))}`}`;
  const [open, setOpen] = useDisclosureOpen(disclosureId, false);
  const enterRef = useEnterAnimation(messageRunning, `tool:${messageId}:${animationKey}`);
  const resultRecord = payloadRecord(result);
  const codeText = text(payloadRecord(args).code) || text(payloadRecord(args).script) || text(payloadRecord(args).command);
  const stdout = text(resultRecord.stdout) || text(resultRecord.output);
  const stderr = text(resultRecord.stderr);
  const message = text(resultRecord.message) || text(resultRecord.error) || text(resultRecord.detail);
  const rawResult = result !== undefined ? safeJson(result) : "";
  const rawArgs = argsText || safeJson(args);
  const hasDetail = Boolean(codeText || rawArgs || stdout || stderr || message || rawResult);
  const copyPayload = [codeText, stdout, stderr, message, rawResult].filter(Boolean).join("\n\n");
  const title = toolTitle(toolName, args, running);

  return (
    <div
      className={cn(
        "group/tool-block min-w-0 max-w-full overflow-hidden text-[length:var(--conversation-tool-font-size)] text-[var(--ui-text-tertiary)]",
        open && hasDetail && "rounded-[0.3125rem] border border-[var(--ui-stroke-tertiary)]",
      )}
      data-slot="tool-block"
      data-tool-open={open ? "" : undefined}
      data-tool-row=""
      ref={enterRef}
    >
      <div className={open && hasDetail ? "border-b border-[var(--ui-stroke-tertiary)] px-2 py-1.5" : ""}>
        <DisclosureRow
          action={
            open && copyPayload ? (
              <CopyButton className="h-5 gap-0 rounded-md px-1" iconClassName="size-3" showLabel={false} text={copyPayload} />
            ) : undefined
          }
          onToggle={hasDetail ? () => setOpen(value => !value) : undefined}
          open={open}
          trailing={running ? <ActivityTimerText className="shrink-0 text-[0.625rem] tabular-nums text-[var(--ui-text-tertiary)]" seconds={elapsed} /> : undefined}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <ToolGlyph error={error} running={running} toolName={toolName} />
            <span
              className={cn(
                "text-[length:var(--conversation-tool-font-size)] font-medium leading-[var(--conversation-line-height)] text-[var(--ui-text-secondary)]",
                error ? "text-[var(--color-danger)]" : running && "shimmer text-foreground/55",
              )}
            >
              {title}
            </span>
            {!running && countLabel(result) && <span className="shrink-0 text-[0.625rem] tabular-nums text-[var(--ui-text-tertiary)]">{countLabel(result)}</span>}
            {!running && durationLabel(resultRecord.duration_ms ?? resultRecord.durationMs ?? resultRecord.duration_s) && (
              <span className="shrink-0 text-[0.625rem] tabular-nums text-[var(--ui-text-tertiary)]">
                {durationLabel(resultRecord.duration_ms ?? resultRecord.durationMs ?? resultRecord.duration_s)}
              </span>
            )}
          </span>
        </DisclosureRow>
      </div>
      {open && hasDetail && (
        <div className="relative grid w-full min-w-0 max-w-full gap-1.5 overflow-hidden p-1.5">
          <DetailBlock format="code" label={toolName === "workspace" ? languageLabel(payloadRecord(args).language).toLowerCase() : "input"} value={codeText} />
          {!codeText && <DetailBlock format="code" label="input" value={rawArgs} />}
          <DetailBlock label="error" tone="error" value={error ? message : ""} />
          <DetailBlock label="stdout" value={stdout} />
          <DetailBlock format="code" label="stderr" tone="muted" value={stderr} />
          {!stdout && !stderr && !message && <DetailBlock label="result" value={rawResult} />}
        </div>
      )}
    </div>
  );
}

const TOOL_GROUP_SCROLL_THRESHOLD = 3;

function useToolWindow(enabled: boolean) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const [faded, setFaded] = useState(false);

  const syncFade = useCallback(() => setFaded((scrollRef.current?.scrollTop ?? 0) > 4), []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= 8;
    syncFade();
  }, [syncFade]);

  useEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!enabled || !el || !content) return;

    const pin = () => {
      if (stickRef.current) el.scrollTop = el.scrollHeight;
      syncFade();
    };

    pin();
    const observer = new ResizeObserver(pin);
    observer.observe(content);
    return () => observer.disconnect();
  }, [enabled, syncFade]);

  return { contentRef, faded, onScroll, scrollRef };
}

function ToolGroup({ children, startIndex }: { children?: ReactNode; endIndex: number; startIndex: number }) {
  const messageId = useAuiState(s => s.message.id);
  const messageRunning = useAuiState(s => s.message.status?.type === "running");
  const enterRef = useEnterAnimation(messageRunning, `tool-group:${messageId}:${startIndex}`);
  const bounded = Children.count(children) >= TOOL_GROUP_SCROLL_THRESHOLD;
  const { contentRef, faded, onScroll, scrollRef } = useToolWindow(bounded);

  return (
    <div className="min-w-0 max-w-full overflow-hidden" data-slot="tool-block" data-tool-group="" ref={enterRef}>
      <div
        className={cn(
          bounded && "tool-group-scroll max-h-[var(--tool-group-scroll-max-h)] overflow-y-auto",
          bounded && faded && "tool-group-scroll--faded",
        )}
        onScroll={bounded ? onScroll : undefined}
        ref={scrollRef}
      >
        <div className="grid min-w-0 max-w-full gap-[var(--tool-row-gap)]" ref={contentRef}>
          {children}
        </div>
      </div>
    </div>
  );
}

const MESSAGE_PARTS_COMPONENTS = {
  Reasoning: ReasoningTextPart,
  ReasoningGroup,
  Text: MarkdownTextPart,
  ToolGroup,
  tools: { Fallback: ToolFallback },
} as const;

const STREAM_STALL_S = 2;

function StreamStallIndicator() {
  const activity = useAuiState(s => {
    let textLength = 0;
    for (const part of s.message.content) {
      const record = part as Record<string, unknown>;
      if (typeof record.text === "string") textLength += record.text.length;
    }
    return `${s.message.content.length}:${textLength}`;
  });
  const [stalledActivity, setStalledActivity] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setStalledActivity(activity), STREAM_STALL_S * 1000);
    return () => window.clearTimeout(id);
  }, [activity]);

  const stalled = stalledActivity === activity;
  const elapsed = useElapsedSeconds(stalled);
  if (!stalled) return null;

  return (
    <div aria-label="AI is thinking" aria-live="polite" className="mt-1.5 flex max-w-full items-center gap-2 self-start text-sm text-muted-foreground/70" role="status">
      <span aria-hidden="true" className="dither inline-block size-3 rounded-[2px] text-midground/80 animate-pulse" />
      <ActivityTimerText seconds={elapsed} />
    </div>
  );
}

function AssistantMessage({
  onCopyMessage,
  onOpenAttachment,
  onRetryMessage,
}: Pick<AssistantMessagesProps, "onCopyMessage" | "onOpenAttachment" | "onRetryMessage">) {
  const messageId = useAuiState(s => s.message.id);
  const status = useAuiState(s => s.message.status?.type);
  const hasVisibleText = useAuiState(s => contentHasVisibleText(s.message.content));
  const errorMessage = useAuiState(s => text(s.message.metadata.custom.errorMessage));
  const rawAttachments = useAuiState(s => s.message.metadata.custom.attachments);
  const attachments = useMemo(
    () => (Array.isArray(rawAttachments) ? rawAttachments.filter(isChatAttachment) : []),
    [rawAttachments],
  );
  const messageRuntime = useMessageRuntime();
  const isRunning = status === "running";
  const enterRef = useEnterAnimation(isRunning, `assistant-message:${messageId}`);
  const liveContent = useCallback(() => runtimeText(messageRuntime.getState().content), [messageRuntime]);

  if (status === "incomplete") {
    return (
      <MessagePrimitive.Root
        className="group flex w-full min-w-0 max-w-full shrink-0 flex-col gap-0 self-start overflow-hidden"
        data-role="assistant"
        data-slot="aui_assistant-message-root"
      >
        <div className="wrap-anywhere min-w-0 max-w-full overflow-hidden text-pretty text-[length:var(--conversation-text-font-size)] leading-[var(--dt-line-height)] text-foreground" data-slot="aui_assistant-message-content">
          <FailedMessage errorMessage={errorMessage} onRetry={() => onRetryMessage(messageId)} />
        </div>
      </MessagePrimitive.Root>
    );
  }

  return (
    <MessagePrimitive.Root
      className="group flex w-full min-w-0 max-w-full shrink-0 flex-col gap-0 self-start overflow-hidden"
      data-role="assistant"
      data-slot="aui_assistant-message-root"
      data-streaming={isRunning ? "true" : undefined}
      ref={enterRef}
    >
      <div className="wrap-anywhere min-w-0 max-w-full overflow-hidden text-pretty text-[length:var(--conversation-text-font-size)] leading-[var(--dt-line-height)] text-foreground" data-slot="aui_assistant-message-content">
        <MessagePrimitive.Parts components={MESSAGE_PARTS_COMPONENTS} />
        {isRunning && <StreamStallIndicator />}
        {attachments.length > 0 && (
          <div className="conversation-output-attachments">
            {attachments.map(attachment => (
              <FileAttachmentTile
                attachment={attachment}
                key={attachment.id}
                onOpen={onOpenAttachment}
                variant="message"
              />
            ))}
          </div>
        )}
      </div>
      {hasVisibleText && (
        <div className="flex min-h-6 flex-col items-end gap-1 pl-[var(--message-text-indent)] pr-[var(--message-text-indent)]">
          <MessageActions content={liveContent} onCopy={() => onCopyMessage?.(liveContent())} />
        </div>
      )}
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return null;
}

export const AssistantMessages = memo(function AssistantMessages({
  editingMessageId,
  isEditSaving,
  isRunning,
  loadingIndicator,
  messages,
  onCopyMessage,
  onEdit,
  onEditCancel,
  onEditSave,
  onOpenAttachment,
  onRetryMessage,
  onStop,
  sessionKey,
}: AssistantMessagesProps) {
  const runtimeMessageRepository = useMemo(() => {
    const items: Array<{ message: ThreadMessage; parentId: string | null }> = [];
    let parentId: string | null = null;
    let headId: string | null = null;

    for (const message of messages) {
      items.push({ message: toRuntimeMessage(message), parentId });
      parentId = message.id;
      headId = message.id;
    }

    return ExportedMessageRepository.fromBranchableArray(items, { headId });
  }, [messages]);
  const onCancel = useCallback(async () => onStop(), [onStop]);
  const onNew = useCallback(async () => {}, []);
  const setMessages = useCallback(() => {}, []);
  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    isRunning,
    messageRepository: runtimeMessageRepository,
    onCancel,
    onNew,
    setMessages,
  });
  const components = useMemo(
    () => ({
      AssistantMessage: () => (
        <AssistantMessage
          onCopyMessage={onCopyMessage}
          onOpenAttachment={onOpenAttachment}
          onRetryMessage={onRetryMessage}
        />
      ),
      SystemMessage,
      UserMessage: () => (
        <UserMessage
          editingMessageId={editingMessageId}
          isEditSaving={isEditSaving}
          onEdit={onEdit}
          onEditCancel={onEditCancel}
          onEditSave={onEditSave}
          onOpenAttachment={onOpenAttachment}
        />
      ),
    }),
    [editingMessageId, isEditSaving, onCopyMessage, onEdit, onEditCancel, onEditSave, onOpenAttachment, onRetryMessage],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadMessageList
        key={sessionKey ?? "thread"}
        components={components}
        editingMessageId={editingMessageId}
        loadingIndicator={loadingIndicator}
        sessionKey={sessionKey}
      />
    </AssistantRuntimeProvider>
  );
});
