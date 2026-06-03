import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
import type { ChatMessage } from "../../types";

interface PendingAssistantProps {
  message: ChatMessage;
}

interface ProgressContext {
  summary?: string;
  connectors?: string[];
  has_artifacts?: boolean;
  started_at?: string;
}

interface ProgressStep {
  offset: number;
  title: string;
  detail: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function progressContext(message: ChatMessage): ProgressContext {
  const metadata = message.metadata_json;
  if (!isRecord(metadata) || !isRecord(metadata.progress_context)) return {};
  const context = metadata.progress_context;
  return {
    summary: typeof context.summary === "string" ? context.summary : undefined,
    connectors: Array.isArray(context.connectors)
      ? context.connectors.filter((item): item is string => typeof item === "string")
      : undefined,
    has_artifacts: typeof context.has_artifacts === "boolean" ? context.has_artifacts : undefined,
    started_at: typeof context.started_at === "string" ? context.started_at : undefined,
  };
}

function elapsedSeconds(startedAt: string | undefined, fallbackStartedAt: string) {
  const startedMs = Date.parse(startedAt || fallbackStartedAt);
  if (Number.isNaN(startedMs)) return 0;
  return Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function connectorList(connectors: string[]) {
  if (connectors.length === 0) return "connected account";
  if (connectors.length === 1) return connectors[0];
  if (connectors.length === 2) return `${connectors[0]} and ${connectors[1]}`;
  return `${connectors.slice(0, -1).join(", ")}, and ${connectors[connectors.length - 1]}`;
}

function buildSteps(context: ProgressContext): ProgressStep[] {
  const connectors = context.connectors || [];
  const accountLabel = connectorList(connectors);

  return [
    {
      offset: 0,
      title: "Reading the request",
      detail: context.summary ? context.summary : "Understanding what needs to be answered.",
    },
    {
      offset: 3,
      title: context.has_artifacts ? "Checking attached files" : "Loading conversation context",
      detail: context.has_artifacts
        ? "Including uploaded files in the request context."
        : "Using the current chat history and available platform context.",
    },
    {
      offset: 7,
      title: connectors.length > 0 ? `Preparing ${accountLabel} access` : "Selecting model and tools",
      detail: connectors.length > 0
        ? "Permissions still come from your connected account."
        : "Choosing the route that best fits this request.",
    },
    {
      offset: 14,
      title: "Waiting for results",
      detail: "The model or connector call is still running.",
    },
    {
      offset: 35,
      title: "Still working",
      detail: "Longer model or connector calls can take a bit.",
    },
  ];
}

function activeStepIndex(steps: ProgressStep[], elapsed: number) {
  let index = 0;
  for (let i = 0; i < steps.length; i += 1) {
    if (elapsed >= steps[i].offset) index = i;
  }
  return index;
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  const context = useMemo(() => progressContext(message), [message]);
  const [elapsed, setElapsed] = useState(() => elapsedSeconds(context.started_at, message.created_at));

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(elapsedSeconds(context.started_at, message.created_at));
    }, 1000);
    return () => clearInterval(interval);
  }, [context.started_at, message.created_at]);

  const steps = useMemo(() => buildSteps(context), [context]);
  const activeIndex = activeStepIndex(steps, elapsed);
  const activeStep = steps[activeIndex];

  return (
    <div className="w-full flex justify-start">
      <div className="group w-full max-w-2xl min-w-0 py-1">
        <div className="flex items-center gap-2 text-sm">
          <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
          <span className="font-semibold text-default">Working for {formatElapsed(elapsed)}</span>
        </div>

        <p className="mt-1 text-sm text-muted leading-relaxed">
          {activeStep.detail}
        </p>

        <div className="mt-3 border-l border-default pl-3 space-y-2">
          {steps.map((step, index) => {
            const isComplete = index < activeIndex;
            const isActive = index === activeIndex;
            return (
              <div
                key={step.title}
                className={`flex items-start gap-2 text-xs ${isActive ? "text-default" : "text-muted"}`}
              >
                {isComplete ? (
                  <CheckCircle2 className="mt-0.5 w-3.5 h-3.5 text-accent shrink-0" />
                ) : isActive ? (
                  <Loader2 className="mt-0.5 w-3.5 h-3.5 text-accent animate-spin shrink-0" />
                ) : (
                  <CircleDashed className="mt-0.5 w-3.5 h-3.5 text-soft shrink-0" />
                )}
                <div className="min-w-0">
                  <div className="font-semibold leading-snug">{step.title}</div>
                  <div className="text-[11px] text-muted leading-snug">{step.detail}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
