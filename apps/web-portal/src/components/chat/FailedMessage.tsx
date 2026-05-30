import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useState, useMemo } from "react";

interface ChatError {
  requestId?: string;
  errorType?: string;
  errorMessage: string;
  technicalDetail?: string;
  httpStatus?: number;
}

const ERROR_HEADINGS: Record<string, string> = {
  timeout: "The request took too long to complete.",
  network: "The AI service could not be reached.",
  model_error: "The model service could not generate a response right now.",
  odoo_error: "I couldn\u2019t retrieve data from a connected system.",
  configuration_error: "AI chat is not configured yet.",
  server_error: "Something went wrong while generating the response.",
};

interface FailedMessageProps {
  errorMessage?: string;
  onRetry: () => void;
}

export function FailedMessage({ errorMessage, onRetry }: FailedMessageProps) {
  const [showDetails, setShowDetails] = useState(false);

  const parsed = useMemo<ChatError | null>(() => {
    if (!errorMessage) return null;
    try {
      const parsed = JSON.parse(errorMessage);
      if (parsed && typeof parsed === "object" && parsed.errorMessage) {
        return parsed as ChatError;
      }
    } catch {}
    return null;
  }, [errorMessage]);

  const heading = parsed
    ? ERROR_HEADINGS[parsed.errorType || ""] || "Something went wrong while generating the response."
    : "Something went wrong while generating the response.";

  const message = parsed?.errorMessage || errorMessage || "Sorry, I could not complete that request. Please try again.";

  return (
    <div className="max-w-[75%]">
      <p className="text-xs font-semibold text-danger mb-1">
        {heading}
      </p>
      <p className="text-[11px] text-muted mb-3">
        {message}
      </p>

      <div className="flex items-center gap-2">
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-white text-[11px] font-semibold hover:opacity-90 transition-all"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Retry
        </button>

        {parsed && (
          <button
            onClick={() => setShowDetails(!showDetails)}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-default font-semibold transition-colors"
          >
            {showDetails ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            View details
          </button>
        )}
      </div>

      {showDetails && parsed && (
        <div className="mt-3 p-3 rounded-xl bg-canvas border border-subtle text-[10px] font-mono text-muted whitespace-pre-wrap break-words space-y-1.5">
          {parsed.requestId && (
            <div><span className="font-semibold text-default">Request ID:</span> {parsed.requestId}</div>
          )}
          {parsed.httpStatus ? (
            <div><span className="font-semibold text-default">Status:</span> HTTP {parsed.httpStatus}</div>
          ) : null}
          {parsed.errorType && (
            <div><span className="font-semibold text-default">Error type:</span> {parsed.errorType}</div>
          )}
          {parsed.technicalDetail && (
            <div><span className="font-semibold text-default">Details:</span> {parsed.technicalDetail}</div>
          )}
        </div>
      )}
    </div>
  );
}
