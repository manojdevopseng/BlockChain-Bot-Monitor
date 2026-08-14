"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2 } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { useAccount, statusLine } from "@/lib/account";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/* Plans — and, when a subscription has ended, the way back in.
 *
 * This page stays reachable with an expired account on purpose: being bounced
 * off the page that takes the payment is how an account stays expired.
 *
 * Picking a plan asks one more question — which coin, on which chain — and then
 * hands over to the order page. The rail matters to the payer more than to us:
 * the same $29.99 costs cents on Solana and several dollars on Ethereum, so the
 * list says so. */

const FEATURES: Record<string, string[]> = {
  trial: ["Every panel, read-only limits", "3 RSI + 3 Market Cap tokens",
          "25 market cap checks a day", "Alerts on the dashboard"],
  monthly: ["25 RSI + 25 Market Cap tokens", "300 checks a day",
            "Alerts in your own Telegram", "Checks every 15s"],
  half: ["50 RSI + 50 Market Cap tokens", "600 checks a day",
         "15-second RSI timeframes", "Priority support"],
  yearly: ["100 RSI + 100 Market Cap tokens", "1,500 checks a day",
           "5-second RSI timeframes", "Fastest support"],
};

export default function PlanPage() {
  const { account, loading } = useAccount();
  const { data: options } = useApi<any>("/api/billing/options");
  const router = useRouter();
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const assets = options?.assets ?? [];

  async function buy(plan: string, asset: string) {
    setBusy(true); setError("");
    try {
      const order = await apiSend("/api/billing/orders", "POST", { plan, asset });
      router.push(`/orders/${order.id}`);
    } catch (e: any) {
      setError(e?.message || "Could not start that order");
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="grid h-64 place-items-center">
      <Loader2 size={18} className="animate-spin text-text-dim" />
    </div>;
  }

  const ended = account && !account.usable;

  return (
    <div className="space-y-5">
      <PageHeader title="Plans"
                  subtitle="Pay in USDT or USDC — monthly, six-monthly or yearly" />

      {ended && (
        <div className="rounded-xl border border-accent-amber/30 bg-accent-amber/10 px-4 py-3">
          <p className="text-sm text-text">{account?.reason}</p>
          <p className="mt-1 text-xs text-text-dim">
            Your tokens, targets and settings are all still here — a plan brings
            them back to life exactly as you left them.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-4">
        {(account?.plans ?? []).map((p) => (
          <Card key={p.id} className={p.current ? "border-brand/40" : undefined}>
            <CardHeader>
              <CardTitle>{p.label}</CardTitle>
              {p.current && <Badge variant="purple">current</Badge>}
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold text-text">
                {p.price_usd ? `$${p.price_usd}` : "Free"}
              </p>
              <p className="text-[11px] text-text-dim">
                {p.days} days{p.note ? ` · ${p.note}` : ""}
              </p>
              <ul className="mt-4 space-y-1.5">
                {(FEATURES[p.id] ?? []).map((f) => (
                  <li key={f} className="flex items-start gap-1.5 text-xs text-text-muted">
                    <Check size={12} className="mt-0.5 shrink-0 text-accent-green" />
                    {f}
                  </li>
                ))}
              </ul>
              {p.id === "trial" ? (
                <Button size="sm" variant="outline"
                        className="mt-4 w-full justify-center" disabled>
                  Included
                </Button>
              ) : picked === p.id ? (
                <div className="mt-4 space-y-1.5">
                  <p className="text-[11px] text-text-dim">Pay with</p>
                  {assets.length === 0 && (
                    <p className="text-[11px] text-accent-amber">
                      No payment rail is open yet — check back shortly.
                    </p>
                  )}
                  {assets.map((a: any) => (
                    <button key={a.id} onClick={() => buy(p.id, a.id)} disabled={busy}
                            className="w-full rounded border border-border-soft px-2 py-1.5 text-left text-[11px] text-text-muted hover:bg-bg-hover disabled:opacity-50">
                      <span className="text-text">{a.label}</span>
                      <span className="block text-text-dim">network fee: {a.fee_note}</span>
                    </button>
                  ))}
                  <button onClick={() => setPicked("")}
                          className="w-full text-[11px] text-text-dim hover:text-text-muted">
                    back
                  </button>
                </div>
              ) : (
                <Button size="sm" variant={p.current ? "outline" : "primary"}
                        className="mt-4 w-full justify-center"
                        onClick={() => setPicked(p.id)} disabled={busy}>
                  {busy ? <Loader2 size={13} className="animate-spin" />
                        : p.current ? "Extend" : "Choose"}
                </Button>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {error && <p className="text-xs text-accent-red">{error}</p>}

      <Card>
        <CardHeader><CardTitle>How paying works</CardTitle></CardHeader>
        <CardContent>
          <p className="text-xs text-text-dim">
            USDT or USDC, on the chain you prefer. Your order shows an address, a
            QR code and an exact amount — send that figure and the payment is
            matched on chain and your plan starts by itself. No screenshots, and
            nothing waiting on anybody being awake. Days are added to whatever
            you already have, so paying early costs nothing.
          </p>
          <p className="mt-3 text-[11px] text-text-dim">
            Current status: {statusLine(account) || "—"}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
