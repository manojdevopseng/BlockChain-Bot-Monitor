"use client";

import { ExternalLink } from "lucide-react";
import { useApi } from "@/lib/api";
import { CopyButton } from "@/components/CopyButton";
import { ChipMap, GroupChip, chipStyleOf } from "@/components/GroupChip";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, Variant } from "@/components/ui/badge";
import { rowKey, shortAddr } from "@/lib/utils";

/* One row per call. Not per token — the same token called by four groups is
   four rows, and called twice by one group is two, because reading the
   sequence is the point of this table.

   Those rows are grouped, though. A token's calls sit together with its newest
   caller at the top, and the token called most recently leads the table — the
   same ordering the main dashboard's detections panel has, which lifts a token
   back to the top every time somebody new calls it. The server does the
   grouping and marks each row with its rank; here that is only a left bar down
   the block and a count on the row that leads it. */

export type Call = {
  chain?: string;
  symbol?: string;
  name?: string;
  address: string;
  group?: string;
  username?: string | null;
  gmgn_url?: string;
  ts?: number;
  // Set by /api/calls: 0 is this token's newest call, and the total is how many
  // of its calls are in the window being shown.
  call_rank?: number;
  call_total?: number;
  // What the group chip opens, and the id its colour is keyed on.
  post_url?: string;
  chat_id?: number;
  msg_id?: number;
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
function tgUrl(c: Call): string | null {
  if (c.post_url) return c.post_url;
  if (c.username && c.msg_id) return `https://t.me/${c.username}/${c.msg_id}`;
  return null;
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
            const rank = c.call_rank ?? 0;
            const total = c.call_total ?? 1;
            // A single call is not a group and must not be dressed as one.
            const grouped = total > 1;
            const lead = grouped ? "border-l-2 border-l-brand/40" : "";
            // The block is newest-first, so its last row is who called it
            // first — the same 🥇 the detections panel puts on that group.
            const first = grouped && rank === total - 1;
            return (
            <tr key={rowKey(c, i)}
                className={`align-top hover:bg-bg-hover/40 ${
                  // The bottom border closes the block, not every row in it, so
                  // one token's calls read as one thing.
                  grouped && rank < total - 1 ? "" : "border-b border-border-soft"
                } ${grouped && rank > 0 ? "bg-bg-soft/25" : ""}`}>
              {showChain && (
                <td className={`px-3 py-3 ${lead}`}>
                  <Badge variant={CHAIN_TONE[c.chain || ""] || "gray"}>
                    {CHAIN_LABEL[c.chain || ""] || c.chain || "?"}
                  </Badge>
                </td>
              )}

              {/* Symbol opens GMGN; the address sits under it with its own copy,
                  so one cell carries both things you do with a token. */}
              <td className={`px-3 py-3 ${showChain ? "" : lead}`}>
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
                  {grouped && rank === 0 && (
                    <span className="rounded bg-brand/15 px-1.5 py-0.5 text-[10px]
                                     font-medium text-brand-soft"
                          title={`${total} calls on this token`}>
                      {total} calls
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

              <td className="px-3 py-3">
                <div className="flex items-center gap-1.5">
                  <span className="text-text-muted">{c.name || "—"}</span>
                  {c.name && <CopyButton value={c.name} />}
                </div>
              </td>

              {/* One chip, because one row is one call — and it opens that
                  call on Telegram, the way the detections panel's chips do.
                  The handle is in the tooltip rather than printed under the
                  chip: a coloured chip with a mono @name beneath it was two
                  labels for one thing. */}
              <td className="px-3 py-3">
                {c.group || c.username ? (
                  <GroupChip
                    label={`${first ? "🥇 " : ""}${c.group || `@${c.username}`}`}
                    url={tgUrl(c)}
                    // Keyed by chat id, never by name: Telegram titles get
                    // re-read and overwritten, and a group can rename itself.
                    style={chipStyleOf(chips, c.chat_id)}
                    title={[
                      first ? "First caller" : null,
                      c.username ? `@${c.username}` : null,
                      tgUrl(c) ? "Open this call on Telegram"
                               : "Private group — no message link",
                    ].filter(Boolean).join(" · ")}
                  />
                ) : <span className="text-text-dim">—</span>}
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
