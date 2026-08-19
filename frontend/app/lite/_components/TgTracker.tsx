"use client";

import { ExternalLink, CornerUpLeft, Users } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge, Variant } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { shortAddr } from "@/lib/utils";

/* The message behind the row.

   Only posts that carried a token appear here — a caller's chat is not the
   feed, their calls are. One entry per message, newest first, and the same
   token posted again is a new entry on purpose: what changed is the words
   around it, and those are the reason to read this at all. */

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
  tokens: Token[];
};

const CHAIN_LABEL: Record<string, string> = {
  eth: "ETH", rbh: "RBH", bnb: "BNB", sol: "SOL", base: "BASE",
};
const CHAIN_TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
};

function ago(ts?: number) {
  if (!ts) return "";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
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
  const items: Entry[] = data?.items ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-border bg-bg-card/60">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <h2 className="text-sm font-semibold text-text">TG Tracker</h2>
        <span className="text-[11px] text-text-dim">{items.length} messages</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {items.length === 0 ? (
          <p className="px-3 py-10 text-center text-xs text-text-dim">
            Nothing yet. Messages appear here the moment a caller posts a token.
          </p>
        ) : items.map((e) => (
          <article key={`${e.chat_id}-${e.msg_id}`}
                   className="border-b border-border-soft px-3 py-3 hover:bg-bg-hover/30">
            {/* Who, and where it came from */}
            <header className="flex items-start justify-between gap-2">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                <span className="truncate text-sm font-semibold text-text">{e.group || "Unknown"}</span>
                {e.username && (
                  <span className="font-mono text-[11px] text-text-dim">@{e.username}</span>
                )}
                {fmtFollowers(e.followers) && (
                  <span className="inline-flex items-center gap-0.5 text-[11px] text-text-dim">
                    <Users size={10} />{fmtFollowers(e.followers)}
                  </span>
                )}
                <span className="text-[11px] text-text-dim">· {ago(e.ts)}</span>
              </div>
              {/* Chain chips first, then the link out — the same order the
                  reference layout puts them in. */}
              <div className="flex shrink-0 items-center gap-1">
                {e.tokens.map((t, i) => (
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
                <span className="min-w-0">
                  Replying to <span className="font-mono text-brand-soft">@{e.reply_to}</span>
                  {e.reply_text && <span className="ml-1 truncate opacity-70">— {e.reply_text}</span>}
                </span>
              </p>
            )}

            {e.text && (
              <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-relaxed text-text-muted">
                {e.text}
              </p>
            )}

            {e.media_id && (
              // Content-addressed and cached hard, so the same graphic posted
              // by six groups is fetched once.
              <img
                src={`/api/calls/media/${e.media_id}`}
                alt=""
                loading="lazy"
                className="mt-2 max-h-64 w-auto rounded-lg border border-border object-contain"
              />
            )}

            {/* The tokens themselves, so a call can be acted on from here */}
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              {e.tokens.map((t, i) => (
                <span key={`t-${i}`} className="flex items-center gap-1">
                  {t.gmgn_url ? (
                    <a href={t.gmgn_url} target="_blank" rel="noopener noreferrer"
                       className="text-[11px] font-semibold text-text hover:text-brand-soft hover:underline">
                      {t.symbol || shortAddr(t.address || "")}
                    </a>
                  ) : (
                    <span className="text-[11px] font-semibold text-text">{t.symbol || "?"}</span>
                  )}
                  {t.address && <CopyButton value={t.address} />}
                </span>
              ))}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
