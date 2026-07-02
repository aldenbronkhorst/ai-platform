import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type * as React from "react";

import { cn } from "../../lib/utils";

function TooltipProvider({
  delayDuration = 0,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return <TooltipPrimitive.Provider data-slot="tooltip-provider" delayDuration={delayDuration} {...props} />;
}

function Tooltip(props: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />;
}

function TooltipTrigger(props: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />;
}

function TooltipContent({
  className,
  sideOffset = 6,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        className={cn(
          "z-[200] w-fit max-w-[min(28rem,calc(100vw-1rem))] rounded-sm border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-1.5 py-1 text-[11px] font-bold leading-none text-[var(--color-text)] shadow-sm select-none [font-family:Arial,sans-serif]",
          className,
        )}
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        {...props}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}

interface TipProps extends Omit<React.ComponentProps<typeof TooltipPrimitive.Content>, "content"> {
  children: React.ReactNode;
  delayDuration?: number;
  label: React.ReactNode;
}

function Tip({ label, children, delayDuration = 0, ...props }: TipProps) {
  if (!label) return <>{children}</>;

  return (
    <TooltipProvider delayDuration={delayDuration}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent {...props}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { Tip, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger };
