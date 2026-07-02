import type { ReactNode } from "react";

interface SurfacePanelProps {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "article";
}

export function SurfacePanel({ children, className = "", as: Tag = "div" }: SurfacePanelProps) {
  return (
    <Tag className={`surface-panel ${className}`}>
      {children}
    </Tag>
  );
}
