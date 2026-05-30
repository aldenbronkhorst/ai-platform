import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface TechnicalDetailsProps {
  data: any;
}

export function TechnicalDetails({ data }: TechnicalDetailsProps) {
  const [isOpen, setIsOpen] = useState(false);
  if (!hasMeaningfulContent(data)) return null;

  return (
    <div className="mt-3 pt-3 border-t border-default">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 text-[11px] text-muted hover:text-default font-semibold select-none transition-colors"
      >
        {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        View details
      </button>

      {isOpen && (
        <div className="mt-2 space-y-3">
          {data.tools_used && (
            <Section label="Tools used" content={data.tools_used} />
          )}
          {data.actions_taken && (
            <Section label="Actions taken" content={data.actions_taken} />
          )}
          {data.documents_created && (
            <Section label="Documents created" content={data.documents_created} />
          )}
        </div>
      )}
    </div>
  );
}

function Section({ label, content }: { label: string; content: unknown }) {
  const text = Array.isArray(content) ? content.join(", ") : String(content);
  return (
    <div className="p-3 rounded-xl bg-canvas border border-subtle">
      <p className="text-[10px] font-bold text-muted uppercase tracking-wider mb-2">
        {label}
      </p>
      <pre className="text-[11px] font-mono text-muted whitespace-pre-wrap break-words leading-relaxed max-h-32 overflow-y-auto">
        {text}
      </pre>
    </div>
  );
}

function hasMeaningfulContent(data: any): boolean {
  if (!data || typeof data !== "object") return false;
  if (Object.keys(data).length === 0) return false;
  const toolCall = data.tools_used && (
    (Array.isArray(data.tools_used) && data.tools_used.length > 0) ||
    (typeof data.tools_used === "string" && data.tools_used.trim())
  );
  const action = data.actions_taken && typeof data.actions_taken === "string" && data.actions_taken.trim().length > 0;
  const doc = data.documents_created && (
    (Array.isArray(data.documents_created) && data.documents_created.length > 0) ||
    (typeof data.documents_created === "string" && data.documents_created.trim())
  );
  return !!(toolCall || action || doc);
}
