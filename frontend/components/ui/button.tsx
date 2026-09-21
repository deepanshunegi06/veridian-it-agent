import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type ButtonVariant = "default" | "outline";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

/* Minimal button matching the login card design. Only the two variants the
 * app uses are implemented; no extra component dependency required. */
export function Button({ variant = "default", className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex w-full items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium outline-none transition-colors disabled:pointer-events-none disabled:opacity-50",
        variant === "default" && "bg-primary text-primary-foreground hover:bg-primary/90",
        variant === "outline" &&
          "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
        className,
      )}
      {...props}
    />
  );
}
