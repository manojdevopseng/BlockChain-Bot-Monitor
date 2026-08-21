"use client";

import { ExternalLink } from "lucide-react";
import { useApi } from "@/lib/api";
import { CallActions } from "./CallActions";
import { CopyButton } from "@/components/CopyButton";
import { ChipMap, GroupChip, chipStyleOf } from "@/components/GroupChip";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, Variant } from "@/components/ui/badge";
import { rowKey, shortAddr } from "@/lib/utils";

/* One row per token, with every caller on it — the same shape as the main
   dashboard's detections panel, which is where this table's readers already
   know how to look.

   The columns are this dashboard's own; only the Groups cell is borrowed. Each
   caller is a chip in the order it called, newest first, and each chip opens
   that caller's own message — so folding the calls into one row hides nothing,
   it just stops one token filling the screen. The token called most recently
   leads the table. */

export type Call = {
  chain?: string;
  symbol?: string;
  name?: string;
  address: string;
  group?: string;
  username?: string | null;
  gmgn_url?: string;
  ts?: number;
  // Set by /api/calls. One entry per caller, newest first; `count` is how many
  // callers and `calls` how many posts — a group that called the same token
  // three times is one chip, not three.
  group_entries?: GroupEntry[];
  count?: number;
  calls?: number;
  // Written onto the row the first time anybody presses MC on this token —
  // see CallActions.
  mcap?: number;
  mcap_at?: number;
  // What it was worth when it was called. Stamped once by the backend and
  // never re-read — see calls._stamp_mcap.
  mcap_call?: number;
};

type GroupEntry = {
  chat_id?: number;
  name?: string;
  username?: string;
  msg_id?: number;
  post_url?: string;
  ts?: number;
};

const CHAIN_LABEL: Record<string, string> = {
  eth: "Ethereum", rbh: "Robinhood", bnb: "BNB", sol: "Solana", base: "Base",
};

// One colour per chain, so a merged view reads at a glance rather than as five
// identical pills. Theme tokens, not hex — each already has a light and a dark
// value, so both themes stay legible without a second palette.
const CHAIN_TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
};

// Date above, clock below. Same split the rest of the dashboard uses: the day
// is what you scan for, the second is what you check once you have found it.
function When({ ts }: { ts?: number }) {
  if (!ts) return <span className="text-text-dim">—</span>;
  const d = new Date(ts * 1000);
  return (
    <span className="block font-mono text-xs leading-tight">
      <span className="block text-text-muted">{d.toLocaleDateString("en-GB")}</span>
      <span className="block text-text-dim">{d.toLocaleTimeString("en-GB")}</span>
    </span>
  );
}

// The message link. post_url is written when the call is recorded; the rebuild
// covers rows stored before it was, and a private group has neither.
function tgUrl(e: GroupEntry): string | null {
  if (e.post_url) return e.post_url;
  if (e.username && e.msg_id) return `https://t.me/${e.username}/${e.msg_id}`;
  return null;
}

// Rows from before the API grouped them still carry a single group on the row
// itself; read as a one-caller list so nothing renders blank.
function entriesOf(c: Call): GroupEntry[] {
  if (c.group_entries?.length) return c.group_entries;
  return c.group || c.username ? [{ name: c.group, username: c.username || "" }] : [];
}

export function CallsTable(
  { items, showChain = true, maxHeight, fill = false }:
  { items: Call[]; showChain?: boolean; maxHeight?: number | false; fill?: boolean },
) {
  // The same per-caller colours Forwarder → Premium Groups sets, and the same
  // request the tracker and the sound alert already make — SWR shares the key,
  // so this costs nothing on a page that has them.
  const { data: styleData } = useApi<any>("/api/forwarder/group-chips");
  const chips: ChipMap | undefined = styleData?.chips;

  return (
    <TableScroll maxHeight={maxHeight} fill={fill}>
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className={`${STICKY_HEAD} border-b border-border`}>
            {showChain && <th className="px-3 py-2.5 font-medium">Chain</th>}
            <th className="px-3 py-2.5 font-medium">Symbol</th>
            <th className="px-3 py-2.5 font-medium">Name</th>
            <th className="px-3 py-2.5 font-medium">Group</th>
            <th className="px-3 py-2.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={showChain ? 5 : 4} className="px-3 py-10 text-center text-text-dim">
                No calls yet
              </td>
            </tr>
          ) : items.map((c, i) => {
            const entries = entriesOf(c);
            const callers = c.count ?? entries.length;
            return (
            <tr key={rowKey(c, i)}
                className="border-b border-border-soft align-top hover:bg-bg-hover/40">
              {showChain && (
                <td className="px-3 py-3">
                  <Badge variant={CHAIN_TONE[c.chain || ""] || "gray"}>
                    {CHAIN_LABEL[c.chain || ""] || c.chain || "?"}
                  </Badge>
                </td>
              )}

              {/* Symbol opens GMGN; the address sits under it with its own copy,
                  so one cell carries both things you do with a token. */}
              <td className="px-3 py-3">
                <div className="flex items-center gap-1.5">
                  {c.gmgn_url ? (
                    <a href={c.gmgn_url} target="_blank" rel="noopener noreferrer"
                       title="Open on GMGN"
                       className="font-semibold text-text hover:text-brand-soft hover:underline">
                      {c.symbol || "?"}
                    </a>
                  ) : (
                    <span className="font-semibold text-text">{c.symbol || "?"}</span>
                  )}
                  {c.symbol && <CopyButton value={c.symbol} />}
                  {/* The detections panel says this in a Count column. There is
                      no room for one here and none was added, so the number
                      rides beside the symbol instead. */}
                  {callers > 1 && (
                    <span className="rounded bg-brand/15 px-1.5 py-0.5 text-[10px]
                                     font-medium text-brand-soft"
                          title={c.calls && c.calls > callers
                            ? `${callers} callers, ${c.calls} posts`
                            : `${callers} callers`}>
                      x{callers}
                    </span>
                  )}
                </div>
                <div className="mt-1 flex items-center gap-1.5">
                  <span className="font-mono text-[11px] text-accent-blue">{shortAddr(c.address)}</span>
                  <CopyButton value={c.address} />
                  {c.gmgn_url && (
                    <a href={c.gmgn_url} target="_blank" rel="noopener noreferrer"
                       title="Open on GMGN"
                       className="inline-grid h-4 w-4 place-items-center rounded text-text-dim hover:text-brand-soft">
                      <ExternalLink size={11} />
                    </a>
                  )}
                </div>
              </td>

              {/* Name, and under it the two things anybody does with a call:
                  take a position, or find out what it is worth. Under the name
                  rather than in columns of their own — the table is read on a
                  laptop beside Telegram, and two more columns is what pushes
                  When off the edge. */}
              <td className="px-3 py-3">
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">{c.name || "—"}</span>
                  {c.name && <CopyButton value={c.name} />}
                </div>
                <CallActions chain={c.chain} address={c.address}
                             symbol={c.symbol} name={c.name}
                             mcap={c.mcap} mcapAt={c.mcap_at}
                             mcapCall={c.mcap_call} />
              </td>

              {/* Every caller, each chip opening its own message. Width-capped
                  the way the detections panel caps it: the chips wrap, and a
                  token called by five groups with long names does not stretch
                  the column and push When off the screen. */}
              <td className="px-3 py-3">
                <div className="flex max-w-[280px] flex-wrap gap-1">
                  {entries.map((e, j) => {
                    // Newest-first, so the last one is who called it first.
                    const first = entries.length > 1 && j === entries.length - 1;
                    const url = tgUrl(e);
                    return (
                      <GroupChip
                        key={j}
                        label={`${first ? "🥇 " : ""}${e.name || `@${e.username}`}`}
                        url={url}
                        // Keyed by chat id, never by name: Telegram titles get
                        // re-read and overwritten, and a group can rename itself.
                        style={chipStyleOf(chips, e.chat_id)}
                        title={[
                          first ? "First caller" : null,
                          e.username ? `@${e.username}` : null,
                          url ? "Open this call on Telegram"
                              : "Private group — no message link",
                        ].filter(Boolean).join(" · ")}
                      />
                    );
                  })}
                  {entries.length === 0 && <span className="text-text-dim">—</span>}
                </div>
              </td>

              <td className="px-3 py-3"><When ts={c.ts} /></td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
