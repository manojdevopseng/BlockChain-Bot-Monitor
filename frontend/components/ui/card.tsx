import * as React from "react";
import { cn } from "@/lib/utils";

/* A card fills the cell it is given.
 *
 * Grid rows stretch by default, so two cards side by side already occupy the
 * same height — but the *card* used to be only as tall as its own content,
 * leaving a short one floating in a tall cell with a border ending halfway
 * down. `h-full` makes the box take the row, and the column layout below lets
 * CardContent take whatever the header does not, so a table or a list inside
 * two unequal cards ends at the same line. It is what stops a page needing to
 * be scrolled to compare two things meant to be compared.
 *
 * A card that should NOT stretch passes `h-auto` — className wins, because cn
 * resolves conflicts in favour of the later class. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex h-full flex-col rounded-xl border border-border bg-bg-card/60 backdrop-blur-sm",
        className
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex shrink-0 items-center justify-between px-5 pt-4 pb-2", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-xs font-semibold uppercase tracking-wider text-text-muted", className)}
      {...props}
    />
  );
}

/* Takes the room the header does not, so the bottom edges line up.
 *
 * `min-h-0` matters more than it looks: a flex child defaults to min-height
 * auto, which lets its content push it past the cell instead of scrolling
 * inside it — which is how a long list ends up stretching a card past the one
 * beside it rather than scrolling within its own bounds. */
export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-h-0 flex-1 px-5 pb-5", className)} {...props} />;
}
