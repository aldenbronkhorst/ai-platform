import type { ReactNode } from "react";
import { cn } from "../../lib/utils";
import { DisclosureCaret } from "./DisclosureCaret";

export function DisclosureRow({
  action,
  children,
  onToggle,
  open,
  trailing,
}: {
  action?: ReactNode;
  children: ReactNode;
  onToggle?: () => void;
  open: boolean;
  trailing?: ReactNode;
}) {
  return (
    <div className="group/disclosure-row relative flex w-full max-w-full min-w-0 text-[var(--ui-text-tertiary)]">
      <button
        aria-expanded={onToggle ? open : undefined}
        className={cn(
          "flex min-w-0 max-w-fit items-start gap-1.5 text-left transition-colors",
          onToggle ? "hover:text-foreground focus-visible:text-foreground focus-visible:outline-none" : "cursor-default",
        )}
        disabled={!onToggle}
        onClick={onToggle}
        type="button"
      >
        <span className="flex min-w-0 flex-col gap-0.5">{children}</span>
        {onToggle && (
          <span
            className={cn(
              "flex h-[var(--conversation-line-height)] shrink-0 items-center justify-center transition-opacity duration-150",
              open ? "opacity-80" : "opacity-0 group-hover/disclosure-row:opacity-80 group-focus-within/disclosure-row:opacity-80",
            )}
          >
            <DisclosureCaret open={open} />
          </span>
        )}
      </button>
      {action && (
        <span className="ml-auto flex h-[var(--conversation-line-height)] shrink-0 items-center self-start pl-1.5">
          {action}
        </span>
      )}
      {trailing && (
        <span className="absolute right-1 top-0 flex h-[var(--conversation-line-height)] items-center">
          {trailing}
        </span>
      )}
    </div>
  );
}
