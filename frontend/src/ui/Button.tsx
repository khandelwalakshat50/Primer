import { type ButtonHTMLAttributes } from "react";

type Variant = "primary" | "ghost";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
};

export function Button({ variant = "primary", className = "", type = "button", ...props }: Props) {
  return <button type={type} className={`btn btn-${variant} ${className}`.trim()} {...props} />;
}
