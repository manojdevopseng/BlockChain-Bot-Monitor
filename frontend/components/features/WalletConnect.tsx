"use client";

import { useEffect, useState } from "react";
import { Loader2, Plug, ShieldCheck, Trash2, TriangleAlert, Wallet } from "lucide-react";
import { apiSend, useApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Connecting a wallet, and disconnecting it.
 *
 * All of this happens in the browser. The extension is asked for an address
 * and for a signature over a sentence; the key never leaves it, and nothing
 * here has anywhere to put one if it did. What the server stores is a public
 * address.
 *
 * Two ways in, on purpose. Signing proves the wallet is yours, which is what
 * anything that spends will have to insist on. Pasting does not, and says so
 * on the row for ever — but a hardware wallet, an exchange address, or a page
 * served over plain HTTP where extensions refuse to inject all still need to
 * be watchable.
 *
 * Disconnect deletes the row. There is nothing to revoke on the chain: the
 * wallet was never granted anything, because reading a balance needs no
 * permission from it at all. */

type W = {
  address: string; kind: "evm" | "sol"; source: string;
  verified: boolean; label?: string;
};

const SOURCE_LABEL: Record<string, string> = {
  metamask: "MetaMask", phantom: "Phantom", manual: "Watched",
};

function short(a: string) {
  return a.length > 16 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a;
}

function toHex(bytes: Uint8Array) {
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function WalletConnect() {
  const { data, mutate } = useApi<any>("/api/trading/wallets",
    { refreshInterval: 0 });
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [addr, setAddr] = useState("");
  const [kind, setKind] = useState<"evm" | "sol">("evm");

  // Extensions inject after the page script runs, and only on a secure
  // origin. Both are checked once mounted rather than assumed.
  const [has, setHas] = useState({ mm: false, ph: false, secure: true });
  useEffect(() => {
    const w = window as any;
    setHas({
      mm: !!w.ethereum,
      ph: !!(w.solana && w.solana.isPhantom),
      secure: window.isSecureContext,
    });
  }, []);

  const items: W[] = data?.items ?? [];

  async function sign(kindWanted: "evm" | "sol") {
    setBusy(kindWanted === "evm" ? "metamask" : "phantom");
    setErr("");
    try {
      const w = window as any;
      let address = "";
      let signature = "";
      let nonce = "";

      if (kindWanted === "evm") {
        const [account] = await w.ethereum.request({ method: "eth_requestAccounts" });
        address = account;
        // One request: the server issues the nonce and the exact sentence
        // containing it together, and the signature has to be over that
        // sentence character for character.
        const fresh = await apiSend<any>("/api/trading/wallets/nonce", "POST", { address });
        nonce = fresh.nonce;
        signature = await w.ethereum.request({
          method: "personal_sign", params: [fresh.message, address],
        });
      } else {
        const res = await w.solana.connect();
        address = res.publicKey.toString();
        const fresh = await apiSend<any>("/api/trading/wallets/nonce", "POST", { address });
        nonce = fresh.nonce;
        const signed = await w.solana.signMessage(
          new TextEncoder().encode(fresh.message), "utf8");
        signature = toHex(signed.signature as Uint8Array);
      }

      await apiSend("/api/trading/wallets", "POST", {
        kind: kindWanted, address, signature, nonce,
        source: kindWanted === "evm" ? "metamask" : "phantom",
      });
      await mutate();
    } catch (e: any) {
      // Somebody closing the wallet popup is not an error worth shouting
      // about — it is them saying no, which they are allowed to do.
      const m = String(e?.message || e);
      setErr(/reject|denied|cancel/i.test(m) ? "" : m);
    } finally {
      setBusy("");
    }
  }

  async function watch() {
    setBusy("manual");
    setErr("");
    try {
      await apiSend("/api/trading/wallets/manual", "POST",
        { kind, address: addr.trim() });
      setAddr("");
      await mutate();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy("");
    }
  }

  async function disconnect(w: W) {
    setBusy(w.address);
    setErr("");
    try {
      await apiSend(`/api/trading/wallets/${w.address}`, "DELETE");
      // Also drop the site's permission in the extension, so "connected" in
      // the wallet matches "connected" here. Neither call is guaranteed to
      // exist, and neither failing changes the outcome on our side.
      const win = window as any;
      try {
        if (w.kind === "sol") await win.solana?.disconnect?.();
        else await win.ethereum?.request?.({
          method: "wallet_revokePermissions", params: [{ eth_accounts: {} }],
        });
      } catch { /* older wallets have no such method */ }
      await mutate();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {items.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {items.map((w) => (
            <div key={w.address}
                 className="flex flex-wrap items-center gap-2 rounded-lg border
                            border-border-soft bg-bg-soft/40 px-2.5 py-2">
              <Wallet size={13} className="shrink-0 text-text-dim" />
              <span className="font-mono text-xs text-text">{short(w.address)}</span>
              <Badge variant={w.kind === "sol" ? "purple" : "blue"}>
                {w.kind === "sol" ? "Solana" : "EVM"}
              </Badge>
              <span className="text-[11px] text-text-dim">
                {SOURCE_LABEL[w.source] ?? w.source}
              </span>
              {w.verified
                ? <Badge variant="green">verified</Badge>
                : <Badge variant="gray">unverified</Badge>}
              <button onClick={() => disconnect(w)} disabled={!!busy}
                      title="Disconnect — the address is forgotten. Nothing is revoked on the chain, because nothing was ever granted."
                      className="ml-auto grid h-6 w-6 place-items-center rounded
                                 text-text-dim transition-colors hover:text-accent-red
                                 disabled:opacity-40">
                {busy === w.address ? <Loader2 size={13} className="animate-spin" />
                                    : <Trash2 size={13} />}
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={!has.mm || !!busy}
                onClick={() => sign("evm")}
                title={has.mm ? "Connect MetaMask — one signature, no transaction"
                              : "MetaMask was not detected in this browser"}>
          {busy === "metamask" ? <Loader2 size={13} className="animate-spin" />
                               : <Plug size={13} />} MetaMask
        </Button>
        <Button size="sm" variant="outline" disabled={!has.ph || !!busy}
                onClick={() => sign("sol")}
                title={has.ph ? "Connect Phantom — one signature, no transaction"
                              : "Phantom was not detected in this browser"}>
          {busy === "phantom" ? <Loader2 size={13} className="animate-spin" />
                              : <Plug size={13} />} Phantom
        </Button>
      </div>

      {!has.secure && (
        <p className="flex items-start gap-2 rounded-lg border border-accent-amber/30
                      bg-accent-amber/10 px-3 py-2 text-[11px] leading-relaxed
                      text-accent-amber">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          <span>
            This page is not served over HTTPS, so wallet extensions will not
            connect. Paste an address below to watch it in the meantime — and
            note that signing over plain HTTP would not be safe even if the
            extension allowed it.
          </span>
        </p>
      )}

      {/* Watch an address without proving it. Honest about what it is. */}
      <div className="flex flex-wrap gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value as any)}
                className="h-9 rounded-lg border border-border bg-bg-soft px-2
                           text-xs text-text">
          <option value="evm">EVM</option>
          <option value="sol">Solana</option>
        </select>
        <Input value={addr} onChange={(e) => setAddr(e.target.value)}
               placeholder={kind === "evm" ? "0x… — watch without connecting"
                                           : "Base58 address"}
               className="min-w-[12rem] flex-1" />
        <Button size="sm" variant="outline" disabled={!addr.trim() || !!busy}
                onClick={watch}
                title="Watch this address. It stays marked unverified — anything that spends will require a signature.">
          {busy === "manual" ? <Loader2 size={13} className="animate-spin" /> : "Watch"}
        </Button>
      </div>

      {err && <p className="text-[11px] text-accent-red">{err}</p>}

      <p className="flex items-start gap-2 text-[11px] leading-relaxed text-text-dim">
        <ShieldCheck size={13} className="mt-0.5 shrink-0" />
        <span>
          Connecting asks your wallet for an address and one signature over a
          plain sentence. It is not a transaction: no gas, no approval, and no
          permission to spend. A seed phrase or private key is never requested,
          and there is nowhere in this app to put one.
        </span>
      </p>
    </div>
  );
}
