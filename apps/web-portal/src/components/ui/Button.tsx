import type { ReactNode, ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: "default" | "primary" | "danger";
  size?: "sm" | "md";
}

export function Button({
  children,
  variant = "default",
  size = "md",
  className = "",
  ...props
}: ButtonProps) {
  const sizeClass = size === "sm" ? "px-3 py-1.5 text-[11px]" : "px-4 py-2.5 text-xs";
  const variantClass = variant === "danger"
    ? "text-[var(--color-danger)]"
    : variant === "primary"
      ? "text-default"
      : "";

  return (
    <button
      className={`ui-button flex items-center justify-center gap-2 font-bold tracking-wide ${sizeClass} ${variantClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
