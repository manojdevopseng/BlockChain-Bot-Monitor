"use client";

import { ExternalLink } from "lucide-react";

/* Turn the URLs in a caller's message into links.

   Rendered, not stored: the text stays exactly as it arrived and this only
   decides how to draw it, so it costs a regex pass per message and nothing
   else — no request, no backend change, no second copy of the text.

   These messages come from a hundred groups nobody here controls, and drainer
   links are a routine part of that feed. Two things follow from it. Every link
   opens in its own tab with `noopener noreferrer`, so the page it lands on
   cannot reach back into the dashboard or learn where the click came from. And
   the link text is the URL itself — we store Telegram's plain text, which
   strips the entity links where the words shown and the address behind them
   differ, so the most dangerous case never reaches this component. */

// Trailing punctuation belongs to the sentence, not the address: "see x.com."
// should not link the full stop, and "(see x.com)" should not link the bracket.
const TRAILING = /[.,;:!?)\]}'"»]+$/;
const URL_RE = /(https?:\/\/[^\s<>"']+|(?:www|t)\.me\/[^\s<>"']+|www\.[^\s<>"']+)/gi;

function href(raw: string) {
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
}

export function Linkify({ text, className }: { text?: string; className?: string }) {
  if (!text) return null;

  const parts: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  URL_RE.lastIndex = 0;

  while ((m = URL_RE.exec(text)) !== null) {
    let raw = m[0];
    const trail = raw.match(TRAILING)?.[0] ?? "";
    if (trail) raw = raw.slice(0, -trail.length);
    if (!raw) continue;

    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(
      <a
        key={`${m.index}-${raw}`}
        href={href(raw)}
        target="_blank"
        rel="noopener noreferrer"
        title={href(raw)}
        className="break-all text-accent-blue underline decoration-accent-blue/40
                   underline-offset-2 hover:decoration-accent-blue"
        onClick={(e) => e.stopPropagation()}
      >
        {raw}
        <ExternalLink size={10} className="ml-0.5 inline-block align-baseline opacity-60" />
      </a>,
    );
    last = m.index + m[0].length - trail.length;
  }

  if (last < text.length) parts.push(text.slice(last));

  return <span className={className}>{parts}</span>;
}
