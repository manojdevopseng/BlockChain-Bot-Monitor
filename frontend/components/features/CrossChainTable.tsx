"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, DEX_TONE, Variant } from "@/components/ui/badge";
import { fmtDateTime, fmtUsd, shortAddr, rowKey } from "@/lib/utils";
import { Age } from "@/components/Age";

export type CrossChainMatch = {
  token_symbol?: string;
  token_address?: string;
  sol_address?: string;
  sol_mcap_usd?: number;
  dex?: string;
  created_at?: number;
  gmgn_url?: string;
  sol_gmgn_url?: string;
  chain?: string;
};

// Shown only in the merged "All" view. SOL is always the source, so what
// varies between rows is the destination chain — and merged, nothing else on
// the row says which one it was.
const FLOW_LABEL: Record<string, string> = {
  eth: "SOL → ETH", robinhood: "SOL → RBH", rbh: "SOL → RBH",
};

// Destination chain gets the colour it has everywhere else — the Detections
// Chain column uses the same two, so ETH is blue in both places.
const FLOW_TONE: Record<string, Variant> = {
  eth: "blue", robinhood: "green", rbh: "green",
};

// SOL→ETH / SOL→RBH ticker matches: the SOL side and the destination-chain side
// side by side, both copyable and linked to GMGN.
//
// No gas-fee column: a cross-chain match is a ticker match, and nothing in that
// path reads a receipt, so the value was always "—". High-gas buys are their own
// feature with their own section.
export function CrossChainTable(
  { items, maxHeight, showFlow = false }: {
    items: CrossChainMatch[]; maxHeight?: number | false; showFlow?: boolean;
  },
) {
  return (
    <TableScroll maxHeight={maxHeight}>
      <table className="w-full min-w-[880px] text-sm">
        <thead>
          <tr className={`${STICKY_HEAD} border-b border-border`}>
            {showFlow && <th className="px-3 py-2.5 font-medium">Flow</th>}
            <th className="px-3 py-2.5 font-medium">Symbol</th>
            <th className="px-3 py-2.5 font-medium">Age</th>
            <th className="px-3 py-2.5 font-medium">SOL Address</th>
            <th className="px-3 py-2.5 font-medium">Matched Address</th>
            <th className="px-3 py-2.5 font-medium">DEX</th>
            <th className="px-3 py-2.5 font-medium">SOL MCap</th>
            <th className="px-3 py-2.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={showFlow ? 8 : 7} className="px-3 py-10 text-center text-text-dim">
                No cross-chain matches yet
              </td>
            </tr>
          ) : (
            items.map((r, i) => (
              <tr key={rowKey(r, i)} className="border-b border-border-soft hover:bg-bg-hover/40">
                {showFlow && (
                  <td className="px-3 py-3">
                    <Badge variant={FLOW_TONE[r.chain || ""] || "gray"}>
                      {FLOW_LABEL[r.chain || ""] || r.chain || "?"}
                    </Badge>
                  </td>
                )}
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-text">{r.token_symbol || "?"}</span>
                    {r.token_symbol && <CopyButton value={r.token_symbol} />}
                  </div>
                </td>
                {/* Age — ticks every second */}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted"><Age ts={r.created_at} /></span>
                </td>
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-xs text-accent-purple">{shortAddr(r.sol_address)}</span>
                    {r.sol_address && <CopyButton value={r.sol_address} />}
                    {r.sol_gmgn_url && (
                      <a href={r.sol_gmgn_url} target="_blank" rel="noopener noreferrer" title="SOL token on GMGN"
                         className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-xs text-accent-blue">{shortAddr(r.token_address)}</span>
                    {r.token_address && <CopyButton value={r.token_address} />}
                    {r.gmgn_url && (
                      <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
                         className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <Badge variant={DEX_TONE[(r.dex || "").toLowerCase()] || "gray"}>{r.dex || "—"}</Badge>
                </td>
                <td className="px-3 py-3 text-text-muted">{fmtUsd(r.sol_mcap_usd)}</td>
                {/* When — the absolute time the match fired */}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted">
                    {r.created_at ? fmtDateTime(r.created_at) : "—"}
                  </span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </TableScroll>
  );
}
