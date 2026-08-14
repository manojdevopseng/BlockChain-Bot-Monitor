"use client";

import { useState } from "react";
import { ExternalLink, RefreshCw, Target, Trash2 } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { FilterTabs, HistorySelect, SearchBox } from "@/components/SectionFilters";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Age } from "@/components/Age";
import { fmtDateTime, shortAddr, rowKey } from "@/lib/utils";

/* Market Cap Alert.
 *
 * The tokens you added and the market cap each one is being watched for. It
 * lives in the RSI nav because both are "watch this token and tell me when",
 * but they share nothing else: this one reads supply × price on a timer and
 * says a single thing, once, when the number is reached.
 *
 * A row stays until you remove it — including after it has fired. */

type Chain = { id: string; label: string; enabled: boolean; own_endpoints: boolean };

// $1.2M / $940K / $12,345 — the same shape the Telegram alert uses, so a
// figure read on the phone matches the one on the page.
function fmtUsd(value: number | null | undefined): string {
  const n = Number(value || 0);
  if (!n) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function AddToken({ chains, onAdded }: { chains: Chain[]; onAdded: () => void }) {
  const [chain, setChain] = useState("rbh");
  const [address, setAddress] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function add() {
    if (!address.trim() || !target.trim()) return;
    setBusy(true); setErr("");
    try {
      await apiSend("/api/mcap/tokens", "POST",
        { chain, address: address.trim(), target: target.trim() });
      setAddress(""); setTarget("");
      onAdded();
    } catch (e: any) {
      setErr(e?.message || "could not watch that token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3 rounded-lg border border-border-soft bg-bg-soft/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <select value={chain} onChange={(e) => setChain(e.target.value)}
                className="h-8 rounded border border-border bg-bg-soft px-2 text-xs text-text">
          {chains.map((c) => (
            <option key={c.id} value={c.id}>{c.enabled ? c.label : `${c.label} (off)`}</option>
          ))}
        </select>
        <Input value={address} onChange={(e) => setAddress(e.target.value)}
               placeholder="contract address" className="h-8 w-[22rem] text-xs" />
        {/* Free text rather than a number field: 250k and 1.5m are how a
            market cap is actually said, and typing 1500000 on a phone is how
            the wrong target gets set. */}
        <Input value={target} onChange={(e) => setTarget(e.target.value)}
               placeholder="target — 250k / 1.5m / 40000"
               className="h-8 w-56 text-xs" />
        <Button size="sm" onClick={add} disabled={busy || !address.trim() || !target.trim()}>
          <Target size={13} className="mr-1" /> Watch
        </Button>
        {err && <span className="text-xs text-accent-red">{err}</span>}
      </div>
    </div>
  );
}

export function MarketCapSection() {
  const [chain, setChain] = useState("all");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q, 300);

  const { data: chainData } = useApi<any>("/api/mcap/chains");
  const { data: settings } = useApi<any>("/api/mcap/settings");
  const chains: Chain[] = chainData?.items ?? [];

  const params = new URLSearchParams();
  if (chain !== "all") params.set("chain", chain);
  if (query) params.set("q", query);
  if (date) params.set("date", date);
  const key = `/api/mcap/tokens${params.toString() ? `?${params}` : ""}`;
  const { data } = useApi<any>(key);
  const { data: datesData } = useApi<any>(`/api/mcap/dates?chain=${chain}`);
  const items = data?.items ?? [];
  const reload = () => { mutate(key); mutate("/api/mcap/stats"); };

  const tabs = [{ id: "all", label: "All" },
                ...chains.map((c) => ({ id: c.id,
                                        label: c.enabled ? c.label : `${c.label} (off)` }))];

  async function setTarget(address: string, value: string) {
    if (!value.trim()) return;
    await apiSend(`/api/mcap/tokens/${address}`, "PATCH", { target: value.trim() });
    reload();
  }
  async function setCadence(value: string) {
    await apiSend("/api/mcap/settings", "PATCH", { cadence: value });
    mutate("/api/mcap/settings");
  }
  async function remove(address: string) {
    await apiSend(`/api/mcap/tokens/${address}`, "DELETE");
    reload();
  }

  return (
    <CollapsibleSection
      id="mcap-alert"
      title="Market Cap Alert"
      icon={<Target size={14} />}
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
        Supply × price, read on chain every{" "}
        <b>{settings?.cadence ?? "15s"}</b> for every token on this list, and one
        alert the moment a token reaches the market cap you set for it. A target
        above where it is now fires on the way up; below, on the way down. Nothing
        is added or removed on its own — a token stays here, target and all, until
        you delete it.
        {settings && !settings.alert_chat_set && (
          <> {" "}<span className="text-accent-amber">
            No alert chat set — market caps are recorded, nothing is sent.
          </span></>
        )}
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-text-dim">
        <span>Checked every</span>
        {(settings?.cadences ?? ["15s", "30s", "1m", "5m"]).map((c: string) => (
          <button key={c} onClick={() => setCadence(c)}
                  className={`rounded border px-2 py-1 ${
                    (settings?.cadence ?? "15s") === c
                      ? "border-brand bg-brand/10 text-brand-soft"
                      : "border-border-soft text-text-muted hover:bg-bg-hover"}`}>
            {c}
          </button>
        ))}
        <span className="text-text-dim">— one request per token per pass</span>
      </div>

      <AddToken chains={chains} onAdded={reload} />

      <TableScroll>
        <table className="w-full min-w-[54rem] text-left text-xs">
          <thead className={STICKY_HEAD}>
            <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-text-dim">
              <th className="px-3 py-2 font-medium">Token</th>
              <th className="px-3 py-2 font-medium">Chain</th>
              <th className="px-3 py-2 font-medium">Market Cap</th>
              <th className="px-3 py-2 font-medium">Target</th>
              <th className="px-3 py-2 font-medium">To go</th>
              <th className="px-3 py-2 font-medium">Price</th>
              <th className="px-3 py-2 font-medium">Checked</th>
              <th className="px-3 py-2 font-medium">Added</th>
              <th className="px-3 py-2 font-medium"></th>
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
                  <span className="font-mono text-sm text-text">{fmtUsd(r.mcap)}</span>
                  {r.hit_at && <Badge variant="green">hit</Badge>}
                </td>
                <td className="px-3 py-3">
                  {/* Editable in place: a target is the one thing about a row
                      that changes, and re-adding the token to move it would
                      lose the reading it already has. */}
                  <input defaultValue={fmtUsd(r.target).replace("$", "")}
                         onBlur={(e) => {
                           const v = e.target.value.trim();
                           if (v && v !== fmtUsd(r.target).replace("$", "")) setTarget(r.address, v);
                         }}
                         onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                         className="h-7 w-24 rounded border border-border bg-bg-soft px-1.5 font-mono text-[11px] text-text" />
                </td>
                <td className="px-3 py-3">
                  <span className={`font-mono text-[11px] ${
                    r.to_target_pct == null ? "text-text-dim"
                      : r.to_target_pct <= 0 ? "text-accent-green" : "text-text-muted"}`}>
                    {r.to_target_pct == null ? "—"
                      : r.to_target_pct > 0 ? `+${r.to_target_pct}%` : `${r.to_target_pct}%`}
                  </span>
                </td>
                <td className="px-3 py-3">
                  <span className="font-mono text-[11px] text-text-muted">
                    {r.price_usd ? `$${Number(r.price_usd).toPrecision(4)}` : "—"}
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
                  <button onClick={() => remove(r.address)} title="Stop watching this token"
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
  );
}
