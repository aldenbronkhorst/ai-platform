import { Fragment, useMemo } from "react";
import type { FC } from "react";
import { cn } from "../../lib/utils";

interface FenceSegment {
  kind: "fence";
  code: string;
  lang: string | null;
}

interface InlineSegment {
  kind: "inline";
  text: string;
}

interface InlineCodeSegment {
  kind: "inline-code";
  code: string;
}

interface InlineTextSegment {
  kind: "inline-text";
  text: string;
}

type TopSegment = FenceSegment | InlineSegment;
type InlineNode = InlineCodeSegment | InlineTextSegment;

const FENCE_RE = /```([^\n`]*)\n([\s\S]*?)```/g;
const INLINE_CODE_RE = /(`+)([^`\n][\s\S]*?)\1/g;
const DIRECTIVE_RE = /@(file|folder|url|image|tool|line|terminal|session):(`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|\S+)/g;
const TRAILING_PUNCTUATION_RE = /[,.;!?]+$/;

function unwrapDirectiveValue(raw: string): string {
  if (raw.length < 2) return raw.replace(TRAILING_PUNCTUATION_RE, "");
  const head = raw[0];
  const tail = raw[raw.length - 1];
  if ((head === "`" && tail === "`") || (head === '"' && tail === '"') || (head === "'" && tail === "'")) {
    return raw.slice(1, -1);
  }
  return raw.replace(TRAILING_PUNCTUATION_RE, "");
}

function directiveLabel(type: string, id: string): string {
  if (type === "url") {
    try {
      return new URL(id).hostname || id;
    } catch {
      return id;
    }
  }

  if (type === "session") {
    const sid = id.split("/").filter(Boolean).pop() || id;
    return sid.length > 10 ? `${sid.slice(0, 8)}...` : sid;
  }

  const tail = id.split(/[\\/]/).filter(Boolean).pop();
  return tail || id || type;
}

function DirectiveContent({ text }: { text: string }) {
  const segments = useMemo(() => {
    const out: Array<{ kind: "text"; text: string } | { kind: "directive"; id: string; label: string; type: string }> = [];
    let cursor = 0;

    for (const match of text.matchAll(DIRECTIVE_RE)) {
      const start = match.index ?? 0;
      if (start > cursor) out.push({ kind: "text", text: text.slice(cursor, start) });

      const type = match[1] || "file";
      const id = unwrapDirectiveValue(match[2] || "");
      out.push({ kind: "directive", id, label: directiveLabel(type, id), type });
      cursor = start + match[0].length;
    }

    if (cursor < text.length) out.push({ kind: "text", text: text.slice(cursor) });
    return out;
  }, [text]);

  return (
    <span className="whitespace-pre-line" data-slot="aui_directive-text">
      {segments.map((segment, index) =>
        segment.kind === "text" ? (
          <Fragment key={`t-${index}`}>{segment.text}</Fragment>
        ) : (
          <span
            className="mx-0.5 inline-flex max-w-56 items-center gap-1 rounded bg-[color-mix(in_srgb,currentColor_8%,transparent)] px-1.5 py-0.5 align-middle text-[0.86em] font-normal leading-none text-muted-foreground"
            data-directive-id={segment.id}
            data-directive-type={segment.type}
            data-slot="aui_directive-chip"
            key={`m-${index}-${segment.id}`}
            title={segment.id}
          >
            <span className="truncate">{segment.label}</span>
          </span>
        ),
      )}
    </span>
  );
}

function splitFences(text: string): TopSegment[] {
  const segments: TopSegment[] = [];
  let cursor = 0;

  for (const match of text.matchAll(FENCE_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) segments.push({ kind: "inline", text: text.slice(cursor, start) });
    segments.push({
      kind: "fence",
      lang: (match[1] || "").trim() || null,
      code: match[2] ?? "",
    });
    cursor = start + match[0].length;
  }

  if (cursor < text.length) segments.push({ kind: "inline", text: text.slice(cursor) });
  return segments;
}

function splitInlineCode(text: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(INLINE_CODE_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push({ kind: "inline-text", text: text.slice(cursor, start) });
    nodes.push({ kind: "inline-code", code: match[2] });
    cursor = start + match[0].length;
  }

  if (cursor < text.length) nodes.push({ kind: "inline-text", text: text.slice(cursor) });
  return nodes;
}

interface UserMessageTextProps {
  text: string;
  className?: string;
}

export const UserMessageText: FC<UserMessageTextProps> = ({ className, text }) => {
  const top = useMemo(() => splitFences(text), [text]);

  return (
    <span className={cn("block", className)} data-slot="aui_user-message-text">
      {top.map((segment, segmentIndex) => {
        if (segment.kind === "fence") {
          return (
            <pre
              className="my-1.5 max-w-full overflow-x-auto rounded-md border border-border/45 bg-[color-mix(in_srgb,currentColor_5%,transparent)] px-2.5 py-2 font-mono text-[0.86em] leading-snug"
              data-slot="aui_user-fence"
              key={`fence-${segmentIndex}`}
            >
              <code className="block whitespace-pre">{segment.code}</code>
            </pre>
          );
        }

        return (
          <Fragment key={`inline-${segmentIndex}`}>
            <InlineSegmentView text={segment.text} />
          </Fragment>
        );
      })}
    </span>
  );
};

const InlineSegmentView: FC<{ text: string }> = ({ text }) => {
  const nodes = useMemo(() => splitInlineCode(text), [text]);

  return (
    <span className="wrap-anywhere block whitespace-pre-line" data-slot="aui_user-inline-text">
      {nodes.map((node, nodeIndex) =>
        node.kind === "inline-code" ? (
          <code
            className="mx-px rounded bg-[color-mix(in_srgb,currentColor_8%,transparent)] px-1 py-px font-mono text-[0.92em]"
            data-slot="aui_user-inline-code"
            key={`code-${nodeIndex}`}
          >
            {node.code}
          </code>
        ) : (
          <Fragment key={`text-${nodeIndex}`}>
            <DirectiveContent text={node.text} />
          </Fragment>
        ),
      )}
    </span>
  );
};
