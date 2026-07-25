import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-9 w-full rounded-lg border border-border bg-bg-soft px-3 text-sm text-text",
      "placeholder:text-text-dim focus:border-brand/60 focus:outline-none focus:ring-1 focus:ring-brand/40",
      className
    )}
    {...props}
  />
));
Input.displayName = "Input";
