"use client";

import Link from "next/link";
import { Check } from "lucide-react";
import { useApi } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Pricing, read from the same plans the app enforces.
 *
 * A marketing page with its own copy of the numbers is a page that will one day
 * promise something the product refuses to give — so every figure on this page
 * comes from /api/public/plans, which reads PLANS. */

function every(seconds: number): string {
  if (seconds >= 3600) return `${seconds / 3600}h`;
  if (seconds >= 60) return `${seconds / 60} min`;
  return `${seconds}s`;
}

export default function PricingPage() {
  const { data } = useApi<any>("/api/public/plans", { refreshInterval: 0 });
  const plans: any[] = data?.plans ?? [];
  const rails: any[] = data?.pay_with ?? [];

  return (
    <SiteChrome>
      <SiteHeading
        eyebrow="Pricing"
        title="One tool, three lengths"
        lead="Pay in USDT or USDC. Days are added to whatever you already have, so
              paying early never costs you anything — and there is nothing to
              cancel, because nothing renews by itself."
      />

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-4">
        {plans.map((p) => (
          <div key={p.id}
               className={`rounded-xl border p-5 ${
                 p.id === "half" ? "border-brand/40 bg-brand/5" : "border-border-soft bg-bg-card/40"}`}>
            <div className="flex items-baseline justify-between">
              <h3 className="text-sm font-semibold text-text">{p.label}</h3>
              {p.id === "half" && (
                <span className="rounded bg-brand/20 px-1.5 py-0.5 text-[10px] text-brand-soft">
                  best value
                </span>
              )}
            </div>
            <p className="mt-3 text-2xl font-semibold text-text">
              {p.price_usd ? `$${p.price_usd}` : "Free"}
            </p>
            <p className="text-[11px] text-text-dim">
              {p.days} days{p.note ? ` · ${p.note}` : ""}
            </p>
            <ul className="mt-4 space-y-1.5">
              {[
                `${p.rsi_tokens} RSI tokens`,
                `${p.mcap_tokens} market cap alerts`,
                `${p.mcap_checks_per_day} market cap checks a day`,
                `${p.ai_checks_per_day} AI fact-checks a day`,
                `checks every ${every(p.min_cadence)}`,
                `RSI down to ${every(p.min_interval)}`,
                p.telegram_alerts
                  ? `${p.alerts_per_day} Telegram alerts a day`
                    + (p.alert_delay_seconds
                        ? ` (${p.alert_delay_seconds}s behind live)` : ", live")
                  : "alerts on the dashboard",
                p.support_hours ? `support within ${p.support_hours}h` : "support",
              ].map((line) => (
                <li key={line} className="flex items-start gap-1.5 text-xs text-text-muted">
                  <Check size={12} className="mt-0.5 shrink-0 text-accent-green" />
                  {line}
                </li>
              ))}
            </ul>
            <Link href="/register"
                  className={`mt-4 block rounded-lg px-3 py-2 text-center text-xs font-medium ${
                    p.price_usd ? "bg-brand text-white hover:opacity-90"
                                : "border border-border text-text-muted hover:text-text"}`}>
              {p.price_usd ? "Start free, then upgrade" : `Start ${data?.trial_days ?? 7} days free`}
            </Link>
          </div>
        ))}
      </div>

      <section className="mt-12 grid grid-cols-1 gap-5 md:grid-cols-2">
        <div className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
          <h3 className="text-sm font-semibold text-text">How you pay</h3>
          {rails.length ? (
            <ul className="mt-3 space-y-1.5">
              {rails.map((r) => (
                <li key={r.label} className="text-xs text-text-muted">
                  • {r.label} — network fee {r.fee_note}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-xs text-text-dim">
              Payment options are being set up.
            </p>
          )}
          <p className="mt-3 text-[11px] leading-relaxed text-text-dim">
            Your order shows an address, a QR and an exact amount. Send that
            figure — the last digits are what identify your payment — and the
            plan starts by itself, usually within a minute. No card, no
            subscription that renews behind your back.
          </p>
        </div>

        <div className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
          <h3 className="text-sm font-semibold text-text">Why the limits exist</h3>
          <p className="mt-3 text-xs leading-relaxed text-text-muted">
            Every tracked token is a real request every few seconds, for as long
            as you track it, on endpoints we pay for. The numbers above are the
            shape of that bill rather than a way to push you up a tier — which
            is also why a faster RSI timeframe costs more than a slower one.
          </p>
          <p className="mt-3 text-[11px] leading-relaxed text-text-dim">
            Need more than the yearly plan allows? Ask — it is a number in a
            config file, not a wall.
          </p>
        </div>
      </section>

      <p className="mt-10 text-[11px] text-text-dim">
        Prices are in US dollars and paid in stablecoins. Crypto payments cannot
        be reversed by us; see <Link href="/legal/refund" className="text-brand-soft hover:underline">
          refunds
        </Link> before you buy.
      </p>
    </SiteChrome>
  );
}
