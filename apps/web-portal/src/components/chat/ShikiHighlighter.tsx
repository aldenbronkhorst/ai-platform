import type { SyntaxHighlighterProps } from "@assistant-ui/react-streamdown";
import { type FC, useMemo } from "react";
import ShikiHighlighter from "react-shiki";

import { codiconForLanguage, isLikelyProseCodeBlock, sanitizeLanguageTag } from "../../lib/markdown-code";
import {
  CodeCard,
  CodeCardBody,
  CodeCardHeader,
  CodeCardIcon,
  CodeCardSubtitle,
  CodeCardTitle,
} from "./CodeCard";
import { ExpandableBlock } from "./ExpandableBlock";
import { CopyButton } from "../ui/CopyButton";

interface HermesSyntaxHighlighterProps extends SyntaxHighlighterProps {
  defer?: boolean;
}

const SHIKI_THEME = { dark: "github-dark-dimmed", light: "github-light-default" } as const;

const SHIKI_COLOR_REPLACEMENTS: Record<string, Record<string, string>> = {
  "github-light-default": { "#6e7781": "#57606a" },
};

const MAX_HIGHLIGHT_CHARS = 150_000;
const MAX_HIGHLIGHT_LINES = 3_000;
const CHUNK_LINES = 200;
const EST_LINE_PX = 16;

function exceedsHighlightBudget(codeText: string): boolean {
  if (codeText.length > MAX_HIGHLIGHT_CHARS) {
    return true;
  }

  let lines = 1;
  let idx = codeText.indexOf("\n");

  while (idx !== -1) {
    if ((lines += 1) > MAX_HIGHLIGHT_LINES) {
      return true;
    }

    idx = codeText.indexOf("\n", idx + 1);
  }

  return false;
}

interface CodeChunk {
  text: string;
  lines: number;
}

function chunkByLines(codeText: string, perChunk: number): CodeChunk[] {
  const lines = codeText.split("\n");

  if (lines.length <= perChunk) {
    return [{ text: codeText, lines: lines.length }];
  }

  const chunks: CodeChunk[] = [];

  for (let i = 0; i < lines.length; i += perChunk) {
    const slice = lines.slice(i, i + perChunk);
    chunks.push({ text: slice.join("\n"), lines: slice.length });
  }

  return chunks;
}

const PlainCode: FC<{ code: string }> = ({ code }) => {
  const chunks = useMemo(() => chunkByLines(code, CHUNK_LINES), [code]);

  if (chunks.length === 1) {
    return <code className="block whitespace-pre">{code}</code>;
  }

  return (
    <>
      {chunks.map((chunk, index) => (
        <code
          className="block whitespace-pre [content-visibility:auto]"
          key={index}
          style={{ containIntrinsicSize: `auto ${chunk.lines * EST_LINE_PX}px` }}
        >
          {chunk.text}
        </code>
      ))}
    </>
  );
};

export const SyntaxHighlighter: FC<HermesSyntaxHighlighterProps> = ({
  components: { Pre },
  language,
  code,
  defer = false,
}) => {
  const trimmed = (code ?? "").replace(/^\n+/, "").trimEnd();

  if (!trimmed.trim()) {
    return null;
  }

  if (isLikelyProseCodeBlock(language, trimmed)) {
    return <div className="aui-prose-fence whitespace-pre-wrap wrap-anywhere text-foreground">{trimmed}</div>;
  }

  const cleanLanguage = sanitizeLanguageTag(language || "");
  const label = cleanLanguage && cleanLanguage !== "unknown" ? cleanLanguage : "";
  const plain = defer || exceedsHighlightBudget(trimmed);

  return (
    <CodeCard data-streaming={defer ? "true" : undefined}>
      <CodeCardHeader>
        <CodeCardTitle>
          <CodeCardIcon name={codiconForLanguage(label)} />
          Code
          {label && <CodeCardSubtitle> · {label}</CodeCardSubtitle>}
        </CodeCardTitle>
        <CopyButton
          className="-my-1 -mr-1 h-5 px-1 opacity-55 hover:opacity-100"
          iconClassName="size-2.5"
          label="Copy code"
          showLabel={false}
          text={trimmed}
        />
      </CodeCardHeader>
      <CodeCardBody>
        <ExpandableBlock>
          <Pre className="aui-shiki m-0 overflow-hidden bg-transparent p-0">
            {plain ? (
              <PlainCode code={trimmed} />
            ) : (
              <ShikiHighlighter
                addDefaultStyles={false}
                as="div"
                colorReplacements={SHIKI_COLOR_REPLACEMENTS}
                defaultColor="light-dark()"
                delay={120}
                language={language || "text"}
                showLanguage={false}
                theme={SHIKI_THEME}
              >
                {trimmed}
              </ShikiHighlighter>
            )}
          </Pre>
        </ExpandableBlock>
      </CodeCardBody>
    </CodeCard>
  );
};
