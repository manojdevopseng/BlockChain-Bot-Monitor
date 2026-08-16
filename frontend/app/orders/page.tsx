"use client";

import Link from "next/link";
import { Loader2, Receipt } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Variant } from "@/components/ui/badge";
import { fmtDateTime } from "@/lib/utils";

/* Orders — every attempt to buy, and what came of it.
 *
 * An expired order is still listed: the watcher matches on the amount, so a
 * transfer that arrives late still settles the order it was quoted for, and a
 * row that vanished would make that look like lost money. */

const TONE: Record<string, Variant> = {
  activated: "green", paid: "blue", awaiting_payment: "amber",
  expired: "gray", cancelled: "gray",
};

const SAID: Record<string, string> = {
  awaiting_payment: "waiting for payment",
  paid: "payment seen",
  activated: "active",
  expired: "expired",
  cancelled: "cancelled",
};

export default function OrdersPage() {
  const { data, isLoading } = useApi<any>("/api/billing/orders",
                                          { refreshInterval: 15000 });
  const items = data?.items ?? [];

  return (
    <div className="space-y-5">
      <PageHeader title="Orders" subtitle="What you bought, and what happened next" />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Receipt size={14} /> Your orders</CardTitle>
          <span className="text-[11px] text-text-dim">{items.length}</span>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="grid h-24 place-items-center">
              <Loader2 size={16} className="animate-spin text-text-dim" />
            </div>
          ) : items.length === 0 ? (
            <p className="py-6 text-center text-xs text-text-dim">
              Nothing yet — <Link href="/plan" className="text-brand-soft hover:underline">
                choose a plan
              </Link> when you are ready.
            </p>
          ) : (
            <div className="space-y-2">
              {items.map((o: any) => (
                <Link key={o.id} href={`/orders/${o.id}`}
                      className="block rounded-lg border border-border-soft px-3 py-2.5 hover:bg-bg-hover/40">
                  <div className="flex flex-wrap items-center gap-2">
                    {/* The receipt number leads: it is the one somebody has
                        on a document they filed. The order id stays beside it
                        because that is what support asks for. */}
                    <span className="font-mono text-xs font-semibold text-text">
                      {o.invoice_no ?? o.id}
                    </span>
                    {o.invoice_no ? (
                      <span className="font-mono text-[11px] text-text-dim">{o.id}</span>
                    ) : null}
                    <span className="text-sm text-text">{o.plan_label}</span>
                    <Badge variant={TONE[o.status] ?? "gray"}>{SAID[o.status] ?? o.status}</Badge>
                    <span className="ml-auto font-mono text-sm text-text">
                      {o.amount} {o.symbol}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-text-dim">
                    <span>{o.asset_label}</span>
                    <span>{fmtDateTime(o.created_at)}</span>
                    {o.plan_until ? (
                      <span className="text-accent-green">
                        runs to {new Date(o.plan_until * 1000).toLocaleDateString()}
                      </span>
                    ) : null}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
