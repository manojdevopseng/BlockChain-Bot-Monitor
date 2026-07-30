"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { CopyButton } from "@/components/CopyButton";
import { fmtDateTime } from "@/lib/utils";
import { Stat } from "./shared";

// Proves the X side works on its own, with the model out of the picture. The
// two halves fail for completely different reasons — no credits versus a dead
// Nitter instance — and a decisions table cannot tell them apart.
// The live watch freezes the moment a launch crosses the bar, so the figure the
// chat was sent never moves afterwards. That leaves "how far did it actually
// go" unanswered on purpose — this answers it, one token at a time, on a click.
// pump.fun publishes its own all-time high per token; checked against our own
// numbers they agree to within a rounding error where both exist.
export function McapCheck() {
  const [address, setAddress] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const { data, error, isLoading } = useApi<any>(
    asked ? `/api/ai/mcap?address=${encodeURIComponent(asked)}` : null,
    // A one-off question, not a feed: asking again is a click, not a timer.
    { refreshInterval: 0, revalidateOnFocus: false },
  );

  const clean = address.trim();
  const usable = clean.length >= 32 && clean.length <= 64;
  const check = () => { if (usable) setAsked(clean); };

  return (
    <CollapsibleSection
      id="ai-mcap-check"
      title="Market cap — check a token"
      icon={<Search size={14} />}
    >
      <p className="mb-3 text-xs text-text-dim">
        Paste a token address for its all-time high. The agent's own figure is
        frozen at the moment a launch crossed the bar, so it answers what was
        worth sending, not how far the token ran — this answers the second one.
      </p>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") check(); }}
          placeholder="Token address"
          className="w-full max-w-[420px] font-mono text-xs"
        />
        <Button size="sm" onClick={check} disabled={!usable || isLoading}>
          {isLoading ? "Checking…" : "Check"}
        </Button>
        {clean && !usable ? (
          <span className="text-xs text-text-dim">
            that is not a Solana address
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-lg border border-border bg-bg-soft p-4 text-sm text-accent-amber">
          {String(error.message || error)}
        </div>
      ) : data && data.ok === false ? (
        <div className="rounded-lg border border-border bg-bg-soft p-4 text-sm text-text-muted">
          {data.error}
        </div>
      ) : data ? (
        <div className="rounded-lg border border-border bg-bg-soft p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <a href={`https://gmgn.ai/sol/token/${data.address}`}
               target="_blank" rel="noopener noreferrer"
               className="font-semibold text-brand-soft hover:underline">
              {data.symbol || "?"}
            </a>
            <span className="text-sm text-text-dim">{data.name}</span>
            <CopyButton value={data.address} />
            {data.complete ? <Badge variant="purple">graduated</Badge> : null}
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="All-time high"
                  value={data.ath_market_cap_usd}
                  sub={data.ath_at ? fmtDateTime(data.ath_at) : undefined}
                  strong />
            <Stat label="Now" value={data.market_cap_usd} />
            <Stat label={`Our peak (first ${data.our_watch_seconds ?? 60}s)`}
                  value={data.our_peak_usd}
                  sub={data.our_peak_usd ? undefined : "not one of ours"} />
          </div>
        </div>
      ) : null}
    </CollapsibleSection>
  );
}
