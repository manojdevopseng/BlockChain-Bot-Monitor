"use client";

import { useState } from "react";
import { mutate } from "swr";
import {
  ExternalLink, Loader2, RefreshCw, Shield, Wallet,
} from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Badge, Variant } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { FilterTabs } from "@/components/SectionFilters";
import { fmtUsd } from "@/lib/utils";

/* The portfolio: what this account owns, and what it has done.
 *
 * Two different kinds of truth, deliberately kept apart rather than blended
 * into one number. Holdings are read from the chain — whatever is in the
 * wallet, however it got there. History is this account's own trades, and
 * only those: a swap made somewhere else is invisible here, and inventing a
 * record of it would be worse than the gap.
 *
 * Realised and unrealised are separated for the same reason. One is money
 * that has been banked; the other is a number that can still go to zero, and
 * adding them together produces a figure that flatters on the way up and
 * lies on the way down. */

const CHAIN_TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
  tron: "red",
};
const CHAIN_LABEL: Record<string, string> = {
  eth: "Ethereum", rbh: "Robinhood", bnb: "BNB", sol: "Solana",
  base: "Base", tron: "Tron",
};
const EXPLORER: Record<string, string> = {
  eth: "https://etherscan.io/tx/",
  rbh: "https://robinhoodchain.blockscout.com/tx/",
  bnb: "https://bscscan.com/tx/",
  base: "https://basescan.org/tx/",
};
const KIND_SHORT: Record<string, string> = {
  evm: "EVM", sol: "Solana", tron: "Tron",
};

const TABS = [
  { id: "all", label: "All" },
  { id: "holdings", label: "Holdings" },
  { id: "history", label: "History" },
  { id: "failed", label: "Failed" },
] as const;

/* The three lists are three shapes, and All has to show them in one table.
   What they share is a token, a chain, a moment and an outcome — so that is
   what the merged view is built from, with the outcome named in a column
   rather than implied by which tab you happened to be on.

   Sorted by when each thing last happened, not by when it started: a
   position closed an hour ago is more recent news than one opened yesterday
   and still running. */
type Row = { kind: "open" | "closed" | "failed"; at: number; p?: any; f?: any };

function merge(holdings: any[], history: any[], failures: any[]): Row[] {
  const rows: Row[] = [
    ...holdings.map((p) => ({ kind: "open" as const, at: p.opened_at || 0, p })),
    ...history.map((p) => ({ kind: "closed" as const, at: p.closed_at || p.opened_at || 0, p })),
    ...failures.map((f) => ({ kind: "failed" as const, at: f.at || 0, f })),
  ];
  return rows.sort((a, b) => b.at - a.at);
}

const STATUS: Record<string, { label: string; tone: Variant }> = {
  open:   { label: "open",   tone: "blue" },
  closed: { label: "closed", tone: "gray" },
  failed: { label: "failed", tone: "red" },
};

function amount(n: number): string {
  if (!n) return "0";
  if (n < 0.0001) return n.toExponential(2);
  if (n < 1) return n.toFixed(6);
  if (n < 1000) return n.toFixed(4);
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function Money({ usd, pct }: { usd: number; pct?: number }) {
  const up = usd >= 0;
  return (
    <span className={up ? "text-accent-green" : "text-accent-red"}>
      <span className="font-medium tabular-nums">
        {up ? "+" : "−"}{fmtUsd(Math.abs(usd))}
      </span>
      {pct !== undefined && (
        <span className="ml-1 text-[11px] tabular-nums opacity-80">
          ({up ? "+" : "−"}{Math.abs(pct).toFixed(1)}%)
        </span>
      )}
    </span>
  );
}

/* One headline number with its name under it. The layout GMGN uses, because
   the thing being compared is always four or five figures at once and a grid
   reads faster than a paragraph. */
function Stat({ label, children, hint }: {
  label: string; children: React.ReactNode; hint?: string;
}) {
  return (
    <div>
      <p className="text-[11px] text-text-dim">{label}</p>
      <p className="mt-0.5 text-sm font-medium tabular-nums text-text">{children}</p>
      {hint && <p className="text-[10px] text-text-dim">{hint}</p>}
    </div>
  );
}

export default function PortfolioPage() {
  const { data, isLoading } = useApi<any>("/api/trading/portfolio",
    { refreshInterval: 0 });
  const [tab, setTab] = useState<"all" | "holdings" | "history" | "failed">("all");
  const [busy, setBusy] = useState(false);

  const s = data?.summary ?? {};
  const wallets: any[] = data?.wallets ?? [];
  const chains: any[] = data?.balances?.chains ?? [];
  const rows: any[] = (tab === "holdings" ? data?.holdings
                       : tab === "history" ? data?.history : []) ?? [];
  const failures: any[] = data?.failures ?? [];
  const all = merge(data?.holdings ?? [], data?.history ?? [], failures);

  async function reload() {
    setBusy(true);
    try {
      await mutate((k) => typeof k === "string" && k.startsWith("/api/trading"));
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Portfolio"
                  subtitle="What this account holds, and every trade it has made" />

      {/* The wallets, and what is in them. Balance first because it is the
          number somebody opens this page to see. */}
      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <div className="rounded-xl border border-border bg-bg-card p-4">
          <div className="mb-3 flex items-center gap-2">
            <Wallet size={14} className="text-text-dim" />
            <span className="text-sm font-medium text-text">
              Wallets ({wallets.length})
            </span>
            {data?.live_trading
              ? <Badge variant="green">live</Badge>
              : <Badge variant="gray">paper</Badge>}
            <button onClick={reload} disabled={busy}
                    title="Read the balances again"
                    className="ml-auto grid h-6 w-6 place-items-center rounded
                               text-text-dim hover:text-brand-soft disabled:opacity-40">
              {busy ? <Loader2 size={13} className="animate-spin" />
                    : <RefreshCw size={13} />}
            </button>
          </div>

          {wallets.length === 0 ? (
            <p className="text-xs leading-relaxed text-text-dim">
              No trading wallet yet. Create one on{" "}
              <a href="/profile" className="text-brand-soft hover:underline">Profile</a>{" "}
              and send it what you want to trade with.
            </p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {wallets.map((w) => (
                <div key={w.kind}
                     className="flex items-center gap-2 rounded-lg border
                                border-border-soft bg-bg-soft/40 px-2.5 py-1.5">
                  <Badge variant={CHAIN_TONE[w.kind === "evm" ? "eth" : w.kind] ?? "gray"}>
                    {KIND_SHORT[w.kind] ?? w.kind}
                  </Badge>
                  <span className="truncate font-mono text-[11px] text-text-muted">
                    {w.address}
                  </span>
                  <CopyButton value={w.address} />
                </div>
              ))}
            </div>
          )}

          <p className="mt-3 text-[11px] text-text-dim">Total balance</p>
          <p className="text-2xl font-semibold tabular-nums text-text">
            {fmtUsd(data?.balances?.total_usd ?? 0)}
          </p>

          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {chains.map((c) => (
              <div key={c.chain}
                   className="rounded-lg border border-border-soft bg-bg-soft/40 px-2 py-1.5">
                <Badge variant={CHAIN_TONE[c.chain] ?? "gray"}>{c.label}</Badge>
                {c.why ? (
                  <p className="mt-1 text-[10px] leading-snug text-text-dim">{c.why}</p>
                ) : (
                  <>
                    <p className="mt-1 text-xs font-medium tabular-nums text-text">
                      {amount(c.balance ?? 0)} {c.symbol}
                    </p>
                    <p className="text-[10px] tabular-nums text-text-muted">
                      {c.usd != null ? fmtUsd(c.usd) : "—"}
                    </p>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* The score. Realised and unrealised never added together — one is
            banked and the other can still go to zero. */}
        <div className="rounded-xl border border-border bg-bg-card p-4">
          <p className="text-[11px] text-text-dim">Total P&amp;L</p>
          <p className="text-2xl font-semibold">
            <Money usd={s.total_pnl_usd ?? 0} />
          </p>

          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Stat label="Realised" hint="banked, cannot change">
              <Money usd={s.realised_usd ?? 0} />
            </Stat>
            <Stat label="Unrealised" hint="still open">
              <Money usd={s.unrealised_usd ?? 0} />
            </Stat>
            <Stat label="Today">
              <Money usd={data?.day?.pnl ?? 0} pct={data?.day?.pct ?? 0} />
            </Stat>
            <Stat label="Total volume">{fmtUsd(s.volume_usd ?? 0)}</Stat>
            <Stat label="Win rate"
                  hint={`${s.wins ?? 0} of ${s.closed ?? 0} closed`}>
              {(s.win_rate ?? 0).toFixed(0)}%
            </Stat>
            <Stat label="Positions"
                  hint={`${s.live_trades ?? 0} on chain`}>
              {s.open ?? 0} open
            </Stat>
          </div>
        </div>
      </div>

      {/* Holdings and history, which mean different things and say so. */}
      <div className="rounded-xl border border-border bg-bg-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
          <FilterTabs value={tab} options={TABS}
                      onChange={(v) => setTab(v)} />
          <p className="text-[11px] text-text-dim">
            {tab === "all"
              ? "Everything this account has done, newest first — open, closed and refused together."
              : tab === "holdings"
              ? "Open positions this account opened. What the wallet holds is above."
              : tab === "history"
              ? "Every trade made through SightLine. Swaps made elsewhere are not here — this cannot see them."
              : "Trades that were attempted and did not go through, and what stopped each one."}
          </p>
        </div>

        {tab === "all" ? (
          <TableScroll>
            <table className="w-full text-left text-xs">
              <thead className={STICKY_HEAD}>
                <tr className="text-text-dim">
                  <th className="px-3 py-2 font-medium">Token</th>
                  <th className="px-3 py-2 font-medium">Chain</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Result</th>
                  <th className="px-3 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {all.map((r, i) => {
                  const st = STATUS[r.kind];
                  const p = r.p, f = r.f;
                  return (
                    <tr key={i} className="hover:bg-bg-soft/40">
                      <td className="px-3 py-2.5">
                        <span className="flex items-center gap-1.5">
                          <span className="font-medium text-text">
                            {(p?.symbol || f?.symbol
                              || (p?.address || f?.address || "").slice(0, 10))}
                          </span>
                          {p?.mev_protect && (
                            <Shield size={10} className="text-accent-green"
                                    aria-label="Routed away from the public mempool" />
                          )}
                          {p?.live && p.tx && (
                            <a href={(EXPLORER[p.chain] || "") + p.tx}
                               target="_blank" rel="noopener noreferrer"
                               title={`Buy — ${p.tx}`}
                               className="text-[10px] text-accent-green hover:underline">
                              buy
                            </a>
                          )}
                          {p?.live && p.sell_tx && (
                            <a href={(EXPLORER[p.chain] || "") + p.sell_tx}
                               target="_blank" rel="noopener noreferrer"
                               title={`Sell — ${p.sell_tx}`}
                               className="text-[10px] text-accent-red hover:underline">
                              sell
                            </a>
                          )}
                          {p && !p.live && <Badge variant="gray">paper</Badge>}
                        </span>
                        {(p?.caller) && (
                          <span className="block text-[10px] text-text-dim">{p.caller}</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge variant={CHAIN_TONE[p?.chain || f?.chain] ?? "gray"}>
                          {CHAIN_LABEL[p?.chain || f?.chain] ?? (p?.chain || f?.chain)}
                        </Badge>
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge variant={st.tone}>{st.label}</Badge>
                        {f && (
                          <span className="block text-[10px] text-text-dim">
                            {f.side} · {f.stage}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-text-muted">
                        {p ? (p.spent_native
                              ? `${Number(p.spent_native)} ${p.native}`
                              : fmtUsd(p.usd))
                          : (f?.amount_native ? String(f.amount_native) : "—")}
                      </td>
                      {/* One column, two meanings — a number when there was a
                          trade, and the reason when there was not. Merging
                          them is the point of this view. */}
                      <td className="px-3 py-2.5">
                        {p ? <Money usd={p.pnl_usd ?? 0} pct={p.pnl_pct ?? 0} />
                           : <span className="text-text-muted">{f?.why}</span>}
                      </td>
                      <td className="px-3 py-2.5 text-[11px] text-text-dim">
                        {r.at ? new Date(r.at * 1000).toLocaleString("en-GB") : "—"}
                        {p?.closed_reason && p.closed_reason !== "manual" && (
                          <span className="block text-[10px]">{p.closed_reason}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {all.length === 0 && !isLoading && (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-xs text-text-dim">
                      Nothing yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScroll>
        ) : tab === "failed" ? (
          <TableScroll>
            <table className="w-full text-left text-xs">
              <thead className={STICKY_HEAD}>
                <tr className="text-text-dim">
                  <th className="px-3 py-2 font-medium">Token</th>
                  <th className="px-3 py-2 font-medium">Chain</th>
                  <th className="px-3 py-2 font-medium">Side</th>
                  <th className="px-3 py-2 font-medium">Stopped at</th>
                  <th className="px-3 py-2 font-medium">Reason</th>
                  <th className="px-3 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {failures.map((f, i) => (
                  <tr key={i} className="hover:bg-bg-soft/40">
                    <td className="px-3 py-2.5 font-medium text-text">
                      {f.symbol || f.address?.slice(0, 10)}
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge variant={CHAIN_TONE[f.chain] ?? "gray"}>
                        {CHAIN_LABEL[f.chain] ?? f.chain}
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={f.side === "buy"
                        ? "text-accent-green" : "text-accent-red"}>{f.side}</span>
                      {f.amount_native ? (
                        <span className="block text-[10px] text-text-dim">
                          {f.amount_native}
                        </span>
                      ) : null}
                    </td>
                    {/* Which step refused, not just that something did. The
                        stage is the difference between "the pool was too thin"
                        and "the wallet had no gas". */}
                    <td className="px-3 py-2.5">
                      <Badge variant="amber">{f.stage}</Badge>
                    </td>
                    <td className="px-3 py-2.5 text-text-muted">{f.why}</td>
                    <td className="px-3 py-2.5 text-[11px] text-text-dim">
                      {f.at ? new Date(f.at * 1000).toLocaleString("en-GB") : "—"}
                    </td>
                  </tr>
                ))}
                {failures.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-xs text-text-dim">
                      Nothing has failed. That is the good outcome.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </TableScroll>
        ) : (
        <TableScroll>
          <table className="w-full text-left text-xs">
            <thead className={STICKY_HEAD}>
              <tr className="text-text-dim">
                <th className="px-3 py-2 font-medium">Token</th>
                <th className="px-3 py-2 font-medium">Chain</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Entry</th>
                <th className="px-3 py-2 font-medium">{tab === "holdings" ? "Now" : "Exit"}</th>
                <th className="px-3 py-2 font-medium">P&amp;L</th>
                <th className="px-3 py-2 font-medium">
                  {tab === "holdings" ? "Held for" : "Closed"}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {rows.map((p) => {
                const opened = p.opened_at ? new Date(p.opened_at * 1000) : null;
                const closed = p.closed_at ? new Date(p.closed_at * 1000) : null;
                const hours = opened
                  ? ((closed ?? new Date()).getTime() - opened.getTime()) / 3.6e6
                  : 0;
                return (
                  <tr key={p.id} className="hover:bg-bg-soft/40">
                    <td className="px-3 py-2.5">
                      <span className="flex items-center gap-1.5">
                        <span className="font-medium text-text">
                          {p.symbol || p.address?.slice(0, 8)}
                        </span>
                        {p.mev_protect && (
                          <Shield size={10} className="text-accent-green"
                                  aria-label="Routed away from the public mempool" />
                        )}
                        {/* On chain, and openable. A paper row has nothing to
                            link to, and that absence is the distinction. */}
                        {p.live ? (
                          <>
                            {/* Both legs, labelled. One link was ambiguous the
                                moment a position had been sold: it looked like
                                the trade, and it was only half of it. */}
                            {p.tx && (
                              <a href={(EXPLORER[p.chain] || "") + p.tx}
                                 target="_blank" rel="noopener noreferrer"
                                 title={`Buy — ${p.tx}`}
                                 className="text-[10px] text-accent-green hover:underline">
                                buy
                              </a>
                            )}
                            {p.sell_tx && (
                              <a href={(EXPLORER[p.chain] || "") + p.sell_tx}
                                 target="_blank" rel="noopener noreferrer"
                                 title={`Sell — ${p.sell_tx}`}
                                 className="text-[10px] text-accent-red hover:underline">
                                sell
                              </a>
                            )}
                          </>
                        ) : (
                          <Badge variant="gray">paper</Badge>
                        )}
                      </span>
                      {p.caller && (
                        <span className="block text-[10px] text-text-dim">{p.caller}</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge variant={CHAIN_TONE[p.chain] ?? "gray"}>
                        {CHAIN_LABEL[p.chain] ?? p.chain}
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-text-muted">
                      {p.spent_native ? (
                        <>
                          <span className="block text-text">
                            {Number(p.spent_native)} {p.native}
                          </span>
                          <span className="block text-[10px] text-text-dim">
                            {fmtUsd(p.usd)}
                          </span>
                        </>
                      ) : fmtUsd(p.usd)}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-text-muted">
                      {Number(p.entry).toPrecision(4)}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-text-muted">
                      {Number(p.exit ?? p.last ?? 0).toPrecision(4)}
                    </td>
                    <td className="px-3 py-2.5">
                      <Money usd={p.pnl_usd ?? 0} pct={p.pnl_pct ?? 0} />
                    </td>
                    <td className="px-3 py-2.5 text-[11px] text-text-dim">
                      {tab === "holdings"
                        ? (hours < 1 ? `${Math.round(hours * 60)}m` : `${hours.toFixed(1)}h`)
                        : (closed ? closed.toLocaleString("en-GB") : "—")}
                      {p.closed_reason && p.closed_reason !== "manual" && (
                        <span className="block text-[10px]">{p.closed_reason}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && !isLoading && (
                <tr>
                  <td colSpan={7} className="px-3 py-8 text-center text-xs text-text-dim">
                    {tab === "holdings"
                      ? "Nothing open."
                      : "No trades yet."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </TableScroll>
        )}
      </div>
    </div>
  );
}
