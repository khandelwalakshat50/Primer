import { forwardRef, type TextareaHTMLAttributes } from "react";

type Props = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Field = forwardRef<HTMLTextAreaElement, Props>(function Field(
  { className = "", ...props },
  ref,
) {
  return <textarea ref={ref} className={`field ${className}`.trim()} {...props} />;
});
