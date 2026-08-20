"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
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

export function CallsTable(
  { items, showChain = true, maxHeight, fill = false }:
  { items: Call[]; showChain?: boolean; maxHeight?: number | false; fill?: boolean },
) {
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

              {/* One group, because one row is one call. */}
              <td className="px-3 py-3">
                <span className="text-text">{c.group || "—"}</span>
                {c.username && (
                  <span className="mt-0.5 block font-mono text-[11px] text-text-dim">@{c.username}</span>
                )}
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
