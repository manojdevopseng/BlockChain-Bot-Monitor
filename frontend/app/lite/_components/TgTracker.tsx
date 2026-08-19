"use client";

import { ExternalLink, CornerUpLeft, Users } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge, Variant } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { AuthImage } from "@/components/AuthImage";
import { Linkify } from "@/components/Linkify";
import { shortAddr } from "@/lib/utils";
import { ChipMap, chipStyleOf } from "@/components/GroupChip";

/* Every premium message, newest first — the same feed the mirror group carries,
   and for the same reason: what a caller says around a call is most of the
   read, so the posts either side of it belong here too.

   A message arrives as text the moment it lands. Its token chips appear a beat
   later, when the chain check comes back. That order is deliberate: the
   alternative is a feed that is always complete and always late. */

type Token = { chain?: string; symbol?: string; address?: string; gmgn_url?: string };

type Entry = {
  chat_id?: number;
  msg_id?: number;
  group?: string;
  username?: string | null;
  followers?: number | null;
  post_url?: string;
  text?: string;
  reply_to?: string | null;
  reply_text?: string;
  media_id?: string | null;
  ts?: number;
  tokens?: Token[];
};

const CHAIN_LABEL: Record<string, string> = {
  eth: "ETH", rbh: "RBH", bnb: "BNB", sol: "SOL", base: "BASE",
};
const CHAIN_TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
};

// The clock time the message landed, not how long ago. "3m" tells you the gap
// but not the moment, and the moment is what you match against Telegram when
// you go looking for the post. The date is added only when it is not today,
// so the common case stays short.
function stamp(ts?: number) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString("en-GB");
  const today = new Date();
  const sameDay = d.getDate() === today.getDate()
    && d.getMonth() === today.getMonth()
    && d.getFullYear() === today.getFullYear();
  return sameDay ? time : `${d.toLocaleDateString("en-GB")} ${time}`;
}

function fmtFollowers(n?: number | null) {
  if (!n) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(n);
}

export function TgTracker({ chain, q }: { chain: string; q: string }) {
  const { data } = useApi<any>(
    `/api/calls/tracker?chain=${chain}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
  );
  // Per-caller box colours, set in Forwarder → Premium Groups. Its own request
  // rather than a field on every message: one caller posting forty times would
  // otherwise repeat its three colours forty times.
  const { data: styleData } = useApi<any>("/api/forwarder/group-chips");
  const boxes: ChipMap | undefined = styleData?.tracker;
  const items: Entry[] = data?.items ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-border bg-bg-card/60">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <h2 className="text-sm font-semibold text-text">TG Tracker</h2>
        <span className="text-[11px] text-text-dim">{items.length} messages</span>
      </div>

      {/* Each message is its own box. A rule between them is not enough: a
          caller's post is often several lines with blank lines of its own, and
          a single line cannot say where one post ends and the next begins. */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2">
        {items.length === 0 ? (
          <p className="px-3 py-10 text-center text-xs text-text-dim">
            Nothing yet. Every message from a premium caller appears here as it
            arrives.
          </p>
        ) : items.map((e) => {
          const tokens = e.tokens ?? [];
          const box = chipStyleOf(boxes, e.chat_id);
          return (
            <article
              key={`${e.chat_id}-${e.msg_id}`}
              // A styled caller gets its own surface; an unstyled one keeps the
              // default, so colouring one group does not make the rest look
              // like an oversight.
              className={`shrink-0 overflow-hidden rounded-lg border px-3 py-2.5 transition-colors ${
                box ? "" : "border-border bg-bg-soft/50 hover:bg-bg-hover/40"
              }`}
              style={box ? { background: box.bg, borderColor: box.border, color: box.text }
                         : undefined}
            >
              <header className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                  <span className={`truncate text-sm font-semibold ${box ? "" : "text-text"}`}>
                    {e.group || "Unknown"}
                  </span>
                  {e.username && (
                    <span className="font-mono text-[11px] text-text-dim">@{e.username}</span>
                  )}
                  {fmtFollowers(e.followers) && (
                    <span className="inline-flex items-center gap-0.5 text-[11px] text-text-dim">
                      <Users size={10} />{fmtFollowers(e.followers)}
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-text-dim">· {stamp(e.ts)}</span>
                </div>
                {/* Chain chips, then the way out to Telegram. */}
                <div className="flex shrink-0 items-center gap-1">
                  {tokens.map((t, i) => (
                    <Badge key={`${t.chain}-${i}`} variant={CHAIN_TONE[t.chain || ""] || "gray"}>
                      {CHAIN_LABEL[t.chain || ""] || t.chain || "?"}
                    </Badge>
                  ))}
                  {e.post_url && (
                    <a href={e.post_url} target="_blank" rel="noopener noreferrer"
                       title="Open in Telegram"
                       className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-brand-soft">
                      <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              </header>

              {e.reply_to && (
                <p className="mt-1.5 flex items-start gap-1 text-[11px] text-text-dim">
                  <CornerUpLeft size={11} className="mt-0.5 shrink-0" />
                  <span className="min-w-0 break-words">
                    Replying to <span className="font-mono text-brand-soft">@{e.reply_to}</span>
                    {e.reply_text && (
                      // Clamped, not cut: the quoted message is context, and a
                      // caller's disclaimer can be longer than their call.
                      <span className="ml-1 line-clamp-2 break-all opacity-70">
                        — <Linkify text={e.reply_text} />
                      </span>
                    )}
                  </span>
                </p>
              )}

              {e.text && (
                <p className={`mt-1.5 whitespace-pre-wrap break-all text-xs leading-relaxed ${
                  box ? "opacity-90" : "text-text-muted"}`}>
                  <Linkify text={e.text} />
                </p>
              )}

              {e.media_id && (
                // Fetched with the session, not by the tag: the endpoint is
                // behind the login and an <img> cannot carry the header.
                <AuthImage
                  path={`/api/calls/media/${e.media_id}`}
                  zoomable
                  caption={`${e.group || ""} · ${stamp(e.ts)}`}
                  className="mt-2 max-h-64 w-auto rounded-lg border border-border object-contain"
                />
              )}

              {tokens.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-soft pt-2">
                  {tokens.map((t, i) => (
                    <span key={`t-${i}`} className="flex items-center gap-1">
                      {t.gmgn_url ? (
                        <a href={t.gmgn_url} target="_blank" rel="noopener noreferrer"
                           className="text-[11px] font-semibold text-text hover:text-brand-soft hover:underline">
                          {t.symbol || shortAddr(t.address || "")}
                        </a>
                      ) : (
                        <span className="text-[11px] font-semibold text-text">{t.symbol || "?"}</span>
                      )}
                      {t.address && (
                        <>
                          <span className="font-mono text-[10px] text-accent-blue">
                            {shortAddr(t.address)}
                          </span>
                          <CopyButton value={t.address} />
                        </>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
