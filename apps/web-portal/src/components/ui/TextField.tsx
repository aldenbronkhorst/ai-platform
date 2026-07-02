import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  multiline?: false;
}

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  multiline: true;
}

type Props = TextInputProps | TextAreaProps;

export function TextField(props: Props) {
  const base = "w-full rounded-lg border border-default bg-transparent px-4 py-3 text-xs text-default placeholder-soft transition-colors focus:border-subtle focus:outline-none";

  if (props.multiline) {
    const { multiline, ...rest } = props;
    void multiline;
    return (
      <textarea
        className={`${base} resize-none`}
        {...rest}
      />
    );
  }

  const { multiline, ...rest } = props;
  void multiline;
  return (
    <input
      className={base}
      {...rest}
    />
  );
}
