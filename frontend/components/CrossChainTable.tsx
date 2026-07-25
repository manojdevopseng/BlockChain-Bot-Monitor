"use client";

import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, fmtEth, fmtUsd, shortAddr, timeAgo } from "@/lib/utils";

export type CrossChainMatch = {
  token_symbol?: string;
  token_address?: string;
  sol_address?: string;
  sol_mcap_usd?: number;
  dex?: string;
  fee_eth?: number | null;
  created_at?: number;
  gmgn_url?: string;
  sol_gmgn_url?: string;
};

// SOL→ETH / SOL→RBH ticker matches: the SOL side and the destination-chain side
// side by side, both copyable and linked to GMGN.
export function CrossChainTable({ items, showFee }: { items: CrossChainMatch[]; showFee?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-dim">
            <th className="px-3 py-2.5 font-medium">Symbol</th>
            <th className="px-3 py-2.5 font-medium">SOL Address</th>
            <th className="px-3 py-2.5 font-medium">Matched Address</th>
            <th className="px-3 py-2.5 font-medium">DEX</th>
            <th className="px-3 py-2.5 font-medium">SOL MCap</th>
            {showFee && <th className="px-3 py-2.5 font-medium">Gas Fee</th>}
            <th className="px-3 py-2.5 font-medium">Timestamp</th>
            <th className="px-3 py-2.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={showFee ? 8 : 7} className="px-3 py-10 text-center text-text-dim">
                No cross-chain matches yet
              </td>
            </tr>
          ) : (
            items.map((r, i) => (
              <tr key={i} className="border-b border-border-soft hover:bg-bg-hover/40">
                <td className="px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-text">{r.token_symbol || "?"}</span>
                    {r.token_symbol && <CopyButton value={r.token_symbol} />}
                  </div>
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
                <td className="px-3 py-3"><Badge variant="purple">{r.dex || "—"}</Badge></td>
                <td className="px-3 py-3 text-text-muted">{fmtUsd(r.sol_mcap_usd)}</td>
                {showFee && (
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-accent-cyan">
                      {r.fee_eth ? fmtEth(r.fee_eth) : "—"}
                    </span>
                  </td>
                )}
                <td className="px-3 py-3">
                  <span className="font-mono text-xs text-text-muted">
                    {r.created_at ? fmtDateTime(r.created_at) : "—"}
                  </span>
                </td>
                <td className="px-3 py-3">
                  <span className="text-text-muted">{r.created_at ? timeAgo(r.created_at) : "—"}</span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
