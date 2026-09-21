import { cn } from "@/lib/utils";
import type { HTMLAttributes, LabelHTMLAttributes } from "react";

export function FieldGroup({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div data-slot="field-group" className={cn("flex w-full flex-col gap-4", className)} {...props} />
  );
}

export function Field({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div data-slot="field" className={cn("flex flex-col gap-2", className)} {...props} />
  );
}

export function FieldLabel({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      data-slot="field-label"
      className={cn("text-sm font-medium text-slate-900", className)}
      {...props}
    />
  );
}

export function FieldDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p data-slot="field-description" className={cn("text-xs text-slate-500", className)} {...props} />
  );
}

export function FieldSeparator({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  const { children, ...rest } = props;
  return (
    <div
      data-slot="field-separator"
      className={cn("flex items-center gap-2 text-xs text-slate-500", className)}
      {...rest}
    >
      <span aria-hidden="true" className="h-px flex-1 bg-slate-200" />
      <span data-slot="field-separator-content" className="bg-card px-2">
        {children}
      </span>
      <span aria-hidden="true" className="h-px flex-1 bg-slate-200" />
    </div>
  );
}
