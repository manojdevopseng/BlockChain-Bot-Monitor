"use client";

import { Check, Loader2 } from "lucide-react";
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
 * Paying is not wired up yet — that is the next phase — so the button says so
 * rather than pretending. A "Subscribe" that does nothing is worse than an
 * honest "not yet". */

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
              <Button size="sm" variant={p.current ? "outline" : "primary"}
                      className="mt-4 w-full justify-center" disabled>
                {p.id === "trial" ? "Included" : "Payment opens soon"}
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader><CardTitle>How paying will work</CardTitle></CardHeader>
        <CardContent>
          <p className="text-xs text-text-dim">
            USDT or USDC, on the chain you prefer. Each order shows an address, a
            QR code and an exact amount; the payment is matched on chain and the
            plan starts by itself — no screenshots to send, nothing to wait for
            by hand. You will see every step on your Orders page.
          </p>
          <p className="mt-3 text-[11px] text-text-dim">
            Current status: {statusLine(account) || "—"}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
