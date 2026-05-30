import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  table: ({ children }) => (
    <div className="overflow-x-auto my-3">
      <table className="min-w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-bg-subtle border-b border-default">{children}</thead>
  ),
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => (
    <tr className="border-b border-default last:border-0">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="px-3 py-2 text-left text-xs font-semibold text-muted uppercase tracking-wider whitespace-nowrap">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-2 text-sm text-default whitespace-normal break-words">
      {children}
    </td>
  ),
  code: ({ className, children, ...props }) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code
          className="bg-bg-subtle border border-default rounded px-1 py-0.5 text-xs font-mono text-muted"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <pre className="my-3 p-4 bg-bg-subtle border border-default rounded-xl overflow-x-auto text-xs leading-relaxed font-mono text-muted">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    );
  },
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline underline-offset-2 hover:text-accent/80"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="my-2 pl-5 list-disc space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 pl-5 list-decimal space-y-1">{children}</ol>,
  li: ({ children }) => <li className="text-sm leading-relaxed text-default">{children}</li>,
  p: ({ children }) => <p className="text-sm leading-relaxed mb-2 last:mb-0">{children}</p>,
  h1: ({ children }) => <h1 className="text-lg font-bold mt-4 mb-2 text-default">{children}</h1>,
  h2: ({ children }) => <h2 className="text-base font-bold mt-4 mb-2 text-default">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-bold mt-3 mb-1 text-default">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 pl-4 border-l-3 border-accent text-muted italic">{children}</blockquote>
  ),
  hr: () => <hr className="my-4 border-default" />,
};

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
