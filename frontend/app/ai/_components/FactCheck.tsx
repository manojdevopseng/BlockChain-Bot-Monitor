"use client";

import { useState } from "react";
import { apiSend } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// The reality check, per row, on a click. It is not run for every launch on
// purpose: it costs a model call, and almost every row is one nobody opens.
// The answer is stored server-side, so a row that has been checked shows its
// answer straight away and asking again is deliberate rather than accidental.
export function FactCheck({ row }: { row: any }) {
  const [fact, setFact] = useState<any>(row.fact ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  const ask = async (force: boolean) => {
    setBusy(true);
    setError("");
    try {
      const r = await apiSend<any>(
        `/api/ai/factcheck?address=${encodeURIComponent(row.address)}${force ? "&force=true" : ""}`,
        "POST",
      );
      if (r.ok) { setFact(r); setOpen(true); } else { setError(r.error || "no answer"); }
    } catch (e: any) {
      setError(e?.message || "request failed");
    } finally {
      setBusy(false);
    }
  };

  if (!fact) {
    return (
      <div className="min-w-[110px]">
        <Button size="sm" variant="outline" disabled={busy}
                onClick={() => ask(false)}>
          {busy ? "Checking…" : "Fact check"}
        </Button>
        {error ? (
          <div className="mt-1 max-w-[200px] text-[11px] text-accent-amber">{error}</div>
        ) : null}
      </div>
    );
  }

  const yes = fact.real === "Yes";
  return (
    <div className="min-w-[150px] max-w-[280px]">
      <button type="button" onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 text-left">
        <Badge variant={yes ? "green" : "amber"}>{fact.real}</Badge>
        <span className="text-[11px] text-text-dim underline decoration-dotted">
          {open ? "hide" : "read"}
        </span>
      </button>
      {open ? (
        <div className="mt-2 rounded-lg border border-border bg-bg-soft p-2">
          <p className="text-xs leading-relaxed text-text-muted">{fact.brief}</p>
          {fact.reason ? (
            <p className="mt-1.5 text-[11px] text-text-dim">{fact.reason}</p>
          ) : null}
          <Button size="sm" variant="ghost" className="mt-1.5 h-6 px-2 text-[11px]"
                  disabled={busy} onClick={() => ask(true)}>
            {busy ? "Asking…" : "Ask again"}
          </Button>
          {error ? (
            <div className="mt-1 text-[11px] text-accent-amber">{error}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
