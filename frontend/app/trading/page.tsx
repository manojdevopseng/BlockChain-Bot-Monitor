"use client";

import { useState } from "react";
import { mutate } from "swr";
import {
  ExternalLink, Loader2, Play, Power, RefreshCw, Settings2, Sparkles, Trash2,
} from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge, Variant } from "@/components/ui/badge";
import { CopyButton } from "@/components/CopyButton";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { FilterTabs } from "@/components/SectionFilters";
import { fmtUsd, shortAddr } from "@/lib/utils";
import { QuickSettings } from "./_components/QuickSettings";
import { CallerPnl } from "./_components/CallerPnl";
import { WalletStrip } from "./_components/WalletStrip";

/* Trading — what the account bought, holds, and made or lost.
 *
 * Recorded rather than executed, and the page says so where it cannot be
 * missed. The point of it is the question underneath: following starred
 * callers automatically — is that actually profitable? Two days of this
 * answers that for nothing, and the same screen becomes the live one later.
 *
 * The three switches on the Positions bar are here rather than buried in the
 * settings modal because they are the ones reached for in a hurry. A kill
 * switch two clicks deep is not a kill switch. */

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

/* A switch that saves the moment it is flipped. No Save button, because the
   things behind these are the things somebody wants off *now*. */
function Switch({ on, label, tone = "brand", busy, onChange }: {
  on: boolean; label: string; tone?: "brand" | "red"; busy?: boolean;
  onChange: (v: boolean) => void;
}) {
  const active = tone === "red"
    ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
    : "border-accent-green/40 bg-accent-green/10 text-accent-green";
  return (
    <button onClick={() => onChange(!on)} disabled={busy}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1
                  text-[11px] font-medium transition-colors disabled:opacity-50 ${
        on ? active : "border-border text-text-dim hover:text-text"
      }`}>
      <span className={`h-1.5 w-1.5 rounded-full ${
        on ? (tone === "red" ? "bg-accent-red" : "bg-accent-green") : "bg-text-dim"}`} />
      {busy ? "…" : label}
    </button>
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
  const day = conf?.day ?? {};
  const presets: number[] = conf?.sell_presets?.length ? conf.sell_presets : [25, 50, 100];

  function reload() {
    mutate((k) => typeof k === "string" && k.startsWith("/api/trading"));
  }

  async function act(id: string, fn: () => Promise<any>) {
    setBusy(id);
    setErr("");
    try { await fn(); reload(); }
    catch (e: any) { setErr(String(e?.message || e).replace(/^Error:\s*/, "")); }
    finally { setBusy(null); }
  }

  const patch = (body: any) => apiSend("/api/trading/settings", "PATCH", body);

  return (
    <div className="space-y-5">
      <PageHeader title="Trading"
                  subtitle="Positions taken from calls and from the detection panels" />

      <div className="rounded-xl border border-accent-amber/30 bg-accent-amber/10 px-4 py-3
                      text-xs leading-relaxed text-accent-amber">
        <b>Paper trading.</b> Every buy and sell here is recorded at the real price
        at that moment, and nothing is sent to a chain. Executing for real means
        something has to hold a key and sign — a decision about custody, not a
        setting. Until that is made, this answers whether the strategy works
        without risking anything on finding out.
      </div>

      {/* The real wallet, above the pretend one. Deliberately adjacent: the
          paper P&L below is easier to read honestly when what you actually
          hold is on the same screen. */}
      <WalletStrip />

      {/* Why auto-buy is off, when something turned it off. Shown loudly: a
          toggle that is mysteriously off costs more than the stop it came from. */}
      {conf?.stopped_reason && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-accent-red/40
                        bg-accent-red/10 px-4 py-3 text-xs text-accent-red">
          <Power size={14} className="shrink-0" />
          <span className="mr-auto">
            <b>Auto-buy is stopped</b> — {conf.stopped_reason}. Nothing is buying on
            its own. Open positions and the auto-sell rules are untouched.
          </span>
          <Button size="sm" variant="outline" disabled={busy === "resume"}
                  onClick={() => act("resume", () => patch({ auto_buy: true }))}>
            {busy === "resume" ? <Loader2 size={13} className="animate-spin" />
                               : <Play size={13} />} Turn auto-buy back on
          </Button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
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
        {/* The number the daily loss limit actually watches. */}
        <Stat label="Today">
          {day.trades ? (
            <span className="flex items-baseline gap-2">
              <Money usd={day.pnl ?? 0} pct={day.pct ?? 0} />
              {conf?.loss_limit_on && (
                <span className="text-[11px] font-normal text-text-dim">
                  stops at −{conf.loss_limit_pct}%
                </span>
              )}
            </span>
          ) : <span className="text-text-dim">—</span>}
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

          <Switch on={!!conf?.auto_sell} busy={busy === "autosell"}
                  label={conf?.auto_sell
                    ? `Auto-sell on · TP ${conf.take_profit_pct}% / SL ${conf.stop_loss_pct}%`
                    + (conf.trailing_pct ? ` / trail ${conf.trailing_pct}%` : "")
                    : "Auto-sell off"}
                  onChange={(v) => act("autosell", () => patch({ auto_sell: v }))} />

          <Switch on={!!conf?.loss_limit_on} busy={busy === "losslimit"}
                  label={conf?.loss_limit_on
                    ? `Daily loss limit ${conf.loss_limit_pct}%`
                    : "Daily loss limit off"}
                  onChange={(v) => act("losslimit", () => patch({ loss_limit_on: v }))} />

          {/* Only there when there is something to kill. */}
          {conf?.auto_buy && (
            <button disabled={busy === "kill"}
              onClick={() => act("kill", () => apiSend("/api/trading/stop", "POST", {}))}
              className="inline-flex items-center gap-1.5 rounded-md border border-accent-red/50
                         bg-accent-red/10 px-2.5 py-1 text-[11px] font-semibold text-accent-red
                         transition-colors hover:bg-accent-red/20 disabled:opacity-50">
              {busy === "kill" ? <Loader2 size={12} className="animate-spin" />
                               : <Power size={12} />} Kill switch
            </button>
          )}

          <FilterTabs value={tab} onChange={setTab} options={TABS} />
          <Button size="sm" variant="outline"
                  onClick={() => act("refresh", () => apiSend("/api/trading/rules", "POST"))}
                  title="Mark every open position to market and run the auto-sell rules now">
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
                      // What closed it, not just that it is closed — a position
                      // the account never touched needs to say why it is gone.
                      <span className="text-[11px] text-text-dim"
                            title={p.closed_reason || "closed"}>
                        {p.closed_reason && p.closed_reason !== "manual"
                          ? p.closed_reason
                          : "closed by hand"}
                      </span>
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

      <CallerPnl />

      {open && conf && (
        <QuickSettings conf={conf} onClose={() => setOpen(false)} onSaved={reload} />
      )}
    </div>
  );
}
