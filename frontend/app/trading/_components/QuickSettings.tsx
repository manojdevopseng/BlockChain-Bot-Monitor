"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, X } from "lucide-react";
import { apiSend } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Quick Buy / Sell settings, in the shape a trader already knows from GMGN:
   two tabs, the sell presets across the top, then slippage and gas.

   Two of these fields do something today and the rest are held for the day
   this executes for real. That is said out loud on the panel rather than shown
   as a greyed-out field — a control that looks live and is not is worse than
   one that admits it. */

const CHAINS = [
  { id: "rbh", label: "RBH" },
  { id: "eth", label: "ETH" },
  { id: "bnb", label: "BNB" },
  { id: "base", label: "BASE" },
  { id: "sol", label: "SOL" },
];

type Conf = Record<string, any>;

function Field({ label, value, onChange, suffix, hint }: {
  label: string; value: any; onChange: (v: string) => void;
  suffix?: string; hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-text-dim">{label}</span>
      <span className="relative block">
        <Input value={value ?? ""} onChange={(e) => onChange(e.target.value)}
               className="h-9 pr-12 text-sm" />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2
                           text-[11px] text-text-dim">{suffix}</span>
        )}
      </span>
      {hint && <span className="mt-1 block text-[10px] text-text-dim">{hint}</span>}
    </label>
  );
}

export function QuickSettings(
  { conf, onClose, onSaved }:
  { conf: Conf; onClose: () => void; onSaved: (c: Conf) => void },
) {
  const [tab, setTab] = useState<"buy" | "sell">("buy");
  const [draft, setDraft] = useState<Conf>(conf);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);

  function set(k: string, v: any) { setDraft((d) => ({ ...d, [k]: v })); }

  function setPreset(i: number, v: string) {
    const next = [...(draft.sell_presets || [])];
    next[i] = Number(v) || 0;
    set("sell_presets", next);
  }

  async function save() {
    setBusy(true);
    setErr("");
    try {
      const body: Conf = {
        auto_buy: !!draft.auto_buy,
        buy_usd: Number(draft.buy_usd) || 0,
        max_open: Number(draft.max_open) || 1,
        daily_buys: Number(draft.daily_buys) || 1,
        chains: draft.chains,
        buy_slippage: Number(draft.buy_slippage) || 0,
        sell_slippage: Number(draft.sell_slippage) || 0,
        buy_gas_gwei: Number(draft.buy_gas_gwei) || 0,
        sell_gas_gwei: Number(draft.sell_gas_gwei) || 0,
        sell_presets: (draft.sell_presets || []).map(Number).filter(Boolean),
      };
      if (String(draft.gmgn_key || "").trim()) body.gmgn_key = draft.gmgn_key;
      const r: any = await apiSend("/api/trading/settings", "PATCH", body);
      onSaved(r.settings);
      onClose();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  if (!mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center bg-black/60 p-4"
         onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
           className="max-h-[88vh] w-full max-w-lg overflow-y-auto rounded-xl border
                      border-border bg-bg-card p-4 shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text">Quick Buy / Sell Settings</h2>
          <button onClick={onClose}
                  className="grid h-6 w-6 place-items-center rounded text-text-dim hover:text-text">
            <X size={14} />
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg border border-border bg-bg-soft p-1">
          {(["buy", "sell"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                tab === t ? "bg-brand/20 text-brand-soft" : "text-text-dim hover:text-text"
              }`}>
              {t} settings
            </button>
          ))}
        </div>

        {tab === "buy" ? (
          <div className="flex flex-col gap-3">
            <label className="flex items-center justify-between rounded-lg border border-border
                              bg-bg-soft/50 px-3 py-2.5">
              <span>
                <span className="block text-sm text-text">Auto-buy starred callers</span>
                <span className="block text-[11px] text-text-dim">
                  When an Important Caller names a token, open a position for it.
                </span>
              </span>
              <input type="checkbox" checked={!!draft.auto_buy}
                     onChange={(e) => set("auto_buy", e.target.checked)}
                     className="h-4 w-4 accent-[var(--brand)]" />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Amount per buy" suffix="USD" value={draft.buy_usd}
                     onChange={(v) => set("buy_usd", v)} />
              <Field label="Max open positions" value={draft.max_open}
                     onChange={(v) => set("max_open", v)} />
              <Field label="Automatic buys per day" value={draft.daily_buys}
                     onChange={(v) => set("daily_buys", v)} />
              <Field label="Slippage" suffix="%" value={draft.buy_slippage}
                     onChange={(v) => set("buy_slippage", v)}
                     hint="Held for live trading" />
            </div>

            <Field label="Buy gas" suffix="Gwei" value={draft.buy_gas_gwei}
                   onChange={(v) => set("buy_gas_gwei", v)} hint="Held for live trading" />

            <div>
              <span className="mb-1.5 block text-[11px] text-text-dim">Chains to buy on</span>
              <div className="flex flex-wrap gap-1.5">
                {CHAINS.map((c) => {
                  const on = (draft.chains || []).includes(c.id);
                  return (
                    <button key={c.id}
                      onClick={() => set("chains", on
                        ? (draft.chains || []).filter((x: string) => x !== c.id)
                        : [...(draft.chains || []), c.id])}
                      className={`rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
                        on ? "border-brand/40 bg-brand/15 text-brand-soft"
                           : "border-border text-text-dim hover:text-text"
                      }`}>
                      {c.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div>
              <span className="mb-1.5 block text-[11px] text-text-dim">
                Sell buttons — the percentages offered on a position
              </span>
              <div className="grid grid-cols-4 gap-2">
                {[0, 1, 2, 3].map((i) => (
                  <span key={i} className="relative">
                    <Input value={(draft.sell_presets || [])[i] ?? ""}
                           onChange={(e) => setPreset(i, e.target.value)}
                           className="h-9 pr-6 text-center text-sm" />
                    <span className="pointer-events-none absolute right-2 top-1/2
                                     -translate-y-1/2 text-[11px] text-text-dim">%</span>
                  </span>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Slippage" suffix="%" value={draft.sell_slippage}
                     onChange={(v) => set("sell_slippage", v)}
                     hint="Held for live trading" />
              <Field label="Sell gas" suffix="Gwei" value={draft.sell_gas_gwei}
                     onChange={(v) => set("sell_gas_gwei", v)}
                     hint="Held for live trading" />
            </div>
          </div>
        )}

        <div className="mt-4 rounded-lg border border-accent-amber/30 bg-accent-amber/10 p-3
                        text-[11px] leading-relaxed text-accent-amber">
          Positions are recorded, not executed. GMGN's trading API signs with a
          private key rather than an API key, and serves Solana only — so
          slippage and gas are stored here for the day this runs live, and do
          nothing today. Everything else on this panel is live now.
        </div>

        {err && <p className="mt-2 text-[11px] text-accent-red">{err}</p>}

        <div className="mt-4 flex gap-2">
          <Button variant="primary" size="sm" disabled={busy} onClick={save}
                  className="flex-1 justify-center">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            Save
          </Button>
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
