import type { ReactNode } from "react";

interface GlassPanelProps {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "article";
}

export function GlassPanel({ children, className = "", as: Tag = "div" }: GlassPanelProps) {
  return (
    <Tag className={`glass-panel ${className}`}>
      {children}
    </Tag>
  );
}
