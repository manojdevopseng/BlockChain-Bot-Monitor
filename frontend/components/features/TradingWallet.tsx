"use client";

import { useState } from "react";
import {
  AlertTriangle, KeyRound, Loader2, Plus, ShieldAlert, Trash2, Wallet,
} from "lucide-react";
import { apiSend, useApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/CopyButton";

/* The trading wallet — the one thing here that can actually spend.
 *
 * Everything else in this app reads. This holds a key, and a key is the
 * difference between software that watches your money and software that has
 * it. The panel is written to make that plain rather than to make it feel
 * routine: the warning is not fine print, the delete needs typing, and
 * creating a wallet is offered before importing one.
 *
 * Why create first. An imported key is usually somebody's main wallet, with
 * everything they own behind it. A wallet made here holds exactly what they
 * chose to send it, so the worst case is a float they picked rather than
 * their whole balance. Both are supported, because people will do both. */

const KIND_LABEL: Record<string, string> = {
  evm: "EVM — Robinhood, Ethereum, BNB, Base",
  sol: "Solana",
  tron: "Tron",
};
const KIND_SHORT: Record<string, string> = {
  evm: "EVM", sol: "Solana", tron: "Tron",
};
const KIND_TONE: Record<string, any> = {
  evm: "blue", sol: "purple", tron: "red",
};

function short(a: string) {
  return a.length > 20 ? `${a.slice(0, 10)}…${a.slice(-8)}` : a;
}

export function TradingWallet() {
  const { data, mutate } = useApi<any>("/api/trading/keys", { refreshInterval: 0 });
  const [kind, setKind] = useState("evm");
  const [secret, setSecret] = useState("");
  const [mode, setMode] = useState<"create" | "import">("create");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const items: any[] = data?.items ?? [];
  const ready = !!data?.vault_ready;
  const have = (k: string) => items.find((i) => i.kind === k);

  async function make() {
    setBusy("make");
    setErr("");
    try {
      await apiSend("/api/trading/keys/create", "POST", { kind });
      await mutate();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(""); }
  }

  async function bring() {
    setBusy("import");
    setErr("");
    try {
      await apiSend("/api/trading/keys/import", "POST",
        { kind, private_key: secret.trim() });
      // Cleared before anything else, including before the list refreshes:
      // the value should not sit in a form field a moment longer than the
      // request needs it.
      setSecret("");
      await mutate();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(""); }
  }

  async function remove(k: string, address: string) {
    const typed = prompt(
      `Delete the ${KIND_SHORT[k]} trading wallet?\n\n` +
      `${address}\n\n` +
      `The key is destroyed and there is no copy anywhere. If this wallet was ` +
      `created here, anything still in it becomes unreachable — move the funds ` +
      `out first.\n\nType DELETE to confirm:`);
    if (typed !== "DELETE") return;
    setBusy(k);
    setErr("");
    try {
      await apiSend(`/api/trading/keys/${k}`, "DELETE");
      await mutate();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(""); }
  }

  if (!ready) {
    return (
      <p className="flex items-start gap-2 rounded-lg border border-accent-amber/30
                    bg-accent-amber/10 px-3 py-2.5 text-[11px] leading-relaxed
                    text-accent-amber">
        <ShieldAlert size={13} className="mt-0.5 shrink-0" />
        <span>
          The key vault is not set up on this server — <code>WALLET_MASTER_KEY</code>{" "}
          is missing, so nothing can be stored. That is deliberate: a server that
          was never meant to hold keys should not start holding them by accident.
        </span>
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Said first, before any control. This is the one panel where the
          consequence should be read before the button is found. */}
      <p className="flex items-start gap-2 rounded-lg border border-accent-red/30
                    bg-accent-red/10 px-3 py-2.5 text-[11px] leading-relaxed
                    text-accent-red">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" />
        <span>
          <b>This wallet can spend without asking you.</b> That is the point —
          auto-buy fires while you are asleep, and nothing can sign then unless a
          key lives on the server. Keep only what you are willing to trade
          automatically in it. Your MetaMask and Phantom wallets are untouched by
          this and stay read-only.
        </span>
      </p>

      {items.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {items.map((w) => (
            <div key={w.kind}
                 className="flex flex-wrap items-center gap-2 rounded-lg border
                            border-border-soft bg-bg-soft/40 px-2.5 py-2">
              <Wallet size={13} className="shrink-0 text-text-dim" />
              <Badge variant={KIND_TONE[w.kind] ?? "gray"}>
                {KIND_SHORT[w.kind] ?? w.kind}
              </Badge>
              <span className="font-mono text-xs text-text">{short(w.address)}</span>
              <CopyButton value={w.address} />
              <Badge variant={w.source === "created" ? "green" : "amber"}>
                {w.source === "created" ? "created here" : "imported"}
              </Badge>
              <button onClick={() => remove(w.kind, w.address)} disabled={!!busy}
                      title="Destroy this key. No copy exists anywhere."
                      className="ml-auto inline-flex items-center gap-1 rounded-md border
                                 border-accent-red/40 px-2 py-1 text-[11px] font-medium
                                 text-accent-red transition-colors hover:bg-accent-red/10
                                 disabled:opacity-40">
                {busy === w.kind ? <Loader2 size={12} className="animate-spin" />
                                 : <Trash2 size={12} />}
                Delete
              </button>
            </div>
          ))}
          <p className="px-1 text-[10px] text-text-dim">
            Send funds to these addresses to trade with them. Only what is in
            them can ever be spent.
          </p>
        </div>
      )}

      <div>
        <span className="mb-1.5 block text-[11px] text-text-dim">Chain</span>
        <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-bg-soft p-1">
          {Object.keys(KIND_SHORT).map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors ${
                kind === k ? "bg-brand/20 text-brand-soft" : "text-text-dim hover:text-text"
              }`}>
              {KIND_SHORT[k]}{have(k) ? " ✓" : ""}
            </button>
          ))}
        </div>
        <span className="mt-1 block text-[10px] text-text-dim">{KIND_LABEL[kind]}</span>
      </div>

      <div className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-bg-soft p-1">
        {(["create", "import"] as const).map((m) => (
          <button key={m} onClick={() => setMode(m)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === m ? "bg-brand/20 text-brand-soft" : "text-text-dim hover:text-text"
            }`}>
            {m === "create" ? "Create a wallet" : "Import a key"}
          </button>
        ))}
      </div>

      {mode === "create" ? (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] leading-relaxed text-text-dim">
            A new wallet is generated on the server and sealed immediately. You
            fund it yourself, so it can only ever lose what you put into it —
            which is why this is offered first.
          </p>
          <Button size="sm" variant="outline" disabled={!!busy} onClick={make}>
            {busy === "make" ? <Loader2 size={13} className="animate-spin" />
                             : <Plus size={13} />}
            Create {KIND_SHORT[kind]} wallet
            {have(kind) ? " (replaces the current one)" : ""}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="flex items-start gap-2 text-[11px] leading-relaxed text-accent-amber">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>
              Import only a wallet you keep a trading float in. If you paste the
              key to your main wallet, this server can spend everything in it —
              not because it will, but because it could.
            </span>
          </p>
          <Input value={secret} onChange={(e) => setSecret(e.target.value)}
                 type="password" autoComplete="off" spellCheck={false}
                 placeholder={kind === "sol" ? "Base58, as your wallet exported it"
                                             : "0x… 64 hex characters"} />
          <Button size="sm" variant="outline"
                  disabled={!secret.trim() || !!busy} onClick={bring}>
            {busy === "import" ? <Loader2 size={13} className="animate-spin" />
                               : <KeyRound size={13} />}
            Import {KIND_SHORT[kind]} key
          </Button>
        </div>
      )}

      {err && <p className="text-[11px] text-accent-red">{err}</p>}

      <p className="text-[10px] leading-relaxed text-text-dim">
        Keys are encrypted with a master key held outside the database, so a
        database copy alone is worthless. Nothing reads a key back out — there
        is no export, no admin view, and no log line that carries one. Over a
        connection without HTTPS the server refuses to accept one at all.
      </p>
    </div>
  );
}
