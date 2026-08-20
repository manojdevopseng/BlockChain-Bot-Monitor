"use client";

import { Users } from "lucide-react";
import { useApi } from "@/lib/api";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { fmtUsd } from "@/lib/utils";

/* Which caller is paying for itself and which one is eating the account.
 *
 * The whole point of following anybody automatically is that the answer stops
 * being a feeling. Realised and unrealised are kept in separate columns on
 * purpose: a caller carried by one position nobody has closed yet has proved
 * nothing, and a table that adds those together would hide it. */

function Signed({ usd, pct }: { usd: number; pct?: number }) {
  const up = usd >= 0;
  return (
    <span className={up ? "text-accent-green" : "text-accent-red"}>
      <span className="font-medium tabular-nums">
        {up ? "+" : "−"}{fmtUsd(Math.abs(usd))}
      </span>
      {pct != null && (
        <span className="ml-1 text-[11px] tabular-nums opacity-80">
          ({up ? "+" : "−"}{Math.abs(pct).toFixed(1)}%)
        </span>
      )}
    </span>
  );
}

export function CallerPnl() {
  const { data } = useApi<any>("/api/trading/callers");
  const items: any[] = data?.items ?? [];

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-bg-card/60">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <Users size={14} className="text-text-dim" />
        <h2 className="text-sm font-semibold text-text">P&amp;L by caller</h2>
        <span className="text-[11px] text-text-dim">
          every position this account took, grouped by who called it
        </span>
      </div>

      <TableScroll maxHeight={420}>
        <table className="w-full min-w-[780px] text-sm">
          <thead>
            <tr className={`${STICKY_HEAD} border-b border-border`}>
              <th className="px-3 py-2.5 text-left font-medium">Caller</th>
              <th className="px-3 py-2.5 font-medium">Trades</th>
              <th className="px-3 py-2.5 font-medium">Spent</th>
              <th className="px-3 py-2.5 font-medium">Realised</th>
              <th className="px-3 py-2.5 font-medium">Unrealised</th>
              <th className="px-3 py-2.5 font-medium">Total</th>
              <th className="px-3 py-2.5 font-medium">Won</th>
              <th className="px-3 py-2.5 font-medium">Best / worst</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">
                Nothing to compare yet. Once positions land here, this says which
                caller is worth following and which one is not.
              </td></tr>
            ) : items.map((c) => (
              <tr key={c.caller} className="border-b border-border-soft hover:bg-bg-hover/40">
                <td className="max-w-[22ch] truncate px-3 py-2.5 font-medium text-text"
                    title={c.caller}>{c.caller}</td>
                <td className="px-3 py-2.5 text-center tabular-nums text-text-muted">
                  {c.trades}
                  <span className="ml-1 text-[10px] text-text-dim">
                    ({c.open} open)
                  </span>
                </td>
                <td className="px-3 py-2.5 text-center tabular-nums text-text-muted">
                  {fmtUsd(c.cost)}
                </td>
                <td className="px-3 py-2.5 text-center"><Signed usd={c.realised} /></td>
                <td className="px-3 py-2.5 text-center">
                  {c.open ? <Signed usd={c.unrealised} />
                          : <span className="text-text-dim">—</span>}
                </td>
                <td className="px-3 py-2.5 text-center"><Signed usd={c.pnl} pct={c.pct} /></td>
                <td className="px-3 py-2.5 text-center tabular-nums text-text-muted">
                  {c.win_rate == null ? <span className="text-text-dim">—</span>
                                      : `${c.win_rate}%`}
                </td>
                <td className="px-3 py-2.5 text-center text-[11px] tabular-nums">
                  <span className="text-accent-green">
                    {c.best == null ? "—" : `${c.best > 0 ? "+" : ""}${c.best}%`}
                  </span>
                  <span className="mx-1 text-text-dim">/</span>
                  <span className="text-accent-red">
                    {c.worst == null ? "—" : `${c.worst}%`}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}
