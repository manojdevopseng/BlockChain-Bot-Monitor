"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { ChipMap, GroupChip, chipStyleOf } from "@/components/GroupChip";
import { fmtDateTime, shortAddr, rowKey } from "@/lib/utils";
import { Age } from "@/components/Age";

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
  { items, maxHeight, showChain = false, chips }: {
    items: Detection[]; maxHeight?: number | false; showChain?: boolean;
    // Per-group chip colours, set in Forwarder → Premium Groups. Absent for a
    // group nobody has styled, which is the default look.
    chips?: ChipMap;
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
            <th className="px-3 py-2.5 font-medium">Age</th>
            <th className="px-3 py-2.5 font-medium">Address</th>
            <th className="px-3 py-2.5 font-medium">Groups</th>
            <th className="px-3 py-2.5 font-medium">Keyword</th>
            <th className="px-3 py-2.5 font-medium">Count</th>
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
                {/* Age — ticks every second, same component as the AI page */}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted"><Age ts={d.ts} /></span>
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
                      const url = e.username && e.message_id
                        ? `https://t.me/${e.username}/${e.message_id}`
                        : null;
                      return (
                        <GroupChip
                          key={j}
                          label={`${first ? "🥇 " : ""}${e.name || "?"}`}
                          url={url}
                          // Keyed by chat id, never by name: Telegram titles are
                          // re-read and overwritten, and a group can rename
                          // itself — the id is the only stable handle.
                          style={chipStyleOf(chips, e.chat_id)}
                          title={url
                            ? (first ? "First caller — open this call on Telegram"
                                     : "Open this call on Telegram")
                            : (first ? `First caller — ${e.name || "?"}`
                                     : `${e.name || "?"} — private group, no message link`)}
                        />
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
                {/* When — the absolute time it was recorded */}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted">{d.ts ? fmtDateTime(d.ts) : "—"}</span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </TableScroll>
  );
}
