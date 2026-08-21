"use client";

import { useState } from "react";
import { mutate } from "swr";
import { Loader2, ShoppingCart, TrendingDown } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";

/* Buy — or sell, when this account is already holding it.
 *
 * One control rather than two side by side, because on a detection row only
 * ever one of them applies: you cannot sell what you have not bought, and a
 * dead "Sell" next to every row is noise on every row.
 *
 * The open positions are fetched once for the whole page — SWR shares the key
 * across every button on it — so a table of a hundred rows costs one request,
 * not a hundred. */

type Props = {
  chain?: string;
  address?: string;
  symbol?: string;
  name?: string;
};

const KEY = "/api/trading/positions?status=open";

export function TradeButton({ chain, address, symbol, name }: Props) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const { data } = useApi<any>(KEY);

  if (!chain || !address) return null;

  const held = (data?.items ?? []).find(
    (p: any) => p.chain === chain
      && String(p.address).toLowerCase() === String(address).toLowerCase(),
  );

  async function go() {
    setBusy(true);
    setNote("");
    try {
      if (held) {
        await apiSend(`/api/trading/sell/${held.id}`, "POST", { percent: 100 });
      } else {
        await apiSend("/api/trading/buy", "POST", { chain, address, symbol, name });
      }
      mutate((k) => typeof k === "string" && k.startsWith("/api/trading"));
    } catch (e: any) {
      // Shown on the row rather than thrown away: "no price for that token
      // right now" is the common answer for something minutes old, and it is
      // worth reading.
      setNote(String(e?.message || e).replace(/^Error:\s*/, ""));
      setTimeout(() => setNote(""), 6000);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <button
        onClick={go}
        disabled={busy}
        title={held ? `Sell the ${symbol || "token"} position` : `Buy ${symbol || "this token"}`}
        className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px]
                    font-medium transition-colors disabled:opacity-50 ${
          held
            ? "border-accent-red/40 text-accent-red hover:bg-accent-red/10"
            : "border-accent-green/40 text-accent-green hover:bg-accent-green/10"
        }`}
      >
        {busy ? <Loader2 size={11} className="animate-spin" />
              : held ? <TrendingDown size={11} /> : <ShoppingCart size={11} />}
        {held ? "Sell" : "Buy"}
      </button>
      {note && <span className="max-w-[22ch] text-[10px] leading-tight text-accent-red">{note}</span>}
    </span>
  );
}
