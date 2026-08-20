"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, Variant } from "@/components/ui/badge";
import { rowKey, shortAddr } from "@/lib/utils";

/* One row per call. Not per token — the same token called by four groups is
   four rows, and called twice by one group is two, because reading the
   sequence is the point of this table. */

export type Call = {
  chain?: string;
  symbol?: string;
  name?: string;
  address: string;
  group?: string;
  username?: string | null;
  gmgn_url?: string;
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
          ) : items.map((c, i) => (
            <tr key={rowKey(c, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
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
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
