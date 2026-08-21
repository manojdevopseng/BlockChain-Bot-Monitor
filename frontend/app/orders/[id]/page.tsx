"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CheckCircle2, Clock, Copy, Loader2, XCircle } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/CopyButton";
import { Receipt } from "./_components/Receipt";

/* One order: pay this exact figure to this address, and watch it settle.
 *
 * The page polls while it is open, because the thing it is waiting for happens
 * on a chain and not in this browser. The exact amount is the loudest thing on
 * the screen — it is what identifies the payment, and a round number instead
 * is the one mistake that stops an order settling by itself. */

function Countdown({ until }: { until: number }) {
  const [left, setLeft] = useState(() => Math.max(0, until - Date.now() / 1000));
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, until - Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, [until]);
  const m = Math.floor(left / 60), s = Math.floor(left % 60);
  return <span className={left < 300 ? "text-accent-amber" : "text-text-muted"}>
    {m}:{String(s).padStart(2, "0")}
  </span>;
}

export default function OrderPage() {
  const id = String(useParams()?.id || "");
  const { data: order, isLoading, mutate } = useApi<any>(
    id ? `/api/billing/orders/${id}` : null,
    // The answer arrives from a chain, so the page asks rather than waits.
    { refreshInterval: 10000 });
  const [busy, setBusy] = useState(false);

  if (isLoading || !order) {
    return <div className="grid h-64 place-items-center">
      <Loader2 size={18} className="animate-spin text-text-dim" />
    </div>;
  }

  const open = order.status === "awaiting_payment";
  const done = order.status === "activated";
  // Paid but not yet applied still counts as settled for the page's purpose:
  // the money has arrived, so asking for it again is wrong either way.
  const settled = done || order.status === "paid";

  async function cancel() {
    setBusy(true);
    try { await apiSend(`/api/billing/orders/${id}/cancel`, "POST"); await mutate(); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <PageHeader title={`Order ${order.id}`}
                  subtitle={[order.plan_label, order.invoice_no]
                    .filter(Boolean).join(" · ")} />

      {done && (
        <div className="flex items-center gap-2 rounded-xl border border-accent-green/30 bg-accent-green/10 px-4 py-3">
          <CheckCircle2 size={16} className="text-accent-green" />
          <div>
            <p className="text-sm text-text">Payment received — your plan is active.</p>
            <p className="text-xs text-text-dim">
              Runs to {order.plan_until
                ? new Date(order.plan_until * 1000).toLocaleDateString() : "—"}.
              {" "}<Link href="/profile" className="text-brand-soft hover:underline">Profile</Link>
            </p>
          </div>
        </div>
      )}

      {order.status === "paid" && (
        <div className="flex items-center gap-2 rounded-xl border border-accent-blue/30 bg-accent-blue/10 px-4 py-3">
          <Loader2 size={16} className="animate-spin text-accent-blue" />
          <p className="text-sm text-text">Payment seen — applying your plan…</p>
        </div>
      )}

      {(order.status === "expired" || order.status === "cancelled") && (
        <div className="flex items-start gap-2 rounded-xl border border-border bg-bg-soft/60 px-4 py-3">
          <XCircle size={16} className="mt-0.5 text-text-dim" />
          <div>
            <p className="text-sm text-text">
              This order is {order.status === "expired" ? "past its window" : "cancelled"}.
            </p>
            <p className="text-xs text-text-dim">
              If you already sent it, do nothing — payments are matched by amount,
              so it will still settle this order. Otherwise{" "}
              <Link href="/plan" className="text-brand-soft hover:underline">start a new one</Link>.
            </p>
          </div>
        </div>
      )}

      {/* Once an order has settled, "Pay exactly this" and its QR are
          instructions for something that already happened — and a live payment
          address on a finished order is an invitation to send money twice. A
          receipt replaces them. */}
      {settled ? (
        <Receipt orderId={String(id)} />
      ) : (
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Pay exactly this</CardTitle>
            {open && <span className="flex items-center gap-1 text-[11px] text-text-dim">
              <Clock size={12} /> <Countdown until={order.expires_at} />
            </span>}
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <p className="text-xs text-text-dim">Amount — send this figure, not a rounded one</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="font-mono text-2xl font-semibold text-text">
                  {order.amount}
                </span>
                <span className="text-sm text-text-muted">{order.symbol}</span>
                <CopyButton value={String(order.amount)} />
              </div>
              <p className="mt-1 text-[11px] text-text-dim">
                The last digits are what tell us the payment is yours.
              </p>
            </div>

            <div>
              <p className="text-xs text-text-dim">To this address — {order.asset_label}</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="break-all font-mono text-xs text-text">{order.address}</span>
                <CopyButton value={order.address} />
              </div>
            </div>

            <div className="rounded-lg border border-border-soft bg-bg-soft/40 p-3">
              <p className="text-[11px] text-text-dim">
                Send only <b>{order.symbol}</b> on <b>{order.asset_label}</b>. A
                transfer on another chain cannot be seen by this order, and coins
                sent to the wrong network are gone for good.
              </p>
            </div>

            {open && (
              <Button size="sm" variant="outline" onClick={cancel} disabled={busy}>
                Cancel this order
              </Button>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Scan</CardTitle></CardHeader>
          <CardContent>
            {order.qr_svg ? (
              <div className="mx-auto max-w-[220px] [&>svg]:h-auto [&>svg]:w-full"
                   dangerouslySetInnerHTML={{ __html: order.qr_svg }} />
            ) : (
              <p className="text-xs text-text-dim">
                No QR for this chain — copy the address and amount above.
              </p>
            )}
            <p className="mt-3 text-[11px] text-text-dim">
              Most wallets fill in the address and the amount from this code.
              Check both before you send.
            </p>
          </CardContent>
        </Card>
      </div>
      )}

      {!settled && (
      <Card>
        <CardHeader><CardTitle>What happens next</CardTitle></CardHeader>
        <CardContent>
          <ol className="space-y-2 text-xs text-text-dim">
            <li>1. You send the exact amount to that address.</li>
            <li>2. We see it on chain, usually within a minute. This page updates itself.</li>
            <li>3. Your plan starts, and its days are added to whatever you already had.</li>
          </ol>
          <p className="mt-3 text-[11px] text-text-dim">
            Sent the wrong amount, or sent it late? Nothing is lost — open a
            support request and it gets matched by hand.
          </p>
        </CardContent>
      </Card>
      )}

      {/* An unpaid order has a receipt too — it says AWAITING PAYMENT, which is
          exactly what somebody needs when the person paying is not the person
          who chose the plan. */}
      {!settled && <Receipt orderId={String(id)} />}
    </div>
  );
}
