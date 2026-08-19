"use client";

import { useContext, useState } from "react";
import Link from "next/link";
import { LayoutDashboard } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { ConnectionContext } from "@/components/layout/Shell";
import { TopbarActions } from "@/components/layout/TopbarActions";
import { FilterTabs, HistorySelect, SearchBox } from "@/components/SectionFilters";
import { DownloadCsv } from "@/components/features/Performance";
import { AddPremiumGroup } from "@/components/features/AddPremiumGroup";
import { CallsTable } from "./_components/CallsTable";
import { TgTracker } from "./_components/TgTracker";

/* The second dashboard. One question — who is calling what, and in what words
   — so it is one screen: the calls on the left, the messages behind them on
   the right, and the box that adds a caller above both.

   It reads the same collections the main dashboard's scanners already fill.
   Nothing here starts a scanner, opens an RPC connection or holds a second
   Telegram session. */

const CHAIN_TABS = [
  { id: "all", label: "All" },
  { id: "rbh", label: "RBH" },
  { id: "eth", label: "ETH" },
  { id: "bnb", label: "BNB" },
  { id: "sol", label: "SOL" },
  { id: "base", label: "BASE" },
] as const;

type Chain = (typeof CHAIN_TABS)[number]["id"];

export default function LiteDashboard() {
  const connected = useContext(ConnectionContext);
  const [chain, setChain] = useState<Chain>("all");
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);

  const params = new URLSearchParams({ chain });
  if (query) params.set("q", query);
  if (date) params.set("date", date);

  const { data } = useApi<any>(`/api/calls?${params}`);
  const { data: dates } = useApi<any>(`/api/calls/dates?chain=${chain}`);
  const { data: stats } = useApi<any>(`/api/calls/stats?chain=${chain}`);

  const items = data?.items ?? [];

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="text-sm font-semibold text-text hover:text-brand-soft">
            2nd Dashboard
          </Link>
          <Link href="/dashboard"
                title="Main Dashboard"
                className="hidden items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-text-dim
                           transition-colors hover:bg-bg-hover hover:text-text sm:flex">
            <LayoutDashboard size={13} /> Main Dashboard
          </Link>
        </div>
        <TopbarActions connected={connected} />
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
        <AddPremiumGroup className="mb-4" />

        {/* Two panels, not a stack: the tracker is read alongside the table,
            not after it. Below xl they stack, because side by side at that
            width leaves neither of them readable. */}
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          <section className="overflow-hidden rounded-xl border border-border bg-bg-card/60">
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
              <h2 className="mr-auto text-sm font-semibold text-text">
                Premium Calls
                <span className="ml-2 text-xs font-normal text-text-dim">
                  {stats?.calls ?? data?.total ?? 0} calls
                  {stats?.tokens ? ` · ${stats.tokens} tokens` : ""}
                  {stats?.callers ? ` · ${stats.callers} callers` : ""}
                </span>
              </h2>
              <FilterTabs value={chain} onChange={setChain} options={CHAIN_TABS} />
              <SearchBox value={q} onChange={setQ} placeholder="symbol / name / address / group" />
              <HistorySelect value={date} onChange={setDate} dates={dates?.dates ?? []} />
              <DownloadCsv path={`/api/calls/export.csv?${params}`}
                           filename={`calls-${chain}.csv`} />
            </div>
            <CallsTable items={items} showChain={chain === "all"} maxHeight={720} />
          </section>

          <div className="min-h-[420px] xl:h-[calc(100vh-13rem)]">
            <TgTracker chain={chain} q={query} />
          </div>
        </div>
      </main>
    </div>
  );
}
