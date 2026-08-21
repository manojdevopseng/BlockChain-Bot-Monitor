"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Shield, ShieldCheck, ShieldOff, X } from "lucide-react";
import { apiSend, useApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Quick Buy / Sell settings, in the shape a trader already knows from GMGN —
   but one panel per chain, because that is what they actually are.
 *
 * A buy is sized in the coin that leaves the wallet: ETH on Ethereum, BNB on
 * BNB Chain, SOL on Solana. Nobody holds dollars in a wallet, so sizing in
 * dollars meant the number on this panel was never the number that moved. The
 * dollar figure is worked out from it afterwards, for the P&L, which is the
 * one place every chain has to share a unit.
 *
 * Gas follows the same logic. Five Gwei is ordinary on Ethereum and absurd on
 * Robinhood; Solana does not price in Gwei at all. One shared field was a
 * single number pretending to be five.
 *
 * Account-wide risk stays account-wide: the master auto-buy switch, which
 * callers to follow, how many positions, the daily loss limit. Those are
 * decisions about the account, not about a chain. */

const CHAINS = [
  { id: "rbh", label: "RBH" },
  { id: "eth", label: "ETH" },
  { id: "bnb", label: "BNB" },
  { id: "base", label: "BASE" },
  { id: "sol", label: "SOL" },
  { id: "tron", label: "TRON" },
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
               className="h-9 pr-14 text-sm" />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2
                           text-[11px] text-text-dim">{suffix}</span>
        )}
      </span>
      {hint && <span className="mt-1 block text-[10px] text-text-dim">{hint}</span>}
    </label>
  );
}

function Toggle({ title, note, on, onChange, disabled, icon }: {
  title: string; note: string; on: boolean; onChange: (v: boolean) => void;
  disabled?: boolean; icon?: React.ReactNode;
}) {
  return (
    <label className={`flex items-center justify-between gap-3 rounded-lg border
                       border-border bg-bg-soft/50 px-3 py-2.5 ${
                         disabled ? "opacity-60" : ""}`}>
      <span className="flex items-start gap-2">
        {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
        <span>
          <span className="block text-sm text-text">{title}</span>
          <span className="block text-[11px] leading-snug text-text-dim">{note}</span>
        </span>
      </span>
      <input type="checkbox" checked={on} disabled={disabled}
             onChange={(e) => onChange(e.target.checked)}
             className="h-4 w-4 shrink-0 accent-[var(--brand)]" />
    </label>
  );
}

export function QuickSettings(
  { conf, onClose, onSaved }:
  { conf: Conf; onClose: () => void; onSaved: (c: Conf) => void },
) {
  const [tab, setTab] = useState<"buy" | "sell" | "account">("buy");
  const [chain, setChain] = useState("rbh");
  const [draft, setDraft] = useState<Conf>(conf);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [mounted, setMounted] = useState(false);

  // The starred groups, to tick the ones auto-buy is allowed to follow. Same
  // request the per-caller table makes, so SWR serves it from cache here.
  const { data: callerData } = useApi<any>("/api/trading/callers");
  const available: any[] = callerData?.available ?? [];
  const picked: number[] = (draft.callers || []).map(Number);

  // Whether each relay is actually reachable, asked of the server rather than
  // assumed. A switch reading "protected" over a dead relay is worse than no
  // switch: the order still goes out, the ordinary way, showing green.
  const { data: mevData } = useApi<any>("/api/trading/mev", { refreshInterval: 0 });
  const relays: Record<string, any> = Object.fromEntries(
    (mevData?.items ?? []).map((r: any) => [r.chain, r]));
  // Credential-free relays a browser wallet can be pointed at. Only the
  // chains where switching changes something appear here.
  const networks: Record<string, any> = mevData?.wallet_networks ?? {};

  // Per-chain values start from what the server resolved — defaults layered
  // under whatever this account changed — and are edited in place.
  const [cc, setCc] = useState<Record<string, any>>(
    () => ({ ...(conf.chains_conf_resolved || {}) }));

  useEffect(() => setMounted(true), []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);

  const here = cc[chain] || {};
  const isSol = chain === "sol";
  // Tron pays in bandwidth and energy rather than a gas price, and an account
  // with enough frozen TRX pays neither. There is nothing to set.
  const noGas = chain === "tron";
  const native = here.native || "";
  const relay = relays[chain] || {};

  function set(k: string, v: any) { setDraft((d) => ({ ...d, [k]: v })); }
  function setHere(k: string, v: any) {
    setCc((c) => ({ ...c, [chain]: { ...(c[chain] || {}), [k]: v } }));
  }

  function setPreset(i: number, v: string) {
    const next = [...(here.sell_presets || [])];
    next[i] = Number(v) || 0;
    setHere("sell_presets", next);
  }

  function toggleCaller(id: number) {
    set("callers", picked.includes(id)
      ? picked.filter((x) => x !== id)
      : [...picked, id]);
  }

  async function save() {
    setBusy(true);
    setErr("");
    try {
      // Only the chain being edited is sent; the server merges it over the
      // others, so saving from the Solana panel cannot wipe Ethereum.
      const block: Conf = {
        buy_amount: Number(here.buy_amount) || 0,
        buy_slippage: Number(here.buy_slippage) || 0,
        sell_slippage: Number(here.sell_slippage) || 0,
        sell_presets: (here.sell_presets || []).map(Number).filter(Boolean),
        take_profit_pct: Number(here.take_profit_pct) || 0,
        stop_loss_pct: Number(here.stop_loss_pct) || 0,
        trailing_pct: Number(here.trailing_pct) || 0,
        mev_protect: !!here.mev_protect,
      };
      if (isSol) block.priority_fee = Number(here.priority_fee) || 0;
      else if (!noGas) {
        block.buy_gas_gwei = Number(here.buy_gas_gwei) || 0;
        block.sell_gas_gwei = Number(here.sell_gas_gwei) || 0;
      }

      const body: Conf = {
        auto_buy: !!draft.auto_buy,
        max_open: Number(draft.max_open) || 1,
        daily_buys: Number(draft.daily_buys) || 1,
        chains: draft.chains,
        callers: picked,
        auto_buy_gas: !!draft.auto_buy_gas,
        sell_check: !!draft.sell_check,
        tg_alerts: !!draft.tg_alerts,
        auto_sell: !!draft.auto_sell,
        loss_limit_on: !!draft.loss_limit_on,
        loss_limit_pct: Number(draft.loss_limit_pct) || 0,
        chains_conf: { [chain]: block },
      };
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

        {/* Which chain is being configured. Above the tabs because it changes
            what every field below means — the amount is in a different coin
            and the gas is on a different scale. */}
        <div className="mb-3">
          <span className="mb-1.5 block text-[11px] text-text-dim">
            Settings for
          </span>
          <div className="grid grid-cols-3 gap-1 rounded-lg border border-border
                          bg-bg-soft p-1 sm:grid-cols-6">
            {CHAINS.map((c) => (
              <button key={c.id} onClick={() => setChain(c.id)}
                className={`rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors ${
                  chain === c.id ? "bg-brand/20 text-brand-soft"
                                 : "text-text-dim hover:text-text"
                }`}>
                {c.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mb-4 grid grid-cols-3 gap-1 rounded-lg border border-border bg-bg-soft p-1">
          {(["buy", "sell", "account"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                tab === t ? "bg-brand/20 text-brand-soft" : "text-text-dim hover:text-text"
              }`}>
              {t === "account" ? "Account" : `${t} · ${chain.toUpperCase()}`}
            </button>
          ))}
        </div>

        {tab === "buy" ? (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Amount per buy" suffix={native} value={here.buy_amount}
                     onChange={(v) => setHere("buy_amount", v)}
                     hint={`Spent in ${native}, straight from the wallet`} />
              <Field label="Slippage" suffix="%" value={here.buy_slippage}
                     onChange={(v) => setHere("buy_slippage", v)}
                     hint="Held for live trading" />
            </div>

            {isSol ? (
              <Field label="Priority fee" suffix="µlamports" value={here.priority_fee}
                     onChange={(v) => setHere("priority_fee", v)}
                     hint="Per compute unit — Solana's version of gas" />
            ) : noGas ? (
              <p className="rounded-lg border border-border bg-bg-soft/40 px-3 py-2
                            text-[10px] leading-relaxed text-text-dim">
                Tron charges bandwidth and energy rather than a gas price, and an
                account with enough frozen TRX pays neither. There is nothing to
                set here.
              </p>
            ) : (
              <Field label="Buy gas" suffix="Gwei" value={here.buy_gas_gwei}
                     onChange={(v) => setHere("buy_gas_gwei", v)}
                     hint="Held for live trading" />
            )}

            <MevToggle chain={chain} here={here} relay={relay} setHere={setHere}
                       network={networks[chain]} />
          </div>
        ) : tab === "sell" ? (
          <div className="flex flex-col gap-3">
            <div>
              <span className="mb-1.5 block text-[11px] text-text-dim">
                Sell buttons — the percentages offered on a position
              </span>
              <div className="grid grid-cols-4 gap-2">
                {[0, 1, 2, 3].map((i) => (
                  <span key={i} className="relative">
                    <Input value={(here.sell_presets || [])[i] ?? ""}
                           onChange={(e) => setPreset(i, e.target.value)}
                           className="h-9 pr-6 text-center text-sm" />
                    <span className="pointer-events-none absolute right-2 top-1/2
                                     -translate-y-1/2 text-[11px] text-text-dim">%</span>
                  </span>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <Field label="Take profit" suffix="%" value={here.take_profit_pct}
                     onChange={(v) => setHere("take_profit_pct", v)} hint="0 = off" />
              <Field label="Stop loss" suffix="%" value={here.stop_loss_pct}
                     onChange={(v) => setHere("stop_loss_pct", v)} hint="0 = off" />
              <Field label="Trailing" suffix="%" value={here.trailing_pct}
                     onChange={(v) => setHere("trailing_pct", v)} hint="0 = off" />
            </div>
            <p className="rounded-lg border border-border bg-bg-soft/40 px-3 py-2
                          text-[10px] leading-relaxed text-text-dim">
              These levels are {chain.toUpperCase()}&apos;s own — the master auto-sell
              switch is on the Account tab. The trailing stop arms only once a position
              has actually been in profit, and never fires on the way up from the entry.
            </p>

            <Field label="Sell slippage" suffix="%" value={here.sell_slippage}
                   onChange={(v) => setHere("sell_slippage", v)}
                   hint="Held for live trading" />
            {!isSol && !noGas && (
              <Field label="Sell gas" suffix="Gwei" value={here.sell_gas_gwei}
                     onChange={(v) => setHere("sell_gas_gwei", v)}
                     hint="Held for live trading" />
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <Toggle title="Auto-buy starred callers" on={!!draft.auto_buy}
                    onChange={(v) => set("auto_buy", v)}
                    note="When a caller below names a token, open a position for it,
                          sized by that chain's own Buy tab." />

            <Toggle title="Auto-sell" on={!!draft.auto_sell}
                    onChange={(v) => set("auto_sell", v)}
                    note="Close a position by itself when one of its chain's rules is
                          hit. Checked once a minute, whether or not this page is open." />

            <div className="grid grid-cols-2 gap-3">
              <Field label="Max open positions" value={draft.max_open}
                     onChange={(v) => set("max_open", v)} />
              <Field label="Automatic buys per day" value={draft.daily_buys}
                     onChange={(v) => set("daily_buys", v)} />
            </div>

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

            {/* Whose calls to follow. Nothing ticked means every starred caller —
                said here, because an empty list looks like "none" otherwise. */}
            <div>
              <span className="mb-1.5 flex items-baseline justify-between gap-2">
                <span className="text-[11px] text-text-dim">Callers to follow</span>
                {picked.length > 0 && (
                  <button onClick={() => set("callers", [])}
                          className="text-[10px] text-text-dim hover:text-text">
                    clear — follow all starred
                  </button>
                )}
              </span>
              {available.length === 0 ? (
                <p className="rounded-lg border border-border bg-bg-soft/50 px-3 py-2.5
                              text-[11px] leading-snug text-text-dim">
                  No starred callers yet. Star a group in Forwarder → Premium Groups
                  and it appears here.
                </p>
              ) : (
                <>
                  <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border
                                  border-border bg-bg-soft/40 p-1.5">
                    {available.map((c) => {
                      const on = picked.includes(Number(c.id));
                      return (
                        <button key={c.id} onClick={() => toggleCaller(Number(c.id))}
                          className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5
                                      text-left text-[11px] transition-colors ${
                            on ? "bg-brand/15 text-brand-soft" : "text-text-dim hover:text-text"
                          }`}>
                          <span className={`grid h-3.5 w-3.5 shrink-0 place-items-center rounded
                                            border ${on ? "border-brand bg-brand/30" : "border-border"}`}>
                            {on && <Check size={9} />}
                          </span>
                          <span className="truncate">{c.name}</span>
                          {c.username && (
                            <span className="ml-auto shrink-0 opacity-60">@{c.username}</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  <span className="mt-1 block text-[10px] text-text-dim">
                    {picked.length === 0
                      ? "Nothing ticked — every starred caller is followed."
                      : `Only these ${picked.length} are followed.`}
                  </span>
                </>
              )}
            </div>

            <Toggle title="Also auto-buy ETH gas-fee tokens" on={!!draft.auto_buy_gas}
                    onChange={(v) => set("auto_buy_gas", v)}
                    note="A second source, armed separately from callers. Nobody has
                          vouched for these, so each one is queued and only bought
                          once the check below says it can be sold again." />

            <Toggle title="Sellability check on gas-fee tokens" on={!!draft.sell_check}
                    onChange={(v) => set("sell_check", v)}
                    note="Before buying an ETH gas-fee token, check the pool has real
                          sells in it. Buys with nobody getting out is what a honeypot
                          looks like from outside." />

            <Toggle title="Daily loss limit" on={!!draft.loss_limit_on}
                    onChange={(v) => set("loss_limit_on", v)}
                    note="Turn auto-buy off by itself once the day is down by the
                          percentage below. The day runs midnight to midnight IST." />
            {draft.loss_limit_on && (
              <Field label="Stop the day at" suffix="%" value={draft.loss_limit_pct}
                     onChange={(v) => set("loss_limit_pct", v)}
                     hint="Measured against what today's positions cost, from midnight IST." />
            )}

            <Toggle title="Telegram alerts" on={!!draft.tg_alerts}
                    onChange={(v) => set("tg_alerts", v)}
                    note="Every buy and sell on this account, in your own Telegram.
                          Connect one in Alert Rules — nothing is sent until you do,
                          and it only ever goes to your own chat." />
          </div>
        )}

        <div className="mt-4 flex items-start gap-2 rounded-lg border border-accent-amber/30
                        bg-accent-amber/10 p-3 text-[11px] leading-relaxed text-accent-amber">
          <ShieldCheck size={14} className="mt-0.5 shrink-0" />
          <span>
            Positions are recorded, not executed — so slippage, gas and the MEV route
            are stored here for the day this runs live, and do nothing today. Amounts,
            rules, callers and limits are all live now.
          </span>
        </div>

        {err && <p className="mt-2 text-[11px] text-accent-red">{err}</p>}

        <div className="mt-4 flex gap-2">
          <Button variant="primary" size="sm" disabled={busy} onClick={save}
                  className="flex-1 justify-center">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            Save {chain.toUpperCase()}
          </Button>
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* MEV protection, told honestly per chain.
 *
 * On a chain with a public mempool this is the difference between paying the
 * price you saw and paying whatever a bot leaves you. On a chain that orders
 * through one sequencer there is no mempool to be watched in, so the switch
 * would be theatre — it is disabled and says why, rather than sitting there
 * green and implying a protection nobody is providing. */
function MevToggle({ chain, here, relay, setHere, network }: {
  chain: string; here: any; relay: any; setHere: (k: string, v: any) => void;
  network?: any;
}) {
  const supported = !!here.mev_supported;
  const on = supported && !!here.mev_protect;
  const reachable = !!relay.reachable;
  const [switching, setSwitching] = useState("");

  /* The setting above governs orders this app sends. It cannot govern the
     ones your own wallet sends — MetaMask broadcasts through whatever RPC it
     has for that chain, and no dApp setting changes that. The only way is to
     ask the wallet to use a protected endpoint, which it will do if the
     person agrees. That is what this button does, and why it needs its own
     explanation rather than being folded into the toggle. */
  async function useProtectedRpc() {
    setSwitching(chain);
    try {
      const w = (window as any).ethereum;
      if (!w) throw new Error("No wallet extension found in this browser");
      await w.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: network.chain_id,
          chainName: network.name,
          rpcUrls: [network.rpc],
          blockExplorerUrls: [network.explorer],
          nativeCurrency: { name: network.symbol, symbol: network.symbol,
                            decimals: network.decimals },
        }],
      });
    } catch { /* declining is an answer, and an allowed one */ }
    finally { setSwitching(""); }
  }

  return (
    <div>
      <Toggle
        title="MEV protection"
        on={on}
        disabled={!supported}
        onChange={(v) => setHere("mev_protect", v)}
        icon={on ? <Shield size={14} className="text-accent-green" />
                 : <ShieldOff size={14} className="text-text-dim" />}
        note={supported
          ? `Send through ${relay.relay || "a private relay"} instead of the public
             mempool, so the order cannot be read and raced before it lands.`
          : relay.note || "No protected route for this chain."}
      />
      {/* Your own wallet's orders, which the setting above cannot reach. */}
      {network && (
        <div className="mt-1.5 rounded-lg border border-border-soft bg-bg-soft/30 px-3 py-2">
          <p className="text-[10px] leading-relaxed text-text-dim">
            The switch above covers orders <b>this app</b> sends. When you trade
            from your own MetaMask, it broadcasts through its own RPC and no
            setting here can change that — the wallet has to be asked.
          </p>
          <button onClick={useProtectedRpc} disabled={!!switching}
                  title={`Ask MetaMask to use ${network.relay} for this chain`}
                  className="mt-1.5 inline-flex items-center gap-1.5 rounded-md border
                             border-accent-green/40 px-2 py-1 text-[11px] font-medium
                             text-accent-green transition-colors hover:bg-accent-green/10
                             disabled:opacity-40">
            {switching ? <Loader2 size={12} className="animate-spin" />
                       : <Shield size={12} />}
            Protect my wallet on {chain.toUpperCase()}
          </button>
          <p className="mt-1 text-[10px] leading-relaxed text-text-dim">
            {network.why} You can undo it in MetaMask by switching the network
            back.
          </p>
        </div>
      )}

      {supported && on && (
        <p className="mt-1 flex items-center gap-1.5 px-1 text-[10px] text-text-dim">
          <span className={`h-1.5 w-1.5 rounded-full ${
            reachable ? "bg-accent-green" : "bg-accent-red"}`} />
          {reachable
            ? `${relay.relay} is answering`
            : `${relay.relay || "The relay"} is not answering right now${
                relay.why ? ` — ${relay.why}` : ""}`}
        </p>
      )}
    </div>
  );
}
