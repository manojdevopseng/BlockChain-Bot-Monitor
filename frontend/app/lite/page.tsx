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

      {/* The window itself never scrolls — the two panels do, each inside its
          own frame. Below xl they stack and the page scrolls instead, because
          two full-height panels on a phone would leave neither of them usable. */}
      <main className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-4
                       sm:px-6 sm:py-5 xl:overflow-hidden">
        <AddPremiumGroup className="shrink-0" />

        {/* Two panels, not a stack: the tracker is read alongside the table,
            not after it. Equal height by the grid, not by numbers either of
            them carries — so they stay level whatever the screen is. */}
        <div className="grid gap-4 xl:min-h-0 xl:flex-1
                        xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          <section className="flex min-h-[420px] flex-col overflow-hidden rounded-xl
                              border border-border bg-bg-card/60 xl:min-h-0">
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
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
            <div className="min-h-0 flex-1">
              <CallsTable items={items} showChain={chain === "all"} fill />
            </div>
          </section>

          <div className="min-h-[420px] xl:min-h-0">
            <TgTracker chain={chain} q={query} />
          </div>
        </div>
      </main>
    </div>
  );
}
