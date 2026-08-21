"use client";

import { useState } from "react";
import { Loader2, RefreshCw, Wallet } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge, Variant } from "@/components/ui/badge";
import { fmtUsd } from "@/lib/utils";

/* What the account's own wallet holds, above the positions it is pretending
   to hold.
 *
 * Watch-only: an address is saved on Profile and read here. Nothing on this
 * strip can move anything, and it says so — the wallet sitting next to a
 * paper P&L is exactly where somebody might assume otherwise.
 *
 * Asked for rather than polled. A balance changes when the person themselves
 * moves funds, so a timer would spend five RPC calls a minute per open tab to
 * re-learn the same number. It loads once and there is a button.
 *
 * Each chain carries its own bad news. An RPC that is rate-limited shows as
 * grey with a reason, never as a zero — a wallet reported empty when the
 * endpoint simply could not be reached is a lie the reader cannot catch. */

const TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
  tron: "red",
};

function amount(n: number): string {
  // Enough digits to see dust, few enough to read. A balance of 0.00004 ETH
  // rounded to two places reads as nothing at all.
  if (n === 0) return "0";
  if (n < 0.0001) return n.toExponential(2);
  if (n < 1) return n.toFixed(6);
  if (n < 1000) return n.toFixed(4);
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function WalletStrip() {
  // No polling, and no refetch when the tab regains focus: the default
  // twelve-second interval would be five RPC calls a minute per open tab
  // for a number that only changes when the person moves funds.
  const { data, isLoading, mutate } = useApi<any>(
    "/api/trading/wallet", { refreshInterval: 0, revalidateOnFocus: false });
  const [busy, setBusy] = useState(false);

  const rows: any[] = data?.chains ?? [];
  // The count, not the arrays — an empty array is truthy, so testing the
  // lists directly would show the strip as populated with nothing in it.
  const anyAddress = (data?.linked ?? 0) > 0;

  async function refresh() {
    setBusy(true);
    try { await mutate(); } finally { setBusy(false); }
  }

  return (
    <div className="rounded-xl border border-border bg-bg-card p-3">
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <Wallet size={14} className="text-text-dim" />
        <span className="text-sm font-medium text-text">Wallet</span>
        <Badge variant="gray">watch-only</Badge>

        {anyAddress && (
          <span className="ml-auto flex items-center gap-3">
            <span className="text-xs text-text-dim">
              Total{" "}
              <span className="font-medium tabular-nums text-text">
                {fmtUsd(data?.total_usd ?? 0)}
              </span>
            </span>
            <button onClick={refresh} disabled={busy || isLoading}
                    title="Read the balances again"
                    className="grid h-6 w-6 place-items-center rounded text-text-dim
                               transition-colors hover:text-brand-soft disabled:opacity-40">
              {busy || isLoading ? <Loader2 size={13} className="animate-spin" />
                                 : <RefreshCw size={13} />}
            </button>
          </span>
        )}
      </div>

      {!anyAddress && !isLoading ? (
        <p className="text-xs leading-relaxed text-text-dim">
          No wallet connected. Connect MetaMask or Phantom on{" "}
          <a href="/profile" className="text-brand-soft hover:underline">Profile</a>,
          or paste an address to watch — an address only, never a key. One EVM
          wallet covers Robinhood, Ethereum, BNB and Base; Solana takes its own.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {rows.map((r) => (
            <div key={r.chain}
                 className="rounded-lg border border-border-soft bg-bg-soft/40 px-2.5 py-2">
              <div className="mb-1 flex items-center gap-1.5">
                <Badge variant={TONE[r.chain] ?? "gray"}>{r.label}</Badge>
              </div>
              {r.why ? (
                // The reason, not a zero.
                <p className="text-[10px] leading-snug text-text-dim">{r.why}</p>
              ) : (
                <>
                  <div className="text-sm font-medium tabular-nums text-text">
                    {amount(r.balance ?? 0)}{" "}
                    <span className="text-[11px] font-normal text-text-dim">{r.symbol}</span>
                  </div>
                  <div className="text-[11px] tabular-nums text-text-muted">
                    {r.usd != null ? fmtUsd(r.usd) : "—"}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
