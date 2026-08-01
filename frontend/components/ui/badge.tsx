import * as React from "react";
import { cn } from "@/lib/utils";

export type Variant = "green" | "red" | "amber" | "blue" | "purple" | "cyan" | "gray";

const styles: Record<Variant, string> = {
  green: "bg-accent-green/15 text-accent-green border-accent-green/30",
  red: "bg-accent-red/15 text-accent-red border-accent-red/30",
  amber: "bg-accent-amber/15 text-accent-amber border-accent-amber/30",
  blue: "bg-accent-blue/15 text-accent-blue border-accent-blue/30",
  purple: "bg-brand/15 text-brand-soft border-brand/30",
  cyan: "bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30",
  gray: "bg-white/5 text-text-muted border-border",
};

// Uniswap version -> colour, shared by the gas panel and the cross-chain table
// so "v4" is the same colour wherever it appears.
export const DEX_TONE: Record<string, Variant> = {
  v2: "blue", v3: "amber", v4: "cyan",
};

export function Badge({
  variant = "gray",
  className,
  ...props
}: { variant?: Variant } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium",
        styles[variant],
        className
      )}
      {...props}
    />
  );
}
