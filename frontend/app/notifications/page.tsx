"use client";

import Link from "next/link";
import { Bell, CreditCard, LifeBuoy, Megaphone, Target } from "lucide-react";
import { useApi } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { fmtDateTime } from "@/lib/utils";

/* Everything the bell has shown this account, in full.
 *
 * The bell holds the last dozen; this is the rest of the month. They age out on
 * the same TTL as alerts — a notice nobody read in thirty days is not going to
 * be read. */

const ICONS: Record<string, any> = {
  alert: Target, billing: CreditCard, support: LifeBuoy, system: Megaphone,
};

export default function NotificationsPage() {
  const { data } = useApi<any>("/api/notifications", { refreshInterval: 30000 });
  const items: any[] = data?.items ?? [];

  return (
    <div className="space-y-5">
      <PageHeader title="Notifications"
                  subtitle="Your alerts, payments and support replies" />
      <Card>
        <CardContent className="p-0">
          {items.length === 0 ? (
            <p className="px-4 py-10 text-center text-xs text-text-dim">
              Nothing yet. Set a market cap target or an RSI token and this fills
              up on its own.
            </p>
          ) : (
            items.map((n: any, i: number) => {
              const Icon = ICONS[n.kind] ?? Bell;
              const inner = (
                <div className="flex gap-3 border-b border-border-soft px-4 py-3 last:border-0">
                  <Icon size={15} className="mt-0.5 shrink-0 text-text-dim" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-text">{n.title}</p>
                    {n.body && <p className="mt-0.5 text-xs text-text-muted">{n.body}</p>}
                  </div>
                  <span className="shrink-0 text-[10px] text-text-dim">
                    {fmtDateTime(n.at)}
                  </span>
                </div>
              );
              return n.link
                ? <Link key={i} href={n.link} className="block hover:bg-bg-hover/40">{inner}</Link>
                : <div key={i}>{inner}</div>;
            })
          )}
        </CardContent>
      </Card>
    </div>
  );
}
