"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, shortAddr, timeAgo } from "@/lib/utils";

export type Detection = {
  symbol?: string;
  name?: string;
  address: string;
  groups?: string[];
  keyword?: string;
  count?: number;
  ts?: number;
  gmgn_url?: string;
};

export function DetectionTable(
  { items, maxHeight }: { items: Detection[]; maxHeight?: number | false },
) {
  return (
    <TableScroll maxHeight={maxHeight}>
      <table className="w-full min-w-[900px] text-sm">
        <thead>
          <tr className={`${STICKY_HEAD} border-b border-border`}>
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
            <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">No detections yet</td></tr>
          ) : (
            items.map((d, i) => (
              <tr key={i} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
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
                {/* Groups */}
                <td className="px-3 py-3">
                  <div className="flex flex-wrap gap-1">
                    {(d.groups || []).map((g, j) => (
                      <Badge key={j} variant="gray">{g}</Badge>
                    ))}
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
