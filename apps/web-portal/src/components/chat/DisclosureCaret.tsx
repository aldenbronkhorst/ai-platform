import { cn } from "../../lib/utils";
import { Codicon, type CodiconProps } from "../ui/Codicon";

interface DisclosureCaretProps extends Omit<CodiconProps, "name"> {
  open: boolean;
}

export function DisclosureCaret({ className, open, size = "0.75rem", ...props }: DisclosureCaretProps) {
  return (
    <Codicon
      className={cn("transition-transform duration-150", open && "rotate-90", className)}
      name="chevron-right"
      size={size}
      {...props}
    />
  );
}
