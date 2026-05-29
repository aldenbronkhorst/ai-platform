import type { ReactNode, ButtonHTMLAttributes } from "react";

interface GlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: "default" | "primary" | "danger";
  size?: "sm" | "md";
}

export function GlassButton({
  children,
  variant = "default",
  size = "md",
  className = "",
  ...props
}: GlassButtonProps) {
  const sizeClass = size === "sm" ? "px-3 py-1.5 text-[11px]" : "px-4 py-2.5 text-xs";

  return (
    <button
      className={`glass-btn font-bold tracking-wide transition-all flex items-center justify-center gap-2 ${sizeClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
