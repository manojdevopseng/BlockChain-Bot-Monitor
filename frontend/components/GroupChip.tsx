"use client";

/* The one place a premium-group chip is drawn.
 *
 * The Detections table had two copies of it — a plain <Badge> for groups with
 * no message link and an <a> with the same styling hand-written for the rest —
 * which is exactly the kind of duplication a per-group colour would have had
 * to be added to twice. */

export type ChipStyle = { bg: string; text: string; border: string };

/** chat id (as a string) -> that group's chosen colours. */
export type ChipMap = Record<string, ChipStyle>;

export function chipStyleOf(chips: ChipMap | undefined, chatId?: number | null) {
  if (!chips || chatId == null) return undefined;
  return chips[String(chatId)];
}

const BASE =
  "inline-flex max-w-[130px] items-center gap-1 truncate rounded-md border " +
  "px-2 py-0.5 text-[11px] font-medium transition-colors";

// Used when the group has no colours of its own. Same look the chips have
// always had, so a group nobody has styled is untouched.
const DEFAULT = "border-border bg-white/5 text-text-muted";
const DEFAULT_LINK = `${DEFAULT} hover:border-brand/40 hover:text-brand-soft`;

export function GroupChip({ label, url, style, title }: {
  label: string;
  url?: string | null;
  style?: ChipStyle;
  title?: string;
}) {
  const custom = style
    ? { background: style.bg, color: style.text, borderColor: style.border }
    : undefined;
  const className = `${BASE} ${style ? "" : url ? DEFAULT_LINK : DEFAULT}`;

  if (!url) {
    return <span className={className} style={custom} title={title}>{label}</span>;
  }
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" title={title}
       className={className} style={custom}>
      {label}
    </a>
  );
}
