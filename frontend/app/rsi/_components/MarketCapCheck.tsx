"use client";

import { useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtDateTime, shortAddr } from "@/lib/utils";

/* Market Cap Check.
 *
 * The watcher above answers "tell me when it gets there". This one answers
 * "what is it worth right now" — pick a chain, paste an address, read the
 * number. Nothing is stored and nothing is watched, so a look costs one
 * request and leaves nothing behind.
 *
 * Same reader as the watcher, so the two can never disagree about a figure. */

type Chain = { id: string; label: string; enabled: boolean };

function fmtUsd(value: number | null | undefined): string {
  const n = Number(value || 0);
  if (!n) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

export function MarketCapCheck() {
  const { data: chainData } = useApi<any>("/api/mcap/chains");
  const chains: Chain[] = chainData?.items ?? [];

  const [chain, setChain] = useState("rbh");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // Kept in this component only — this is a look, not a record, and a list
  // that survives a refresh would be pretending otherwise.
  const [results, setResults] = useState<any[]>([]);

  async function check() {
    if (!address.trim()) return;
    setBusy(true); setErr("");
    try {
      const got = await apiSend("/api/mcap/check", "POST",
        { chain, address: address.trim() });
      setResults((cur) => [got, ...cur].slice(0, 8));
      setAddress("");
    } catch (e: any) {
      setErr(e?.message || "could not read that token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <CollapsibleSection
      id="mcap-check"
      title="Market Cap Check"
      icon={<Search size={14} />}
      count={results.length}
    >
      <p className="mb-3 text-xs text-text-dim">
        Pick a chain, paste a token address, read its market cap. Supply × price
        off the chain — V2, V3 and V4 pools on RBH / ETH / BSC, and Jupiter on
        SOL. Nothing here is watched or saved; for that, add it to the watcher
        above.
      </p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select value={chain} onChange={(e) => setChain(e.target.value)}
                className="h-8 rounded border border-border bg-bg-soft px-2 text-xs text-text">
          {chains.map((c) => (
            <option key={c.id} value={c.id}>{c.enabled ? c.label : `${c.label} (off)`}</option>
          ))}
        </select>
        <Input value={address} onChange={(e) => setAddress(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") check(); }}
               placeholder="token address" className="h-8 w-[24rem] text-xs" />
        <Button size="sm" onClick={check} disabled={busy || !address.trim()}>
          <Search size={13} className="mr-1" /> {busy ? "Reading…" : "Check"}
        </Button>
        {err && <span className="text-xs text-accent-red">{err}</span>}
      </div>

      {results.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-soft px-3 py-8 text-center text-xs text-text-dim">
          Nothing checked yet — the last few readings appear here.
        </div>
      ) : (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div key={`${r.address}-${r.checked_at}-${i}`}
                 className="rounded-lg border border-border-soft bg-bg-soft/40 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-text">{r.symbol || "?"}</span>
                {r.name && <span className="text-xs text-text-muted">{r.name}</span>}
                <Badge variant="blue">{r.chain_label}</Badge>
                <Badge variant="gray">{r.source}</Badge>
                <span className="ml-auto font-mono text-base text-text">{fmtUsd(r.mcap)}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-text-dim">
                <span className="font-mono">{shortAddr(r.address)}</span>
                <CopyButton value={r.address} />
                {r.gmgn_url && (
                  <a href={r.gmgn_url} target="_blank" rel="noopener noreferrer"
                     className="inline-flex items-center gap-1 hover:text-brand-soft">
                    <ExternalLink size={11} /> GMGN
                  </a>
                )}
                {r.price_usd != null && (
                  <span className="font-mono">${Number(r.price_usd).toPrecision(4)} / token</span>
                )}
                {r.supply != null && (
                  <span className="font-mono">supply {Number(r.supply).toLocaleString()}</span>
                )}
                <span>{r.checked_at ? fmtDateTime(r.checked_at) : ""}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </CollapsibleSection>
  );
}
