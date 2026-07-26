"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, BellOff, ExternalLink, Search } from "lucide-react";
import { useApi } from "@/lib/api";
import { CopyButton } from "@/components/CopyButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn, fmtUsd, shortAddr, timeAgo } from "@/lib/utils";

// Chain filter — the same three buttons the reference dashboard had. `value`
// is the `chain` the scanners write on a cross-chain alert.
const FILTERS = [
  { id: "all", label: "All", value: null },
  { id: "eth", label: "ETH", value: "eth" },
  { id: "rbh", label: "RBH", value: "robinhood" },
] as const;

type Alert = {
  token_symbol?: string;
  token_address?: string;
  sol_symbol?: string;
  sol_address?: string;
  sol_mcap_usd?: number;
  chain?: string;
  dex?: string;
  type?: string;
  created_at?: number;
  gmgn_url?: string | null;
  sol_gmgn_url?: string | null;
};

function chainTone(chain?: string) {
  const c = (chain || "").toLowerCase();
  if (c === "eth" || c === "ethereum") return "blue";
  if (c === "robinhood" || c === "rbh") return "green";
  if (c === "sol" || c === "solana") return "purple";
  return "gray";
}

// Short beep + a desktop notification when a new alert lands, so a match is
// noticed without watching the page. Off by default; the choice sticks.
function useArrivalPing(items: Alert[], on: boolean) {
  const lastTs = useRef(0);
  useEffect(() => {
    const newest = items.reduce((m, a) => Math.max(m, a.created_at ?? 0), 0);
    if (!newest) return;
    const previous = lastTs.current;
    lastTs.current = newest;
    if (!on || !previous || newest <= previous) return;
    const a = items[0];
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.value = 0.05;
      osc.start();
      setTimeout(() => { osc.stop(); ctx.close(); }, 180);
    } catch {}
    try {
      if (window.Notification && Notification.permission === "granted") {
        new Notification(`Cross-chain match: ${a?.token_symbol ?? ""}`, {
          body: `${(a?.chain ?? "").toUpperCase()} · ${a?.token_address ?? ""}`,
        });
      }
    } catch {}
  }, [items, on]);
}

export function RecentAlerts({ limit = 100 }: { limit?: number }) {
  const [filter, setFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [notify, setNotify] = useState(false);

  useEffect(() => {
    setNotify(localStorage.getItem("alert_sound") === "1");
  }, []);

  // Debounced so a fast typist doesn't fire a request per keystroke.
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const chain = FILTERS.find((f) => f.id === filter)?.value ?? null;
  const params = new URLSearchParams({ limit: String(limit) });
  if (chain) params.set("chain", chain);
  if (q) params.set("q", q);

  const { data } = useApi<any>(`/api/alerts?${params.toString()}`);
  const { data: chains } = useApi<any>("/api/alerts/chains");
  const items: Alert[] = data?.items ?? [];
  useArrivalPing(items, notify);

  function toggleNotify() {
    const next = !notify;
    setNotify(next);
    try { localStorage.setItem("alert_sound", next ? "1" : "0"); } catch {}
    if (next && window.Notification && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }

  function countFor(f: (typeof FILTERS)[number]): number {
    const counts: Record<string, number> = chains?.counts ?? {};
    if (!f.value) return chains?.total ?? 0;
    return counts[f.value] ?? 0;
  }

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Recent Alerts</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-dim" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="token / address"
              className="h-8 w-44 pl-7 text-xs"
            />
          </div>
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                filter === f.id
                  ? "border-brand/40 bg-brand/15 text-brand-soft"
                  : "border-border text-text-muted hover:bg-bg-hover"
              )}
            >
              {f.label}
              <span className="ml-1.5 text-[10px] text-text-dim">{countFor(f)}</span>
            </button>
          ))}
          <button
            onClick={toggleNotify}
            title={notify ? "Sound & notifications on" : "Muted"}
            className={cn(
              "grid h-7 w-7 place-items-center rounded-md border transition-colors",
              notify
                ? "border-accent-green/40 bg-accent-green/15 text-accent-green"
                : "border-border text-text-dim hover:bg-bg-hover"
            )}
          >
            {notify ? <Bell size={13} /> : <BellOff size={13} />}
          </button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-dim">
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Chain</th>
                <th className="px-3 py-2.5 font-medium">Address</th>
                <th className="px-3 py-2.5 font-medium">DEX</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-10 text-center text-text-dim">
                    {q || chain ? "No alerts match this filter" : "No alerts yet"}
                  </td>
                </tr>
              ) : (
                items.map((a, i) => (
                  <tr key={i} className="border-b border-border-soft hover:bg-bg-hover/40">
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-text">{a.token_symbol || "?"}</span>
                        {a.token_symbol && <CopyButton value={a.token_symbol} />}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[11px] text-text-dim">
                        {a.sol_mcap_usd ? <span>{fmtUsd(a.sol_mcap_usd)}</span> : null}
                        {a.sol_gmgn_url && (
                          <a href={a.sol_gmgn_url} target="_blank" rel="noopener noreferrer"
                             className="text-accent-purple hover:underline">
                            SOL ↗
                          </a>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <Badge variant={chainTone(a.chain) as any}>
                        {(a.chain || "?").toUpperCase()}
                      </Badge>
                    </td>
                    <td className="px-3 py-3">
                      {a.token_address ? (
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-xs text-accent-blue">
                            {shortAddr(a.token_address)}
                          </span>
                          <CopyButton value={a.token_address} />
                          {a.gmgn_url && (
                            <a href={a.gmgn_url} target="_blank" rel="noopener noreferrer"
                               title="View on GMGN"
                               className="inline-grid h-5 w-5 place-items-center rounded text-text-dim hover:text-brand-soft">
                              <ExternalLink size={12} />
                            </a>
                          )}
                        </div>
                      ) : (
                        <span className="text-text-dim">—</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <Badge variant="purple">{a.dex || a.type || "—"}</Badge>
                    </td>
                    <td className="px-3 py-3 text-text-muted">
                      {a.created_at ? timeAgo(a.created_at) : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
