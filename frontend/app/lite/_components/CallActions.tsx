"use client";

import { useState } from "react";
import { mutate } from "swr";
import { Clock, Gauge, Loader2 } from "lucide-react";
import { apiSend } from "@/lib/api";
import { TradeButton } from "@/components/features/TradeButton";
import { fmtUsd } from "@/lib/utils";

/* What you do with a called token, under its name: buy it, and find out what
   it is worth.
 *
 * The market cap is asked for rather than fetched with the table. A hundred
 * rows would be a hundred lookups on every poll, and most of them are for
 * tokens nobody is going to act on. So the button asks once, the answer is
 * written onto the row, and from then on it is simply there — for this page,
 * for the next person to open it, and for every call on the same token.
 *
 * The reading comes from the Market Cap feature's own check, which owns the
 * daily allowance and the per-chain switches. One question, one answer, one
 * place that decides it. */

export function CallActions(
  { chain, address, symbol, name, mcap, mcapAt, mcapCall }: {
    chain?: string; address: string; symbol?: string; name?: string;
    mcap?: number; mcapAt?: number; mcapCall?: number;
  },
) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [value, setValue] = useState<number | undefined>(mcap);

  // A row re-rendered from fresher data should show that, not what this
  // component happened to fetch earlier.
  const shown = mcap ?? value;

  async function check() {
    setBusy(true);
    setErr("");
    try {
      const got: any = await apiSend("/api/calls/mcap", "POST", { chain, address });
      setValue(got?.mcap);
      // Every view of this feed, not just this row: the figure was written to
      // the other calls on this token too.
      mutate((k) => typeof k === "string" && k.startsWith("/api/calls"));
    } catch (e: any) {
      setErr(String(e?.message || e).replace(/^Error:\s*/, ""));
      setTimeout(() => setErr(""), 8000);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <TradeButton chain={chain} address={address} symbol={symbol} name={name} />

        {/* What it was worth when the caller spoke. Stamped once, never
            re-read — it is the number the caller is judged on. */}
        {mcapCall ? (
          <span title="Market cap when this token was called"
                className="inline-flex items-center gap-1 rounded-md border
                           border-border-soft px-1.5 py-0.5 text-[11px]
                           tabular-nums text-text-dim">
            <Clock size={10} /> {fmtUsd(mcapCall)}
          </span>
        ) : null}

        {(
          shown ? (
            <button onClick={check} disabled={busy}
                    title={mcapAt
                      ? `Market cap now, read ${new Date(mcapAt * 1000).toLocaleTimeString("en-GB")} — click to read it again`
                      : "Click to read it again"}
                    className="inline-flex items-center gap-1 rounded-md border
                               border-border px-1.5 py-0.5 text-[11px] tabular-nums
                               text-text-muted transition-colors hover:border-brand/40
                               hover:text-brand-soft disabled:opacity-50">
              {busy ? <Loader2 size={10} className="animate-spin" /> : <Gauge size={10} />}
              {fmtUsd(shown)}
            </button>
          ) : (
            <button onClick={check} disabled={busy}
                    title="Read this token's market cap"
                    className="inline-flex items-center gap-1 rounded-md border
                               border-border px-1.5 py-0.5 text-[11px] text-text-dim
                               transition-colors hover:border-brand/40
                               hover:text-brand-soft disabled:opacity-50">
              {busy ? <Loader2 size={10} className="animate-spin" /> : <Gauge size={10} />}
              MC
            </button>
          )
        )}
      </div>
      {err && <span className="max-w-[24ch] text-[10px] leading-tight text-accent-red">{err}</span>}
    </div>
  );
}
