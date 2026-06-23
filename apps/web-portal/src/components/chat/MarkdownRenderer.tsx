import type { ReactNode } from "react";

const inlinePattern = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

function safeHref(href: string) {
  if (/^(https?:|mailto:)/i.test(href)) return href;
  return undefined;
}

function parseInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let matchIndex = 0;
  inlinePattern.lastIndex = 0;

  for (const match of text.matchAll(inlinePattern)) {
    const start = match.index ?? 0;
    if (start > lastIndex) {
      nodes.push(text.slice(lastIndex, start));
    }

    const key = `${keyPrefix}-inline-${matchIndex}`;
    if (match[1]) {
      nodes.push(<code key={key}>{match[1]}</code>);
    } else if (match[2]) {
      nodes.push(<strong key={key}>{match[2]}</strong>);
    } else if (match[3]) {
      nodes.push(<em key={key}>{match[3]}</em>);
    } else if (match[4] && match[5]) {
      const href = safeHref(match[5]);
      nodes.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {match[4]}
          </a>
        ) : match[4]
      );
    }

    lastIndex = start + match[0].length;
    matchIndex += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length > 0 ? nodes : [text];
}

function isEscapedPipe(text: string, index: number) {
  let slashCount = 0;
  for (let i = index - 1; i >= 0 && text[i] === "\\"; i -= 1) {
    slashCount += 1;
  }
  return slashCount % 2 === 1;
}

function trimTableBoundaryPipes(line: string) {
  let trimmed = line.trim();
  if (trimmed.startsWith("|")) {
    trimmed = trimmed.slice(1);
  }
  if (trimmed.endsWith("|") && !isEscapedPipe(trimmed, trimmed.length - 1)) {
    trimmed = trimmed.slice(0, -1);
  }
  return trimmed;
}

function splitTableRow(line: string) {
  const trimmed = trimTableBoundaryPipes(line);
  const cells: string[] = [];
  let current = "";

  for (let index = 0; index < trimmed.length; index += 1) {
    const char = trimmed[index];
    if (char === "|" && !isEscapedPipe(trimmed, index)) {
      cells.push(current.trim().replace(/\\\|/g, "|"));
      current = "";
    } else {
      current += char;
    }
  }

  cells.push(current.trim().replace(/\\\|/g, "|"));
  return cells;
}

function isTableSeparator(line: string) {
  const cells = splitTableRow(line);
  return cells.length > 1 && cells.every(cell => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

function isTableLikeRow(line: string) {
  return line.trim().startsWith("|") && splitTableRow(line).length > 1;
}

function flexibleCellIndex(headers: string[]) {
  const index = headers.findIndex(header => /product|name|description|detail/i.test(header));
  return index >= 0 ? index : 0;
}

function normalizeTableRow(cells: string[], headers: string[]) {
  if (cells.length === headers.length) return cells;
  if (cells.length < headers.length) {
    return [...cells, ...Array.from({ length: headers.length - cells.length }, () => "")];
  }

  const targetIndex = flexibleCellIndex(headers);
  const extraCount = cells.length - headers.length;
  const mergedCell = cells.slice(targetIndex, targetIndex + extraCount + 1).join(" | ");
  return [
    ...cells.slice(0, targetIndex),
    mergedCell,
    ...cells.slice(targetIndex + extraCount + 1),
  ];
}

function isHorizontalRule(line: string) {
  return /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line);
}

function isUnorderedListItem(line: string) {
  return /^\s*[-*+]\s+/.test(line);
}

function isOrderedListItem(line: string) {
  return /^\s*\d+[.)]\s+/.test(line);
}

function listContent(line: string) {
  return line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "");
}

function headingLevel(line: string) {
  const match = line.match(/^(#{1,4})\s+(.+)$/);
  if (!match) return null;
  return { level: match[1].length, text: match[2].trim() };
}

function isTableStart(lines: string[], index: number) {
  if (index + 1 >= lines.length) return false;
  const header = splitTableRow(lines[index]);
  return header.length > 1 && isTableSeparator(lines[index + 1]);
}

function startsNewBlock(lines: string[], index: number) {
  const line = lines[index];
  const trimmed = line.trim();
  return !trimmed
    || trimmed.startsWith("```")
    || Boolean(headingLevel(trimmed))
    || trimmed.startsWith(">")
    || isHorizontalRule(line)
    || isUnorderedListItem(line)
    || isOrderedListItem(line)
    || isTableStart(lines, index);
}

function renderHeading(level: number, text: string, key: string) {
  if (level === 1) return <h1 key={key}>{parseInline(text, key)}</h1>;
  if (level === 2) return <h2 key={key}>{parseInline(text, key)}</h2>;
  if (level === 3) return <h3 key={key}>{parseInline(text, key)}</h3>;
  return <h4 key={key}>{parseInline(text, key)}</h4>;
}

function renderTable(lines: string[], startIndex: number, key: string) {
  const headers = splitTableRow(lines[startIndex]);
  const rows: string[][] = [];
  let index = startIndex + 2;

  while (index < lines.length && lines[index].trim() && isTableLikeRow(lines[index])) {
    rows.push(normalizeTableRow(splitTableRow(lines[index]), headers));
    index += 1;
  }

  return {
    nextIndex: index,
    node: (
      <div key={key} className="overflow-x-auto my-3">
        <table>
          <thead>
            <tr>
              {headers.map((cell, cellIndex) => (
                <th key={`${key}-h-${cellIndex}`}>{parseInline(cell, `${key}-h-${cellIndex}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${key}-r-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`${key}-r-${rowIndex}-${cellIndex}`}>
                    {parseInline(cell, `${key}-r-${rowIndex}-${cellIndex}`)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
  };
}

function renderBlocks(content: string) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const rawLine = lines[index];
    const trimmed = rawLine.trim();
    const key = `block-${index}`;

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre key={key}>
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const heading = headingLevel(trimmed);
    if (heading) {
      blocks.push(renderHeading(heading.level, heading.text, key));
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const table = renderTable(lines, index, key);
      blocks.push(table.node);
      index = table.nextIndex;
      continue;
    }

    if (isUnorderedListItem(rawLine) || isOrderedListItem(rawLine)) {
      const ordered = isOrderedListItem(rawLine);
      const items: string[] = [];
      while (
        index < lines.length
        && (ordered ? isOrderedListItem(lines[index]) : isUnorderedListItem(lines[index]))
      ) {
        items.push(listContent(lines[index]));
        index += 1;
      }

      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={key}>
          {items.map((item, itemIndex) => (
            <li key={`${key}-item-${itemIndex}`}>{parseInline(item, `${key}-item-${itemIndex}`)}</li>
          ))}
        </ListTag>
      );
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quoteLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(
        <blockquote key={key}>
          {parseInline(quoteLines.join(" "), key)}
        </blockquote>
      );
      continue;
    }

    if (isHorizontalRule(rawLine)) {
      blocks.push(<hr key={key} />);
      index += 1;
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && !startsNewBlock(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    blocks.push(
      <p key={key}>{parseInline(paragraphLines.join(" "), key)}</p>
    );
  }

  return blocks;
}

export function MarkdownRenderer({ content }: { content: string }) {
  return <div className="markdown-body">{renderBlocks(content)}</div>;
}
