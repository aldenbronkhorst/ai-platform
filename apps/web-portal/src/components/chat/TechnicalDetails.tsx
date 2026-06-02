import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface TechnicalDetailsProps {
  data: unknown;
}

export function TechnicalDetails({ data }: TechnicalDetailsProps) {
  const [isOpen, setIsOpen] = useState(false);
  if (!hasMeaningfulContent(data)) return null;
  const details = data;

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
          {Boolean(details.tools_used) && (
            <Section label="Tools used" content={details.tools_used} />
          )}
          {Boolean(details.actions_taken) && (
            <Section label="Actions taken" content={details.actions_taken} />
          )}
          {Boolean(details.documents_created) && (
            <Section label="Documents created" content={details.documents_created} />
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

function hasMeaningfulContent(data: unknown): data is Record<string, unknown> {
  if (!data || typeof data !== "object") return false;
  const record = data as Record<string, unknown>;
  if (Object.keys(record).length === 0) return false;
  const toolCall = record.tools_used && (
    (Array.isArray(record.tools_used) && record.tools_used.length > 0) ||
    (typeof record.tools_used === "string" && record.tools_used.trim())
  );
  const action = record.actions_taken && typeof record.actions_taken === "string" && record.actions_taken.trim().length > 0;
  const doc = record.documents_created && (
    (Array.isArray(record.documents_created) && record.documents_created.length > 0) ||
    (typeof record.documents_created === "string" && record.documents_created.trim())
  );
  return !!(toolCall || action || doc);
}
