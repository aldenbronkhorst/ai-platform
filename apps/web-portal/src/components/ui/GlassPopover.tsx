import type { ReactNode } from "react";

interface GlassPopoverProps {
  children: ReactNode;
  className?: string;
}

export function GlassPopover({ children, className = "" }: GlassPopoverProps) {
  return (
    <div
      className={`bg-raised border border-default rounded-2xl shadow-2xl p-2 animate-fade-in ${className}`}
    >
      {children}
    </div>
  );
}
