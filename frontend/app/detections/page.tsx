"use client";

import { useState } from "react";
import { Layers, Fuel, ExternalLink, ArrowRightLeft } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DetectionTable } from "@/components/features/DetectionTable";
import { CrossChainTable } from "@/components/features/CrossChainTable";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { FilterTabs, HistorySelect, SearchBox } from "@/components/SectionFilters";
import { DownloadCsv } from "@/components/features/Performance";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { CopyButton } from "@/components/CopyButton";
import { fmtDateTime, fmtEth, shortAddr, rowKey } from "@/lib/utils";
import { Age, TickProvider } from "@/components/Age";

/* ── shared section chrome ─────────────────────────────────────────────── */

/* ── 1-3: premium caller detection panels (RBH / ETH / SOL) ─────────────── */

type PremiumChain = "all" | "rbh" | "eth" | "bnb" | "sol";

const PREMIUM_TABS = [
  { id: "all", label: "All" },
  { id: "rbh", label: "Robinhood" },
  { id: "eth", label: "Ethereum" },
  { id: "bnb", label: "BNB" },
  { id: "sol", label: "Solana" },
] as const satisfies readonly { id: PremiumChain; label: string }[];

// One section for all three chains, filtered rather than three sections that
// were identical apart from which chain they asked for. The chain moves into a
// column so a merged row still says where it came from.
function PremiumSection() {
  const [chain, setChain] = useState<PremiumChain>("all");
  const [q, setQ] = useState("");
  const [multi, setMulti] = useState(false);
  const [date, setDate] = useState("");

  const query = useDebounced(q);

  // The search and Multi filters go to whichever view is showing. They used to
  // be dropped in history mode while the controls stayed on screen, so typing
  // there silently did nothing.
  const filters = `&multi=${multi}${query ? `&q=${encodeURIComponent(query)}` : ""}`;
  const { data: datesData } = useApi<any>(`/api/forwarder/detections/dates?chain=${chain}`);
  const live = useApi<any>(
    date ? null : `/api/forwarder/detections?chain=${chain}${filters}`
  );
  const hist = useApi<any>(
    date ? `/api/forwarder/detections/history?chain=${chain}&date=${date}${filters}` : null
  );
  const data = date ? hist.data : live.data;

  return (
    <CollapsibleSection
      id="premium-detections"
      title="Addresses Detected From Premium Callers"
      count={data?.total ?? 0}
      controls={<>
        <FilterTabs value={chain} onChange={setChain} options={PREMIUM_TABS} />
        <SearchBox value={q} onChange={setQ} placeholder="symbol / name / address / group" />
        <Button size="sm" variant={multi ? "primary" : "outline"} onClick={() => setMulti((v) => !v)}
          title="Only tokens called by 2+ groups">
          <Layers size={13} /> Multi 2+
        </Button>
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        <DownloadCsv path={`/api/forwarder/detections/export.csv?chain=${chain}`}
          filename={`detections-${chain}.csv`} />
      </>}
    >
      <DetectionTable items={data?.items ?? []} showChain={chain === "all"} />
    </CollapsibleSection>
  );
}

/* ── 4: ETH gas fees (per-tx) ───────────────────────────────────────────── */

function GasSection() {
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (date) params.set("date", date);
  const qs = params.toString();

  const { data } = useApi<any>(`/api/rpc/gas/recent${qs ? `?${qs}` : ""}`);
  const { data: datesData } = useApi<any>("/api/rpc/gas/dates");
  const { data: summary } = useApi<any>("/api/rpc/gas");
  const items = data?.items ?? [];

  return (
    <CollapsibleSection
      id="gas"
      title="ETH Gas Fees — High-Gas Early Buys"
      icon={<Fuel size={14} />}
      count={data?.total ?? items.length}
      controls={<>
        <SearchBox value={q} onChange={setQ} placeholder="symbol / address / tx" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        <span className="text-xs text-text-dim">
          threshold <span className="text-text">{fmtEth(summary?.min_fee_eth)}</span>
        </span>
        <span className="text-xs text-text-dim">
          max <span className="text-text">{fmtEth(summary?.max_eth)}</span>
        </span>
        <Badge variant={summary?.enabled ? "green" : "gray"}>{summary?.enabled ? "on" : "off"}</Badge>
      </>}
    >
        <p className="mb-3 text-xs text-text-dim">
          Every new V2/V4 pair is watched; a buy paying ≥ {fmtEth(summary?.min_fee_eth)} gas means
          someone is sniping — it fires one alert and the watch stops.
        </p>
        <TableScroll>
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Symbol</th>
                <th className="px-3 py-2.5 font-medium">Name</th>
                <th className="px-3 py-2.5 font-medium">Age</th>
                <th className="px-3 py-2.5 font-medium">CA</th>
                <th className="px-3 py-2.5 font-medium">DEX</th>
                <th className="px-3 py-2.5 font-medium">Gas Fee</th>
                <th className="px-3 py-2.5 font-medium">Tx</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={8} className="px-3 py-10 text-center text-text-dim">
                  {query ? "No high-gas buy matches this search"
                    : date ? `No high-gas buys on ${date}`
                    : "No high-gas buys caught yet"}
                </td></tr>
              ) : items.map((r: any, i: number) => (
                <tr key={rowKey(r, i)} className="border-b border-border-soft hover:bg-bg-hover/40">
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-text">{r.symbol}</span>
                      {r.symbol && <CopyButton value={r.symbol} />}
                    </div>
                  </td>
                  <td className="px-3 py-3"><span className="text-text-muted">{r.name || "—"}</span></td>
                  {/* Age — ticks every second, same component as the AI page */}
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted"><Age ts={r.created_at} /></span>
                  </td>
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
                    {r.tx_hash ? (
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-text-muted">{shortAddr(r.tx_hash, 8, 6)}</span>
                        <CopyButton value={r.tx_hash} />
                      </span>
                    ) : "—"}
                  </td>
                  {/* When — the absolute time the buy landed */}
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted">
                      {r.created_at ? fmtDateTime(r.created_at) : "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
    </CollapsibleSection>
  );
}

/* ── 5-6: cross-chain matches (SOL→RBH, SOL→ETH) ────────────────────────── */

type Flow = "all" | "rbh" | "eth";

const FLOW_TABS = [
  { id: "all", label: "All" },
  { id: "rbh", label: "SOL to RBH" },
  { id: "eth", label: "SOL to ETH" },
] as const satisfies readonly { id: Flow; label: string }[];

// SOL is always the source side of a cross-chain match, so the only thing the
// filter picks is the destination chain — hence three tabs, not four.
function CrossChainSection() {
  const [flow, setFlow] = useState<Flow>("all");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const { data: datesData } = useApi<any>(`/api/alerts/crosschain/dates?flow=${flow}`);
  const query = useDebounced(q);
  const { data } = useApi<any>(
    `/api/alerts/crosschain?flow=${flow}${query ? `&q=${encodeURIComponent(query)}` : ""}${date ? `&date=${date}` : ""}`
  );

  return (
    <CollapsibleSection
      id="xchain"
      title="Cross-Chain Matches"
      icon={<ArrowRightLeft size={14} />}
      count={data?.total ?? 0}
      controls={<>
        <FilterTabs value={flow} onChange={setFlow} options={FLOW_TABS} />
        <SearchBox value={q} onChange={setQ} placeholder="symbol / address / dex" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </>}
    >
      <CrossChainTable items={data?.items ?? []} showFlow={flow === "all"} />
    </CollapsibleSection>
  );
}

/* ── page ───────────────────────────────────────────────────────────────── */

export default function DetectionsPage() {
  return (
    <TickProvider>
    <div className="space-y-5">
      <PageHeader
        title="Detections"
        subtitle="Premium-caller addresses, cross-chain matches and ETH gas fees"
      />
      <PremiumSection />
      <CrossChainSection />
      <GasSection />
    </div>
    </TickProvider>
  );
}
