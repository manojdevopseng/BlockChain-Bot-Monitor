"use client";

import { useState } from "react";
import { Search, Layers, History as HistoryIcon, Fuel, ExternalLink, ArrowRightLeft } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DetectionTable } from "@/components/DetectionTable";
import { CrossChainTable } from "@/components/CrossChainTable";
import { CopyButton } from "@/components/CopyButton";
import { fmtEth, shortAddr, timeAgo } from "@/lib/utils";

/* ── shared section chrome ─────────────────────────────────────────────── */

function SectionHead({
  title, icon, count, children,
}: {
  title: string;
  icon?: React.ReactNode;
  count?: number;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text-muted">
        {icon}{title}
      </h3>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        {count !== undefined && <Badge variant="purple">{count}</Badge>}
      </div>
    </div>
  );
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-dim" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="h-8 w-60 pl-8 text-xs" />
    </div>
  );
}

function HistorySelect({ value, onChange, dates }: { value: string; onChange: (v: string) => void; dates: string[] }) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-lg border border-border bg-bg-soft pl-7 pr-2 text-xs text-text focus:border-brand/60 focus:outline-none">
        <option value="">Live</option>
        {dates.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
      <HistoryIcon size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
    </div>
  );
}

/* ── 1-3: premium caller detection panels (RBH / ETH / SOL) ─────────────── */

function PremiumSection({ chain, title }: { chain: "eth" | "rbh" | "sol"; title: string }) {
  const [q, setQ] = useState("");
  const [multi, setMulti] = useState(false);
  const [date, setDate] = useState("");

  const { data: datesData } = useApi<any>(`/api/forwarder/detections/dates?chain=${chain}`);
  const live = useApi<any>(
    date ? null : `/api/forwarder/detections?chain=${chain}&multi=${multi}${q ? `&q=${encodeURIComponent(q)}` : ""}`
  );
  const hist = useApi<any>(date ? `/api/forwarder/detections/history?chain=${chain}&date=${date}` : null);
  const data = date ? hist.data : live.data;

  return (
    <Card>
      <SectionHead title={title} count={data?.total ?? 0}>
        <SearchBox value={q} onChange={setQ} placeholder="symbol / name / address / group" />
        <Button size="sm" variant={multi ? "primary" : "outline"} onClick={() => setMulti((v) => !v)}
          title="Only tokens called by 2+ groups">
          <Layers size={13} /> Multi 2+
        </Button>
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </SectionHead>
      <CardContent className="pt-0">
        <DetectionTable items={data?.items ?? []} />
      </CardContent>
    </Card>
  );
}

/* ── 4: ETH gas fees (per-tx) ───────────────────────────────────────────── */

function GasSection() {
  const [q, setQ] = useState("");
  const { data } = useApi<any>(`/api/rpc/gas/recent${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  const { data: summary } = useApi<any>("/api/rpc/gas");
  const items = data?.items ?? [];

  return (
    <Card>
      <SectionHead
        title="ETH Gas Fees — High-Gas Early Buys"
        icon={<Fuel size={14} />}
        count={items.length}
      >
        <SearchBox value={q} onChange={setQ} placeholder="symbol / address / tx" />
        <span className="text-xs text-text-dim">
          threshold <span className="text-text">{fmtEth(summary?.min_fee_eth)}</span>
        </span>
        <span className="text-xs text-text-dim">
          max <span className="text-text">{fmtEth(summary?.max_eth)}</span>
        </span>
        <Badge variant={summary?.enabled ? "green" : "gray"}>{summary?.enabled ? "on" : "off"}</Badge>
      </SectionHead>
      <CardContent className="pt-0">
        <p className="mb-3 text-xs text-text-dim">
          Every new V2/V4 pair is watched; a buy paying ≥ {fmtEth(summary?.min_fee_eth)} gas means
          someone is sniping — it fires one alert and the watch stops.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-dim">
                <th className="px-3 py-2.5 font-medium">Symbol</th>
                <th className="px-3 py-2.5 font-medium">Name</th>
                <th className="px-3 py-2.5 font-medium">CA</th>
                <th className="px-3 py-2.5 font-medium">DEX</th>
                <th className="px-3 py-2.5 font-medium">Gas Fee</th>
                <th className="px-3 py-2.5 font-medium">Age</th>
                <th className="px-3 py-2.5 font-medium">Tx</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">
                  No high-gas buys caught yet
                </td></tr>
              ) : items.map((r: any, i: number) => (
                <tr key={i} className="border-b border-border-soft hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-text">{r.symbol}</span>
                      {r.symbol && <CopyButton value={r.symbol} />}
                    </div>
                  </td>
                  <td className="px-3 py-3"><span className="text-text-muted">{r.name || "—"}</span></td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs text-accent-blue">{shortAddr(r.address)}</span>
                      <CopyButton value={r.address} />
                      {r.gmgn_url && (
                        <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer" title="View on GMGN"
                           className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-3"><Badge variant="purple">{(r.dex || "—").toUpperCase()}</Badge></td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs font-semibold text-accent-amber">{fmtEth(r.fee_eth)}</span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="text-text-muted">{r.age_seconds != null ? `${r.age_seconds}s` : "—"}</span>
                  </td>
                  <td className="px-3 py-3">
                    {r.tx_hash ? (
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-text-muted">{shortAddr(r.tx_hash, 8, 6)}</span>
                        <CopyButton value={r.tx_hash} />
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-3"><span className="text-text-muted">{r.created_at ? timeAgo(r.created_at) : "—"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

/* ── 5-6: cross-chain matches (SOL→RBH, SOL→ETH) ────────────────────────── */

function CrossChainSection({ flow, title }: { flow: "eth" | "rbh"; title: string }) {
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const { data: datesData } = useApi<any>(`/api/alerts/crosschain/dates?flow=${flow}`);
  const { data } = useApi<any>(
    `/api/alerts/crosschain?flow=${flow}${q ? `&q=${encodeURIComponent(q)}` : ""}${date ? `&date=${date}` : ""}`
  );

  return (
    <Card>
      <SectionHead title={title} icon={<ArrowRightLeft size={14} />} count={data?.total ?? 0}>
        <SearchBox value={q} onChange={setQ} placeholder="symbol / address / dex" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </SectionHead>
      <CardContent className="pt-0">
        <CrossChainTable items={data?.items ?? []} showFee={flow === "eth"} />
      </CardContent>
    </Card>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */

export default function DetectionsPage() {
  return (
    <div className="space-y-5">
      <PageHeader
        title="Detections"
        subtitle="Premium-caller addresses, ETH gas fees and cross-chain matches"
      />
      <PremiumSection chain="rbh" title="Robinhood Address Detected From Premium Caller" />
      <PremiumSection chain="eth" title="ETH Address Detected From Premium Caller" />
      <PremiumSection chain="sol" title="SOL Address Detected From Premium Caller" />
      <GasSection />
      <CrossChainSection flow="rbh" title="SOL to RBH — Cross-Chain Matches" />
      <CrossChainSection flow="eth" title="SOL to ETH — Cross-Chain Matches" />
    </div>
  );
}
