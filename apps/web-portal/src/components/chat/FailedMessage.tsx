import { AlertCircle, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useState } from "react";

interface FailedMessageProps {
  errorMessage?: string;
  onRetry: () => void;
}

export function FailedMessage({ errorMessage, onRetry }: FailedMessageProps) {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <div className="flex gap-3 justify-start">
      <div className="w-8 h-8 rounded-lg bg-danger/10 border border-danger/20 flex items-center justify-center shrink-0">
        <AlertCircle className="w-4 h-4 text-danger" />
      </div>

      <div className="max-w-[75%] p-4 rounded-2xl border bg-canvas border-danger/20">
        <p className="text-xs font-semibold text-danger mb-1">
          Something went wrong while generating the response.
        </p>
        <p className="text-[11px] text-muted mb-3">
          {errorMessage || "Sorry, I could not complete that request. Please try again."}
        </p>

        <div className="flex items-center gap-2">
          <button
            onClick={onRetry}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-white text-[11px] font-semibold hover:opacity-90 transition-all"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Retry
          </button>

          {errorMessage && (
            <button
              onClick={() => setShowDetails(!showDetails)}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-default font-semibold transition-colors"
            >
              {showDetails ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              View details
            </button>
          )}
        </div>

        {showDetails && errorMessage && (
          <pre className="mt-3 p-3 rounded-xl bg-canvas border border-subtle text-[10px] font-mono text-muted whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
            {errorMessage}
          </pre>
        )}
      </div>
    </div>
  );
}
