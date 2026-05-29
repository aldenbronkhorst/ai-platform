import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";

interface GlassInputProps extends InputHTMLAttributes<HTMLInputElement> {
  multiline?: false;
}

interface GlassTextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  multiline: true;
}

type Props = GlassInputProps | GlassTextareaProps;

export function GlassInput(props: Props) {
  const base = "w-full px-4 py-3 bg-transparent border border-default rounded-xl focus:outline-none focus:border-soft text-xs text-default placeholder-soft transition-all";

  if (props.multiline) {
    const { multiline: _, ...rest } = props as GlassTextareaProps;
    return (
      <textarea
        className={`${base} resize-none`}
        {...(rest as any)}
      />
    );
  }

  const { multiline: _, ...rest } = props as GlassInputProps;
  return (
    <input
      className={base}
      {...rest}
    />
  );
}
