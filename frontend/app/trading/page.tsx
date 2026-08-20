"use client";

import { useState } from "react";
import { mutate } from "swr";
import { ExternalLink, Loader2, RefreshCw, Settings2, Sparkles, Trash2 } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge, Variant } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { FilterTabs } from "@/components/SectionFilters";
import { fmtUsd, shortAddr } from "@/lib/utils";
import { QuickSettings } from "./_components/QuickSettings";

/* Trading — what the account bought, holds, and made or lost.
 *
 * Recorded rather than executed, and the page says so where it cannot be
 * missed. The point of it is the question underneath: following starred
 * callers automatically — is that actually profitable? Two days of this
 * answers that for nothing, and the same screen becomes the live one later. */

const TABS = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "closed", label: "Closed" },
] as const;

const CHAIN_LABEL: Record<string, string> = {
  eth: "Ethereum", rbh: "Robinhood", bnb: "BNB", sol: "Solana", base: "Base",
};
const CHAIN_TONE: Record<string, Variant> = {
  eth: "blue", rbh: "green", bnb: "amber", sol: "purple", base: "cyan",
};
const SOURCE_LABEL: Record<string, string> = {
  auto: "Auto", manual: "Manual", demo: "Demo",
};

function gmgn(chain: string, address: string) {
  const slug = { eth: "eth", rbh: "robinhood", bnb: "bsc", sol: "sol", base: "base" }[chain] || chain;
  return `https://gmgn.ai/${slug}/token/${address}`;
}

function Money({ usd, pct }: { usd: number; pct: number }) {
  const up = usd >= 0;
  return (
    <span className={up ? "text-accent-green" : "text-accent-red"}>
      <span className="font-medium tabular-nums">{up ? "+" : "−"}{fmtUsd(Math.abs(usd))}</span>
      <span className="ml-1 text-[11px] tabular-nums opacity-80">
        ({up ? "+" : "−"}{Math.abs(pct).toFixed(1)}%)
      </span>
    </span>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-bg-card/60 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-text-dim">{label}</div>
      <div className="mt-1 text-lg font-semibold text-text">{children}</div>
    </div>
  );
}

export default function TradingPage() {
  const [tab, setTab] = useState<"all" | "open" | "closed">("all");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");

  const { data: conf } = useApi<any>("/api/trading/settings");
  const { data } = useApi<any>(`/api/trading/positions?status=${tab}`);

  const items: any[] = data?.items ?? [];
  const sum = data?.summary ?? {};
  const presets: number[] = conf?.sell_presets?.length ? conf.sell_presets : [25, 50, 100];

  function reload() {
    mutate((k) => typeof k === "string" && k.startsWith("/api/trading"));
  }

  async function act(id: string, fn: () => Promise<any>) {
    setBusy(id);
    setErr("");
    try { await fn(); reload(); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Trading"
                  subtitle="Positions taken from calls and from the detection panels" />

      <div className="rounded-xl border border-accent-amber/30 bg-accent-amber/10 px-4 py-3
                      text-xs leading-relaxed text-accent-amber">
        <b>Paper trading.</b> Every buy and sell here is recorded at the real price
        at that moment, and nothing is sent to a chain. GMGN's trading API signs
        with a private key rather than an API key and serves Solana only — so
        until that changes, this answers whether the strategy works without
        risking anything on finding out.
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Open">{sum.open ?? 0}</Stat>
        <Stat label="Held value">{fmtUsd(sum.open_value ?? 0)}</Stat>
        <Stat label="Unrealised">
          {sum.open ? <Money usd={sum.unrealised ?? 0}
                             pct={sum.open_cost ? (sum.unrealised / sum.open_cost) * 100 : 0} />
                    : <span className="text-text-dim">—</span>}
        </Stat>
        <Stat label="Realised">
          <span className="flex items-baseline gap-2">
            <span className={(sum.realised ?? 0) >= 0 ? "text-accent-green" : "text-accent-red"}>
              {fmtUsd(sum.realised ?? 0)}
            </span>
            <span className="text-[11px] font-normal text-text-dim">
              {sum.win_rate == null ? "no closed trades yet" : `${sum.win_rate}% won`}
            </span>
          </span>
        </Stat>
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-bg-card/60">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
          <h2 className="mr-auto text-sm font-semibold text-text">
            Positions
            {conf?.auto_buy
              ? <Badge variant="green" className="ml-2">auto-buy on</Badge>
              : <Badge variant="gray" className="ml-2">auto-buy off</Badge>}
          </h2>
          <FilterTabs value={tab} onChange={setTab} options={TABS} />
          <Button size="sm" variant="outline"
                  onClick={() => act("refresh", () => apiSend("/api/trading/refresh", "POST"))}>
            {busy === "refresh" ? <Loader2 size={13} className="animate-spin" />
                                : <RefreshCw size={13} />} Prices
          </Button>
          <Button size="sm" variant="outline"
                  onClick={() => act("demo", () => apiSend("/api/trading/demo", "POST"))}>
            {busy === "demo" ? <Loader2 size={13} className="animate-spin" />
                             : <Sparkles size={13} />} Demo token
          </Button>
          <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
            <Settings2 size={13} /> Quick Buy / Sell Settings
          </Button>
        </div>

        {err && <p className="border-b border-border px-3 py-2 text-[11px] text-accent-red">{err}</p>}

        <TableScroll maxHeight={620}>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Chain</th>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Source</th>
                <th className="px-3 py-2.5 font-medium">Spent</th>
                <th className="px-3 py-2.5 font-medium">Entry</th>
                <th className="px-3 py-2.5 font-medium">Now</th>
                <th className="px-3 py-2.5 font-medium">P&amp;L</th>
                <th className="px-3 py-2.5 font-medium">Sell</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">
                  Nothing here yet. Turn auto-buy on for your starred callers, buy
                  from a detection row, or open a demo token to see the page work.
                </td></tr>
              ) : items.map((p) => (
                <tr key={p.id} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <Badge variant={CHAIN_TONE[p.chain] || "gray"}>
                      {CHAIN_LABEL[p.chain] || p.chain}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <a href={gmgn(p.chain, p.address)} target="_blank" rel="noopener noreferrer"
                         className="font-semibold text-text hover:text-brand-soft hover:underline">
                        {p.symbol || "?"}
                      </a>
                      <ExternalLink size={11} className="text-text-dim" />
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5">
                      <span className="font-mono text-[11px] text-accent-blue">
                        {shortAddr(p.address)}
                      </span>
                      <CopyButton value={p.address} />
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <span className="text-[11px] text-text-muted">
                      {SOURCE_LABEL[p.source] || p.source}
                    </span>
                    {p.caller && (
                      <span className="mt-0.5 block max-w-[13ch] truncate text-[11px] text-text-dim"
                            title={p.caller}>{p.caller}</span>
                    )}
                  </td>
                  <td className="px-3 py-3 tabular-nums text-text-muted">{fmtUsd(p.usd)}</td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-[11px] text-text-muted">
                      {p.entry?.toPrecision(4)}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-[11px] text-text-muted">
                      {(p.exit ?? p.last)?.toPrecision(4)}
                    </span>
                  </td>
                  <td className="px-3 py-3"><Money usd={p.pnl_usd} pct={p.pnl_pct} /></td>
                  <td className="px-3 py-3">
                    {p.status === "closed" ? (
                      <span className="text-[11px] text-text-dim">closed</span>
                    ) : (
                      <span className="flex flex-wrap gap-1">
                        {presets.map((pc) => (
                          <button key={pc} disabled={busy === p.id}
                            onClick={() => act(p.id, () =>
                              apiSend(`/api/trading/sell/${p.id}`, "POST", { percent: pc }))}
                            className="rounded border border-border px-1.5 py-0.5 text-[11px]
                                       text-text-dim transition-colors hover:border-accent-red/40
                                       hover:text-accent-red disabled:opacity-50">
                            {busy === p.id ? "…" : `${pc}%`}
                          </button>
                        ))}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>

        {items.some((p) => p.source === "demo") && (
          <div className="border-t border-border px-3 py-2">
            <button
              onClick={() => act("cleardemo", () => apiSend("/api/trading/demo", "DELETE"))}
              className="flex items-center gap-1.5 text-[11px] text-text-dim hover:text-accent-red">
              <Trash2 size={11} /> Remove the demo positions
            </button>
          </div>
        )}
      </div>

      {open && conf && (
        <QuickSettings conf={conf} onClose={() => setOpen(false)} onSaved={reload} />
      )}
    </div>
  );
}
