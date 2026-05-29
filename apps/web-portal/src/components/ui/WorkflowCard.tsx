import { ArrowRight } from "lucide-react";
import type { WorkflowCardData } from "../../types";

interface WorkflowCardProps {
  workflow: WorkflowCardData;
  onSelect: (workflow: WorkflowCardData) => void;
}

export function WorkflowCard({ workflow, onSelect }: WorkflowCardProps) {
  return (
    <div
      onClick={() => onSelect(workflow)}
      className="p-6 glass-panel rounded-2xl cursor-pointer flex flex-col justify-between h-full transition-all hover:bg-glass-hover"
    >
      <div>
        <h4 className="font-bold text-sm text-default mb-2">{workflow.title}</h4>
        <p className="text-xs text-muted leading-relaxed">{workflow.description}</p>
      </div>
      <div className="mt-5 flex items-center gap-1.5 text-xs text-muted font-semibold hover-text-default transition-all">
        Configure <ArrowRight className="w-3.5 h-3.5" />
      </div>
    </div>
  );
}
