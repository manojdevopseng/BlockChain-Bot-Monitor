import * as React from "react";
import { cn } from "@/lib/utils";

/* A card is as tall as what is in it.
 *
 * It briefly filled its grid cell instead, so that two panels side by side
 * would end on the same line. That is right for two panels and wrong for
 * everything else: on Settings one column holds a single group and the next
 * holds four stacked, so the single one stretched to match four and the page
 * grew a screenful of empty border. Equal height is a property of a particular
 * pair of panels, not of every card ever drawn — so it is asked for where it
 * is wanted (`className="h-full"`, and put `items-stretch` on the grid) rather
 * than imposed here.
 *
 * The column layout stays: it costs nothing at auto height and it is what lets
 * a card that IS given a height hand the leftover to its content. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-xl border border-border bg-bg-card/60 backdrop-blur-sm",
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

/* Takes whatever room the header does not.
 *
 * At auto height that is simply its own content, so this changes nothing for
 * an ordinary card. It matters only when a card has been given a height on
 * purpose: then the content fills the rest, and `min-h-0` lets a long list
 * scroll inside its own bounds instead of pushing the box past the one beside
 * it — a flex child defaults to min-height auto, which is what allows that. */
export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-h-0 flex-1 px-5 pb-5", className)} {...props} />;
}
