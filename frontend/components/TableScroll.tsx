"use client";

// One scroll container for every table in the app.
//
// Vertical: caps the section's height so a page with several stacked sections
// stays a screen or two tall instead of growing with the row count.
// Horizontal: kept from before, so wide tables scroll sideways on a phone
// rather than wrapping every cell into a tall block.
//
// The height is an inline style, not a Tailwind class: `max-h-[${n}px]` built
// from a prop is invisible to Tailwind's scanner and would produce no CSS.
export function TableScroll({
  children,
  maxHeight = 420,
}: {
  children: React.ReactNode;
  maxHeight?: number | false;   // false = no cap (short, fixed-size tables)
}) {
  return (
    <div
      className="overflow-x-auto overflow-y-auto"
      style={maxHeight === false ? undefined : { maxHeight }}
    >
      {children}
    </div>
  );
}

// Header row that stays put while the body scrolls — without this, scrolling a
// capped table loses the column names.
export const STICKY_HEAD =
  "sticky top-0 z-10 bg-bg-card text-left text-[11px] uppercase " +
  "tracking-wider text-text-dim";
