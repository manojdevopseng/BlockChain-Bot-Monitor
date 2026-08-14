"use client";

import { useState } from "react";
import { Activity, Plus, RefreshCw, Trash2 } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { FilterTabs, HistorySelect, SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge, Variant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Age } from "@/components/Age";
import { ExternalLink } from "lucide-react";
import { fmtDateTime, shortAddr, rowKey } from "@/lib/utils";
import { MarketCapSection } from "./_components/MarketCapSection";
import { MarketCapCheck } from "./_components/MarketCapCheck";

/* RSI Tracker.
 *
 * The tokens you added, what their RSI is now, and on which interval each one
 * is being read. Nothing appears here on its own — this list is yours. */

const ZONE_TONE: Record<string, Variant> = {
  oversold: "green", overbought: "red", neutral: "gray",
};

type Chain = { id: string; label: string; enabled: boolean; own_endpoints: boolean };
type Interval = { id: string; label: string };

// Adding a token: the chain and the address are the only things that cannot be
// worked out later. The timeframe starts at whatever the default is set to —
// 5 Min unless it has been changed — and this row, or the token's own row
// afterwards, is where it gets moved off it.
function AddToken({ chains, intervals, fallback, onAdded }: {
  chains: Chain[]; intervals: Interval[]; fallback: string; onAdded: () => void;
}) {
  const [chain, setChain] = useState("eth");
  const [address, setAddress] = useState("");
  const [symbol, setSymbol] = useState("");
  const [interval, setInterval] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function add() {
    if (!address.trim()) return;
    setBusy(true); setErr("");
    try {
      await apiSend("/api/rsi/tokens", "POST",
        { chain, address: address.trim(), symbol: symbol.trim(),
          interval: interval || fallback });
      setAddress(""); setSymbol("");
      onAdded();
    } catch (e: any) {
      setErr(e?.message || "could not add that token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3">
      <div className="flex flex-wrap gap-2">
        <select value={chain} onChange={(e) => setChain(e.target.value)}
                className="h-9 rounded-lg border border-border bg-bg-soft px-2 text-xs text-text">
          {chains.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <Input value={address} onChange={(e) => setAddress(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && add()}
               placeholder="0x… contract address" className="min-w-[280px] flex-1" />
        <Input value={symbol} onChange={(e) => setSymbol(e.target.value)}
               placeholder="ticker (optional)" className="w-40" />
        <select value={interval || fallback} onChange={(e) => setInterval(e.target.value)}
                title="This token's RSI timeframe"
                className="h-9 rounded-lg border border-border bg-bg-soft px-2 text-xs text-text">
          {intervals.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
        </select>
        <Button variant="primary" size="sm" disabled={busy || !address.trim()} onClick={add}>
          <Plus size={14} /> Add
        </Button>
      </div>
      {err && <p className="mt-1.5 text-[11px] text-accent-red">{err}</p>}
    </div>
  );
}

export default function RsiPage() {
  const [chain, setChain] = useState("all");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);

  const { data: chainData } = useApi<any>("/api/rsi/chains");
  const { data: settings } = useApi<any>("/api/rsi/settings");
  const { data: stats } = useApi<any>("/api/rsi/stats");
  const chains: Chain[] = chainData?.items ?? [];
  const intervals: Interval[] = settings?.intervals ?? [];

  const params = new URLSearchParams();
  if (chain !== "all") params.set("chain", chain);
  if (query) params.set("q", query);
  if (date) params.set("date", date);
  const key = `/api/rsi/tokens${params.toString() ? `?${params}` : ""}`;
  const { data } = useApi<any>(key);
  const { data: datesData } = useApi<any>(`/api/rsi/dates?chain=${chain}`);
  const items = data?.items ?? [];
  const reload = () => { mutate(key); mutate("/api/rsi/stats"); };

  const tabs = [{ id: "all", label: "All" },
                ...chains.map((c) => ({ id: c.id,
                                        label: c.enabled ? c.label : `${c.label} (off)` }))];

  async function setInterval(address: string, value: string) {
    await apiSend(`/api/rsi/tokens/${address}`, "PATCH", { interval: value });
    reload();
  }
  // How many candles this one token's reading is made of. Per token for the
  // same reason the timeframe is: a fast one and a slow one want different
  // answers, and the default only decides where a new token starts.
  async function setCandles(address: string, value: number) {
    await apiSend(`/api/rsi/tokens/${address}`, "PATCH", { candles: value });
    reload();
  }
  async function setDefaultCandles(value: number) {
    await apiSend("/api/rsi/settings", "PATCH", { default_candles: value });
    mutate("/api/rsi/settings");
  }
  async function remove(address: string) {
    await apiSend(`/api/rsi/tokens/${address}`, "DELETE");
    reload();
  }
  // The default only decides what a NEW token starts on — tokens already on
  // the list keep whatever they were set to, from here or from Telegram.
  async function setDefaultInterval(value: string) {
    await apiSend("/api/rsi/settings", "PATCH", { default_interval: value });
    mutate("/api/rsi/settings");
  }
  async function setCadence(value: string) {
    await apiSend("/api/rsi/settings", "PATCH", { cadence: value });
    mutate("/api/rsi/settings");
  }
  async function setBounds(low: number, high: number) {
    await apiSend("/api/rsi/settings", "PATCH", { low, high });
    mutate("/api/rsi/settings");
  }

  return (
    <div className="space-y-5">
      <PageHeader title="RSI" subtitle="Relative strength on the tokens you are watching" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Tokens" value={stats?.total ?? 0} icon={Activity} tone="purple" />
        <StatCard label="Oversold" value={stats?.oversold ?? 0} icon={Activity} tone="green" />
        <StatCard label="Overbought" value={stats?.overbought ?? 0} icon={Activity} tone="red" />
        <StatCard label="Candles" value={stats?.candles ?? 0} icon={Activity} tone="cyan" />
      </div>

      <CollapsibleSection
        id="rsi-tracker"
        title="RSI Tracker"
        icon={<Activity size={14} />}
        count={data?.total ?? 0}
        controls={<>
          <FilterTabs value={chain} onChange={setChain} options={tabs} />
          <SearchBox value={q} onChange={setQ} placeholder="address / ticker / name" />
          <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
          <Button size="sm" variant="outline" onClick={reload} title="Refresh now">
            <RefreshCw size={13} />
          </Button>
        </>}
      >
        <p className="mb-3 text-xs text-text-dim">
          Wilder&rsquo;s RSI over {settings?.period ?? 14} candles, read off each
          token&rsquo;s own pool. A token is only added here by you, and each one runs
          on its own <b>timeframe</b> — the length of one candle. New tokens start
          on {intervals.find((i) => i.id === (settings?.default_interval ?? "5m"))?.label
              ?? "5 Min"}; change a row and only that token moves. Alerts fire when
          the RSI on that timeframe <b>crosses</b> {settings?.low ?? 30} or{" "}
          {settings?.high ?? 70}, not while it sits there. Kept{" "}
          {settings?.retention_days ?? 15} days.
          {settings && !settings.alert_chat_set && (
            <> {" "}<span className="text-accent-amber">
              No alert chat set — readings are recorded, nothing is sent.
            </span></>
          )}
        </p>

        {/* Three controls, and the first two are constantly mistaken for each
            other: the timeframe is how long one candle is — what RSI is
            actually computed on — while the cadence is only how often that sum
            is redone, which is what costs RPC requests. */}
        <div className="mb-3 flex flex-wrap items-center gap-4 rounded-lg border border-border-soft px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-dim"
                  title="The candle RSI is read on. New tokens start here; each row can be changed on its own.">
              Timeframe (RSI candles)
            </span>
            <FilterTabs value={settings?.default_interval ?? "5m"}
                        onChange={setDefaultInterval}
                        options={intervals.map((i) => ({ id: i.id, label: i.label }))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-dim"
                  title="How many candles one reading is made of. 15 is the chart default (RSI 14). Fewer turns sooner and jumps about; more is steadier.">
              Candles
            </span>
            <FilterTabs value={String(settings?.default_candles ?? 15)}
                        onChange={(v) => setDefaultCandles(Number(v))}
                        options={(settings?.candle_choices ?? [8, 10, 15, 21, 31, 51])
                          .map((n: number) => ({ id: String(n), label: String(n) }))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-dim"
                  title="How often RSI is recomputed and the bounds are checked — this is the RPC cost, not the candle length.">
              Recheck every (RPC)
            </span>
            <FilterTabs value={settings?.cadence ?? "30s"} onChange={setCadence}
                        options={(settings?.cadences ?? []).map((c: string) => ({ id: c, label: c }))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-dim">Bounds</span>
            <Input defaultValue={settings?.low ?? 30} className="h-7 w-16 text-xs"
                   onBlur={(e) => setBounds(Number(e.target.value), settings?.high ?? 70)} />
            <span className="text-[11px] text-text-dim">/</span>
            <Input defaultValue={settings?.high ?? 70} className="h-7 w-16 text-xs"
                   onBlur={(e) => setBounds(settings?.low ?? 30, Number(e.target.value))} />
          </div>
        </div>

        <AddToken chains={chains} intervals={intervals}
                  fallback={settings?.default_interval ?? "5m"} onAdded={reload} />

        <TableScroll>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Chain</th>
                <th className="px-3 py-2.5 font-medium"
                    title="This token's own RSI timeframe">Timeframe</th>
                <th className="px-3 py-2.5 font-medium"
                    title="How many candles this token's reading is made of">Candles</th>
                <th className="px-3 py-2.5 font-medium">RSI</th>
                <th className="px-3 py-2.5 font-medium">Price</th>
                <th className="px-3 py-2.5 font-medium">Checked</th>
                <th className="px-3 py-2.5 font-medium">Added</th>
                <th className="px-3 py-2.5 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-text-dim">
                  {query ? "Nothing matches this search"
                    : date ? `Nothing read on ${date}`
                    : "No tokens yet — add one above"}
                </td></tr>
              ) : items.map((r: any, i: number) => (
                <tr key={rowKey(r, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-text">{r.symbol || "?"}</span>
                      <CopyButton value={r.address} />
                      {r.gmgn_url && (
                        <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
                           className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                    <span className="font-mono text-[10px] text-text-dim">{shortAddr(r.address)}</span>
                  </td>
                  <td className="px-3 py-3"><Badge variant="blue">{String(r.chain).toUpperCase()}</Badge></td>
                  <td className="px-3 py-3">
                    {/* Per token, because that is the whole point: one on 1 Sec,
                        the rest on 5 Min. Changing it starts a fresh series. */}
                    <select value={r.interval} onChange={(e) => setInterval(r.address, e.target.value)}
                            className="h-7 rounded border border-border bg-bg-soft px-1.5 text-[11px] text-text">
                      {intervals.map((iv) => <option key={iv.id} value={iv.id}>{iv.label}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-3">
                    <select value={r.candles ?? 15}
                            onChange={(e) => setCandles(r.address, Number(e.target.value))}
                            className="h-7 rounded border border-border bg-bg-soft px-1.5 text-[11px] text-text">
                      {(settings?.candle_choices ?? [8, 10, 15, 21, 31, 51])
                        .map((n: number) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-3">
                    {r.rsi == null ? (
                      <span className="text-[11px] text-text-dim">
                        warming up {r.samples ?? 0}/{r.candles ?? 15}
                      </span>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-sm text-text">{r.rsi.toFixed(1)}</span>
                        {r.zone && <Badge variant={ZONE_TONE[r.zone] || "gray"}>{r.zone}</Badge>}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-[11px] text-text-muted">
                      {r.price ? Number(r.price).toPrecision(6) : "—"}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted">
                      {r.checked_at ? <Age ts={r.checked_at} /> : "—"}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted">
                      {r.added_at ? fmtDateTime(r.added_at) : "—"}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <button onClick={() => remove(r.address)} title="Stop tracking this token"
                            className="grid h-6 w-6 place-items-center rounded text-text-dim hover:bg-bg-hover hover:text-accent-red">
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </CollapsibleSection>

      {/* Same nav, separate feature: "has this turned" above, "has this got
          there" here. Its own worker, its own endpoints, its own switches. */}
      <MarketCapSection />

      {/* And the other half of the same question: not "tell me when", but
          "what is it right now". Same reader, nothing stored. */}
      <MarketCapCheck />
    </div>
  );
}
