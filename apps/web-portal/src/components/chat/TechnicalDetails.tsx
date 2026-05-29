import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface TechnicalDetailsProps {
  data: any;
}

export function TechnicalDetails({ data }: TechnicalDetailsProps) {
  const [isOpen, setIsOpen] = useState(false);
  if (!data) return null;

  const sections = buildSections(data);

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
          {sections.map((section, i) => (
            <div key={i} className="p-3 rounded-xl bg-canvas border border-subtle">
              <p className="text-[10px] font-bold text-muted uppercase tracking-wider mb-2">
                {section.label}
              </p>
              <pre className="text-[11px] font-mono text-muted whitespace-pre-wrap break-words leading-relaxed max-h-32 overflow-y-auto">
                {section.content}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function buildSections(data: any): { label: string; content: string }[] {
  const sections: { label: string; content: string }[] = [];
  if (!data) return sections;

  if (data.actions_taken) {
    sections.push({ label: "Actions taken", content: data.actions_taken });
  }
  if (data.tools_used) {
    const tools = Array.isArray(data.tools_used) ? data.tools_used.join(", ") : String(data.tools_used);
    sections.push({ label: "Tools used", content: tools });
  }
  if (data.documents_created) {
    const docs = Array.isArray(data.documents_created) ? data.documents_created.join(", ") : String(data.documents_created);
    sections.push({ label: "Documents created", content: docs });
  }

  const rest: Record<string, any> = {};
  for (const [k, v] of Object.entries(data)) {
    if (!["actions_taken", "tools_used", "documents_created"].includes(k)) {
      rest[k] = v;
    }
  }
  if (Object.keys(rest).length > 0) {
    sections.push({ label: "Technical log", content: JSON.stringify(rest, null, 2) });
  }

  return sections;
}
