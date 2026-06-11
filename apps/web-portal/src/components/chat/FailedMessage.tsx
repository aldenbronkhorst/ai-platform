import { RefreshCw } from "lucide-react";
import { useMemo } from "react";

interface ChatError {
  errorType?: string;
  errorMessage: string;
}

const ERROR_HEADINGS: Record<string, string> = {
  timeout: "The request took too long to complete.",
  network: "The AI service could not be reached.",
  model_error: "The model service could not generate a response right now.",
  odoo_error: "I couldn\u2019t retrieve data from a connected system.",
  configuration_error: "AI chat is not configured yet.",
  server_error: "Something went wrong while generating the response.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayValue(value: unknown, fallback = "") {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return fallback;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface FailedMessageProps {
  errorMessage?: string;
  onRetry: () => void;
}

export function FailedMessage({ errorMessage, onRetry }: FailedMessageProps) {
  const parsed = useMemo<ChatError | null>(() => {
    if (!errorMessage) return null;
    try {
      const parsed = JSON.parse(errorMessage) as unknown;
      if (isRecord(parsed) && (parsed.errorMessage || parsed.error_message)) {
        return {
          errorType: displayValue(parsed.errorType ?? parsed.error_type),
          errorMessage: displayValue(
            parsed.errorMessage ?? parsed.error_message,
            "Sorry, I could not complete that request. Please try again.",
          ),
        };
      }
    } catch { /* ignore malformed error payloads */ }
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

      </div>
    </div>
  );
}
