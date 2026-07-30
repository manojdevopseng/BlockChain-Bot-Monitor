"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, shortAddr, timeAgo, rowKey } from "@/lib/utils";

export type GroupEntry = {
  chat_id?: number;
  name?: string;
  username?: string | null;
  message_id?: number | null;
};

export type Detection = {
  symbol?: string;
  name?: string;
  address: string;
  groups?: string[];
  group_entries?: GroupEntry[];
  keyword?: string;
  count?: number;
  ts?: number;
  gmgn_url?: string;
  chain?: string;
};

// Shown only in the merged "All" view. With one section per chain the column
// was pure repetition of the section title; merged, it is the only thing that
// says which chain a row came from.
const CHAIN_LABEL: Record<string, string> = {
  eth: "Ethereum", rbh: "Robinhood", bnb: "BNB", sol: "Solana",
};

// Records written before group_entries existed only carry plain names; show
// those as non-clickable chips rather than dropping them.
function groupEntries(d: Detection): GroupEntry[] {
  if (d.group_entries?.length) return d.group_entries;
  return (d.groups || []).map((name) => ({ name }));
}

export function DetectionTable(
  { items, maxHeight, showChain = false }: {
    items: Detection[]; maxHeight?: number | false; showChain?: boolean;
  },
) {
  return (
    <TableScroll maxHeight={maxHeight}>
      <table className="w-full min-w-[900px] text-sm">
        <thead>
          <tr className={`${STICKY_HEAD} border-b border-border`}>
            {showChain && <th className="px-3 py-2.5 font-medium">Chain</th>}
            <th className="px-3 py-2.5 font-medium">Symbol</th>
            <th className="px-3 py-2.5 font-medium">Name</th>
            <th className="px-3 py-2.5 font-medium">Address</th>
            <th className="px-3 py-2.5 font-medium">Groups</th>
            <th className="px-3 py-2.5 font-medium">Keyword</th>
            <th className="px-3 py-2.5 font-medium">Count</th>
            <th className="px-3 py-2.5 font-medium">Timestamp</th>
            <th className="px-3 py-2.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr><td colSpan={showChain ? 9 : 8} className="px-3 py-10 text-center text-text-dim">No detections yet</td></tr>
          ) : (
            items.map((d, i) => (
              <tr key={rowKey(d, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                {showChain && (
                  <td className="px-3 py-3">
                    <Badge variant="purple">{CHAIN_LABEL[d.chain || ""] || d.chain || "?"}</Badge>
                  </td>
                )}
                {/* Symbol */}
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-text">{d.symbol || "?"}</span>
                    {d.symbol && <CopyButton value={d.symbol} />}
                  </div>
                </td>
                {/* Name */}
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="text-text-muted">{d.name || "—"}</span>
                    {d.name && <CopyButton value={d.name} />}
                  </div>
                </td>
                {/* Address + copy + GMGN link */}
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-xs text-accent-blue">{shortAddr(d.address)}</span>
                    <CopyButton value={d.address} />
                    {d.gmgn_url && (
                      <a href={d.gmgn_url} target="_blank" rel="noopener noreferrer"
                         title="View on GMGN"
                         className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-brand-soft">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </td>
                {/* Groups — each chip opens that group's actual message.
                    Bounded: the chips wrap, but nothing capped how wide the
                    cell could get, so a token called by several groups with
                    long names stretched this column and pushed everything
                    after it off to the right. */}
                <td className="px-3 py-3">
                  <div className="flex max-w-[260px] flex-wrap gap-1">
                    {groupEntries(d).map((e, j, arr) => {
                      // Entries are newest-first, so the last one is the group
                      // that called it first.
                      const first = arr.length > 1 && j === arr.length - 1;
                      const label = `${first ? "🥇 " : ""}${e.name || "?"}`;
                      const url = e.username && e.message_id
                        ? `https://t.me/${e.username}/${e.message_id}`
                        : null;
                      if (!url) {
                        return (
                          <Badge key={j} variant="gray"
                            title={first ? `First caller — ${e.name || "?"}`
                                         : `${e.name || "?"} — private group, no message link`}>
                            <span className="block max-w-[130px] truncate">{label}</span>
                          </Badge>
                        );
                      }
                      return (
                        <a key={j} href={url} target="_blank" rel="noopener noreferrer"
                          title={first ? "First caller — open this call on Telegram"
                                       : "Open this call on Telegram"}
                          className="inline-flex max-w-[130px] items-center gap-1 truncate rounded-md border border-border bg-white/5 px-2 py-0.5 text-[11px] font-medium text-text-muted transition-colors hover:border-brand/40 hover:text-brand-soft">
                          {label}
                        </a>
                      );
                    })}
                    {groupEntries(d).length === 0 && <span className="text-text-dim">—</span>}
                  </div>
                </td>
                {/* Keyword */}
                <td className="px-3 py-3">
                  {d.keyword
                    ? <Badge variant="green">{d.keyword} Matched</Badge>
                    : <Badge variant="gray">Not Matched</Badge>}
                </td>
                {/* Count */}
                <td className="px-3 py-3">
                  <span className="font-bold text-accent-amber">{d.count ?? 1}</span>
                </td>
                {/* Timestamp */}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted">{d.ts ? fmtDateTime(d.ts) : "—"}</span>
                </td>
                {/* When */}
                <td className="px-3 py-3">
                  <span className="text-text-muted">{d.ts ? timeAgo(d.ts) : "—"}</span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </TableScroll>
  );
}
