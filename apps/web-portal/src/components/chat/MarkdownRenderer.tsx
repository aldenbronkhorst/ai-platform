import { TextMessagePartProvider, useMessagePartText } from "@assistant-ui/react";
import {
  parseMarkdownIntoBlocks,
  StreamdownTextPrimitive,
  type SyntaxHighlighterProps,
  type StreamdownTextComponents,
} from "@assistant-ui/react-streamdown";
import { code } from "@streamdown/code";
import { createMathPlugin } from "@streamdown/math";
import {
  cloneElement,
  isValidElement,
  memo,
  type ComponentProps,
  type ReactNode,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { preprocessMarkdown } from "../../lib/markdown-preprocess";
import { tailBoundedRemend } from "../../lib/remend-tail";
import { cn } from "../../lib/utils";
import { ExpandableBlock } from "./ExpandableBlock";
import { SyntaxHighlighter } from "./ShikiHighlighter";

const mathPlugin = createMathPlugin({ singleDollarTextMath: true });

function preprocessWithTailRepair(text: string): string {
  try {
    return tailBoundedRemend(preprocessMarkdown(text));
  } catch {
    return text;
  }
}

const BLOCK_CACHE_MAX = 64;
const BLOCK_CACHE_MIN_LENGTH = 1024;
const blockCache = new Map<string, string[]>();

function parseMarkdownIntoBlocksCached(markdown: string): string[] {
  if (markdown.length < BLOCK_CACHE_MIN_LENGTH) {
    return parseMarkdownIntoBlocks(markdown);
  }

  const hit = blockCache.get(markdown);
  if (hit) {
    blockCache.delete(markdown);
    blockCache.set(markdown, hit);
    return hit;
  }

  const blocks = parseMarkdownIntoBlocks(markdown);
  blockCache.set(markdown, blocks);
  if (blockCache.size > BLOCK_CACHE_MAX) {
    blockCache.delete(blockCache.keys().next().value as string);
  }
  return blocks;
}

function safeHref(href?: string) {
  if (!href) return undefined;
  if (/^(https?:|mailto:)/i.test(href)) return href;
  return undefined;
}

function childrenToText(children: unknown): string {
  if (typeof children === "string" || typeof children === "number") {
    return String(children).trim();
  }

  if (Array.isArray(children) && children.every((child) => typeof child === "string" || typeof child === "number")) {
    return children.join("").trim();
  }

  return "";
}

function MarkdownLink({ children, className, href, ...props }: ComponentProps<"a">) {
  const target = safeHref(href);
  const text = childrenToText(children);
  const fallbackLabel = text && target && text !== target ? text : undefined;

  return (
    <a
      className={cn("aui-pretty-link wrap-anywhere", className)}
      href={target}
      rel="noopener noreferrer"
      target="_blank"
      title={fallbackLabel}
      {...props}
    >
      {children}
    </a>
  );
}

function MarkdownImage({ className, alt, src, ...props }: ComponentProps<"img">) {
  if (!src || !/^(https?:|data:)/i.test(src)) {
    return null;
  }

  return (
    <span className="my-2 block w-fit max-w-full">
      <img
        alt={alt ?? ""}
        className={cn(
          "m-0 block h-auto w-auto max-w-[min(100%,var(--image-preview-max-width))] rounded-lg object-contain",
          className,
        )}
        data-slot="aui_markdown-image"
        src={src}
        {...props}
      />
    </span>
  );
}

type AlertType = "caution" | "important" | "note" | "tip" | "warning";

const ALERT_STYLES: Record<AlertType, { label: string; tone: string }> = {
  caution: { label: "Caution", tone: "text-red-600 dark:text-red-400" },
  important: { label: "Important", tone: "text-[var(--ui-text-secondary)]" },
  note: { label: "Note", tone: "text-[var(--ui-text-secondary)]" },
  tip: { label: "Tip", tone: "text-[var(--ui-text-secondary)]" },
  warning: { label: "Warning", tone: "text-amber-600 dark:text-amber-400" },
};

const MARKER_RE = /^\s*\[!(note|tip|important|warning|caution)\]\s*\n?/i;

function firstText(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);

  if (Array.isArray(node)) {
    for (const child of node) {
      const text = firstText(child);
      if (text.trim()) return text;
    }
    return "";
  }

  if (isValidElement(node)) {
    return firstText((node.props as { children?: ReactNode }).children);
  }

  return "";
}

function stripMarker(node: ReactNode, state: { done: boolean }): ReactNode {
  if (state.done) return node;

  if (typeof node === "string") {
    const replaced = node.replace(MARKER_RE, "");
    if (replaced !== node) {
      state.done = true;
      return replaced;
    }
    return node;
  }

  if (Array.isArray(node)) {
    return node.map((child, index) => <Fragmentless key={index} node={stripMarker(child, state)} />);
  }

  if (isValidElement(node)) {
    const children = (node.props as { children?: ReactNode }).children;
    if (children == null) return node;
    return cloneElement(node, undefined, stripMarker(children, state));
  }

  return node;
}

function Fragmentless({ node }: { node: ReactNode }) {
  return <>{node}</>;
}

function extractAlert(children: ReactNode): { body: ReactNode; type: AlertType } | null {
  const match = firstText(children).match(MARKER_RE);
  if (!match) return null;
  return { body: stripMarker(children, { done: false }), type: match[1].toLowerCase() as AlertType };
}

function MarkdownAlert({ children, type }: { children: ReactNode; type: AlertType }) {
  const style = ALERT_STYLES[type];

  return (
    <div
      className="my-2 rounded-lg border border-border bg-muted px-3 py-2 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0"
      data-slot="aui_markdown-alert"
    >
      <div className={cn("mb-1 flex items-center gap-1.5 text-[0.8125rem] font-semibold", style.tone)}>
        {style.label}
      </div>
      {children}
    </div>
  );
}

const REVEAL_DRAIN_MS = 500;
const REVEAL_MAX_CHARS_PER_FRAME = 30;
const REVEAL_MIN_COMMIT_MS = 33;

function commonPrefixLength(left: string, right: string) {
  const max = Math.min(left.length, right.length);
  let index = 0;
  while (index < max && left[index] === right[index]) index += 1;
  return index;
}

function useSmoothReveal(text: string, isRunning: boolean): string {
  const [displayed, setDisplayed] = useState(isRunning ? "" : text);
  const targetRef = useRef(text);
  const shownRef = useRef(displayed);
  const frameRef = useRef<number | null>(null);
  const lastTickRef = useRef(0);

  // Hermes keeps these refs synchronized with the current render so the
  // streaming child always drains from the latest text, not a stale effect.
  // eslint-disable-next-line react-hooks/refs
  shownRef.current = displayed;
  // eslint-disable-next-line react-hooks/refs
  targetRef.current = text;

  useEffect(() => {
    if (typeof window === "undefined") return;

    if (!targetRef.current.startsWith(shownRef.current)) {
      const prefixLength = commonPrefixLength(shownRef.current, targetRef.current);
      shownRef.current = isRunning ? targetRef.current.slice(0, prefixLength) : targetRef.current;
      setDisplayed(shownRef.current);
    }

    if (shownRef.current.length >= targetRef.current.length || frameRef.current !== null) return;

    lastTickRef.current = performance.now();

    const tick = () => {
      const now = performance.now();
      const dt = now - lastTickRef.current;

      if (dt < REVEAL_MIN_COMMIT_MS) {
        frameRef.current = requestAnimationFrame(tick);
        return;
      }

      lastTickRef.current = now;
      const remaining = targetRef.current.length - shownRef.current.length;
      const add = Math.min(
        remaining,
        Math.ceil((REVEAL_MAX_CHARS_PER_FRAME * dt) / 16.7),
        Math.max(1, Math.ceil((remaining * dt) / REVEAL_DRAIN_MS)),
      );

      shownRef.current = targetRef.current.slice(0, shownRef.current.length + add);
      setDisplayed(shownRef.current);
      frameRef.current = shownRef.current.length < targetRef.current.length ? requestAnimationFrame(tick) : null;
    };

    frameRef.current = requestAnimationFrame(tick);
  }, [text, isRunning]);

  useEffect(
    () => () => {
      if (frameRef.current !== null && typeof window !== "undefined") {
        cancelAnimationFrame(frameRef.current);
      }
    },
    [],
  );

  return displayed;
}

function SmoothStreamingText({ children }: { children: ReactNode }) {
  const { text, status } = useMessagePartText();
  const isRunning = status.type === "running";
  const revealed = useSmoothReveal(text, isRunning);

  return (
    <TextMessagePartProvider isRunning={isRunning || revealed !== text} text={revealed}>
      {children}
    </TextMessagePartProvider>
  );
}

function DeferStreamingText({ children }: { children: ReactNode }) {
  const { text, status } = useMessagePartText();
  const deferredText = useDeferredValue(text);
  const isRunning = status.type === "running";

  return (
    <TextMessagePartProvider isRunning={isRunning} text={deferredText}>
      {children}
    </TextMessagePartProvider>
  );
}

const HEADING_SIZES: Record<"h1" | "h2" | "h3" | "h4", string> = {
  h1: "text-[1rem] tracking-tight",
  h2: "text-[0.9375rem] tracking-tight",
  h3: "text-[0.875rem]",
  h4: "text-[0.8125rem]",
};

const MARKDOWN_CONTAINER_CLASS_NAME = cn(
  "aui-md prose w-full max-w-none overflow-hidden text-[length:var(--conversation-text-font-size)] leading-[var(--dt-line-height)] text-foreground",
  "prose-p:leading-[var(--dt-line-height)] prose-li:leading-[var(--dt-line-height)]",
  "prose-headings:text-foreground prose-strong:text-foreground",
  "prose-a:break-words prose-p:[overflow-wrap:anywhere]",
  "prose-li:marker:text-muted-foreground/70",
  "prose-code:rounded-[0.25rem] prose-code:px-[0.1875rem] prose-code:py-px prose-code:font-mono prose-code:text-[0.9em] prose-code:font-normal prose-code:before:content-none prose-code:after:content-none",
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&>*+*]:mt-[var(--paragraph-gap)]",
);

const MAX_MARKDOWN_CHARS = 200_000;

function chunkByLines(text: string, perChunk: number): Array<{ lines: number; text: string }> {
  const lines = text.split("\n");
  const chunks: Array<{ lines: number; text: string }> = [];

  for (let index = 0; index < lines.length; index += perChunk) {
    const slice = lines.slice(index, index + perChunk);
    chunks.push({ lines: slice.length, text: slice.join("\n") });
  }

  return chunks;
}

function HugeTextFallback({ containerClassName, text }: { containerClassName?: string; text: string }) {
  const chunks = useMemo(() => chunkByLines(text, 200), [text]);

  return (
    <div
      className={cn(
        "aui-md w-full max-w-none overflow-hidden rounded-[0.625rem] border border-border font-mono text-[0.7rem] leading-relaxed text-foreground",
        containerClassName,
      )}
    >
      <ExpandableBlock className="p-2">
        {chunks.map((chunk, index) => (
          <div className="[content-visibility:auto]" key={index} style={{ containIntrinsicSize: `auto ${chunk.lines * 16}px` }}>
            {chunk.text}
          </div>
        ))}
      </ExpandableBlock>
    </div>
  );
}

interface MarkdownTextSurfaceProps {
  containerClassName?: string;
  containerProps?: ComponentProps<"div">;
}

function MarkdownTextSurface({ containerClassName, containerProps }: MarkdownTextSurfaceProps) {
  const { status, text } = useMessagePartText();
  const isStreaming = status.type === "running";
  const plugins = useMemo(() => ({ code, math: mathPlugin }), []);

  const components = useMemo(
    () =>
      ({
        h1: ({ className, ...props }: ComponentProps<"h1">) => (
          <h1 className={cn("my-1 font-semibold", HEADING_SIZES.h1, className)} {...props} />
        ),
        h2: ({ className, ...props }: ComponentProps<"h2">) => (
          <h2 className={cn("my-1 font-semibold", HEADING_SIZES.h2, className)} {...props} />
        ),
        h3: ({ className, ...props }: ComponentProps<"h3">) => (
          <h3 className={cn("my-1 font-semibold", HEADING_SIZES.h3, className)} {...props} />
        ),
        h4: ({ className, ...props }: ComponentProps<"h4">) => (
          <h4 className={cn("my-1 font-semibold", HEADING_SIZES.h4, className)} {...props} />
        ),
        p: ({ className, ...props }: ComponentProps<"p">) => (
          <p className={cn("wrap-anywhere leading-[var(--dt-line-height)]", className)} {...props} />
        ),
        a: MarkdownLink,
        inlineCode: ({ className, ...props }: ComponentProps<"code">) => <code className={className} dir="ltr" {...props} />,
        hr: () => <div aria-hidden className="my-3" />,
        blockquote: ({ children, className, ...props }: ComponentProps<"blockquote">) => {
          const alert = extractAlert(children);

          if (alert) {
            return <MarkdownAlert type={alert.type}>{alert.body}</MarkdownAlert>;
          }

          return (
            <blockquote
              className={cn("border-s-2 border-border ps-3 text-muted-foreground italic", className)}
              dir="auto"
              {...props}
            >
              {children}
            </blockquote>
          );
        },
        ul: ({ className, ...props }: ComponentProps<"ul">) => <ul className={cn("my-1 gap-0", className)} dir="auto" {...props} />,
        ol: ({ className, ...props }: ComponentProps<"ol">) => <ol className={cn("my-1 gap-0", className)} dir="auto" {...props} />,
        li: ({ className, ...props }: ComponentProps<"li">) => <li className={cn("leading-[var(--dt-line-height)]", className)} {...props} />,
        table: ({ className, ...props }: ComponentProps<"table">) => (
          <div className="aui-md-table my-2 max-w-full overflow-x-auto rounded-[0.375rem] border border-border">
            <table
              className={cn(
                "m-0 w-full min-w-[18rem] border-collapse text-[0.8125rem] [&_tr]:border-b [&_tr]:border-border last:[&_tr]:border-0",
                className,
              )}
              {...props}
            />
          </div>
        ),
        thead: ({ className, ...props }: ComponentProps<"thead">) => (
          <thead className={cn("m-0 bg-muted/35 text-muted-foreground", className)} {...props} />
        ),
        th: ({ className, ...props }: ComponentProps<"th">) => (
          <th
            className={cn(
              "whitespace-nowrap px-2.5 py-1.5 text-left align-middle text-[0.75rem] font-medium text-muted-foreground",
              className,
            )}
            {...props}
          />
        ),
        td: ({ className, ...props }: ComponentProps<"td">) => (
          <td className={cn("px-2.5 py-1.5 align-top text-[0.8125rem] leading-snug", className)} {...props} />
        ),
        img: MarkdownImage,
        SyntaxHighlighter: (props: SyntaxHighlighterProps) => <SyntaxHighlighter {...props} defer={isStreaming} />,
      }) as unknown as StreamdownTextComponents,
    [isStreaming],
  );

  if (text.length > MAX_MARKDOWN_CHARS) {
    return <HugeTextFallback containerClassName={containerClassName} text={text} />;
  }

  return (
    <StreamdownTextPrimitive
      components={components}
      containerClassName={cn(MARKDOWN_CONTAINER_CLASS_NAME, containerClassName)}
      containerProps={containerProps}
      lineNumbers={false}
      mode="streaming"
      parseIncompleteMarkdown={false}
      parseMarkdownIntoBlocksFn={parseMarkdownIntoBlocksCached}
      plugins={plugins}
      preprocess={preprocessWithTailRepair}
    />
  );
}

interface MarkdownTextContentProps extends MarkdownTextSurfaceProps {
  isRunning: boolean;
  text: string;
}

export function MarkdownTextContent({ isRunning, text, ...surfaceProps }: MarkdownTextContentProps) {
  return (
    <TextMessagePartProvider isRunning={isRunning} text={text}>
      <SmoothStreamingText>
        <DeferStreamingText>
          <MarkdownTextSurface {...surfaceProps} />
        </DeferStreamingText>
      </SmoothStreamingText>
    </TextMessagePartProvider>
  );
}

function MarkdownRendererImpl({
  content,
  isRunning = false,
}: {
  content: string;
  isRunning?: boolean;
}) {
  return <MarkdownTextContent isRunning={isRunning} text={content} />;
}

export const MarkdownRenderer = memo(MarkdownRendererImpl);
