"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, LifeBuoy, Loader2, Mail, Receipt, Users } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Variant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtDateTime } from "@/lib/utils";

/* The operator's desk.
 *
 * Four things wait on a person — a ticket, an order, a payment nobody could
 * match, a message from somebody with no account — so those four are the page,
 * with their counts at the top. Accounts are underneath, because looking one up
 * is what you do second.
 *
 * Every list here is somebody else's data, which is why the whole router behind
 * it is admin-only and every row says who it belongs to. */

const STATUS_TONE: Record<string, Variant> = {
  open: "amber", in_progress: "blue", resolved: "green", closed: "gray",
  awaiting_payment: "amber", paid: "blue", activated: "green",
  expired: "gray", cancelled: "gray",
  trialing: "blue", active: "green", unverified: "amber", blocked: "red",
};

function Section({ title, icon, count, children }: {
  title: string; icon: React.ReactNode; count?: number; children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">{icon} {title}</CardTitle>
        {count != null && <span className="text-[11px] text-text-dim">{count}</span>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Tickets() {
  const { data } = useApi<any>("/api/support/queue", { refreshInterval: 20000 });
  const items = data?.items ?? [];
  return (
    <Section title="Support queue" icon={<LifeBuoy size={14} />} count={items.length}>
      {items.length === 0 ? (
        <p className="py-4 text-center text-xs text-text-dim">Nothing waiting.</p>
      ) : (
        <div className="space-y-2">
          {items.map((t: any) => (
            <Link key={t.id} href={`/support/${t.id}`}
                  className="block rounded-lg border border-border-soft px-3 py-2 hover:bg-bg-hover/40">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[11px] text-text">{t.id}</span>
                <Badge variant={STATUS_TONE[t.status] ?? "gray"}>{t.status}</Badge>
                {t.priority <= 2 && <Badge variant="red">priority {t.priority}</Badge>}
                <span className="text-xs text-text-muted">{t.user_id}</span>
                <span className="ml-auto text-[10px] text-text-dim">
                  {fmtDateTime(t.created_at)}
                </span>
              </div>
              <p className="mt-1 line-clamp-1 text-[11px] text-text-muted">
                {(t.labels ?? []).join(" · ") || t.message}
              </p>
            </Link>
          ))}
        </div>
      )}
    </Section>
  );
}

function Money() {
  const { data: unmatched } = useApi<any>("/api/admin/unmatched",
                                          { refreshInterval: 30000 });
  const { data: orders } = useApi<any>("/api/admin/orders?limit=25",
                                       { refreshInterval: 30000 });
  const [settling, setSettling] = useState("");
  const rows = orders?.items ?? [];
  const stray = unmatched?.items ?? [];

  async function settle(id: string) {
    setSettling(id);
    try {
      await apiSend(`/api/admin/orders/${id}/settle`, "POST", {});
      mutate("/api/admin/orders?limit=25");
      mutate("/api/admin/unmatched");
    } finally { setSettling(""); }
  }

  return (
    <>
      {stray.length > 0 && (
        <Card className="border-accent-amber/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle size={14} className="text-accent-amber" /> Unmatched payments
            </CardTitle>
            <span className="text-[11px] text-text-dim">{stray.length}</span>
          </CardHeader>
          <CardContent>
            <p className="mb-2 text-[11px] text-text-dim">
              Money arrived that no open order was quoted — nearly always a round
              number sent instead of the exact one. Find that person&rsquo;s order
              below and settle it by hand.
            </p>
            {stray.map((u: any, i: number) => (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-border-soft px-3 py-2">
                <span className="font-mono text-sm text-text">{u.amount}</span>
                <span className="text-xs text-text-muted">{u.asset_id}</span>
                <span className="ml-auto text-[10px] text-text-dim">{fmtDateTime(u.at)}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Section title="Orders" icon={<Receipt size={14} />} count={rows.length}>
        {rows.length === 0 ? (
          <p className="py-4 text-center text-xs text-text-dim">No orders yet.</p>
        ) : (
          <div className="space-y-2">
            {rows.map((o: any) => (
              <div key={o.id} className="rounded-lg border border-border-soft px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-text">{o.id}</span>
                  <Badge variant={STATUS_TONE[o.status] ?? "gray"}>{o.status}</Badge>
                  <span className="text-xs text-text-muted">{o.user_id}</span>
                  <span className="text-xs text-text-muted">{o.plan_label}</span>
                  <span className="ml-auto font-mono text-xs text-text">
                    {o.amount} {o.symbol}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-[10px] text-text-dim">
                  <span>{o.chain}</span>
                  <span>{fmtDateTime(o.created_at)}</span>
                  {o.status !== "activated" && (
                    <button onClick={() => settle(o.id)} disabled={settling === o.id}
                            className="ml-auto text-brand-soft hover:underline">
                      {settling === o.id ? "settling…" : "settle by hand"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

function Accounts() {
  const [q, setQ] = useState("");
  const { data } = useApi<any>(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`,
                               { refreshInterval: 0 });
  const [busy, setBusy] = useState("");
  const rows = data?.items ?? [];

  async function grant(username: string, days: number) {
    setBusy(username);
    try {
      await apiSend(`/api/admin/users/${username}`, "PATCH",
                    { grant_days: days, reason: `${days} days granted from the admin desk` });
      mutate(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    } finally { setBusy(""); }
  }

  async function block(username: string, blocked: boolean) {
    setBusy(username);
    try {
      await apiSend(`/api/admin/users/${username}`, "PATCH", { blocked });
      mutate(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    } finally { setBusy(""); }
  }

  return (
    <Section title="Accounts" icon={<Users size={14} />} count={rows.length}>
      <Input value={q} onChange={(e) => setQ(e.target.value)}
             placeholder="username or email" className="mb-3 h-8 w-64 text-xs" />
      <div className="space-y-2">
        {rows.map((u: any) => (
          <div key={u.username} className="rounded-lg border border-border-soft px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-text">{u.username}</span>
              <Badge variant={STATUS_TONE[u.status] ?? "gray"}>{u.status}</Badge>
              <span className="text-xs text-text-muted">{u.plan_label}</span>
              {u.comped && <Badge variant="purple">granted</Badge>}
              {u.telegram_linked && <Badge variant="blue">telegram</Badge>}
              <span className="ml-auto text-[11px] text-text-dim">{u.email}</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-[10px] text-text-dim">
              <span>{u.days_left} days left</span>
              <span>{u.usage?.rsi_tokens ?? 0} RSI · {u.usage?.mcap_tokens ?? 0} market cap</span>
              <span className="ml-auto flex gap-2">
                <button onClick={() => grant(u.username, 7)} disabled={busy === u.username}
                        className="text-brand-soft hover:underline">+7 days</button>
                <button onClick={() => grant(u.username, 30)} disabled={busy === u.username}
                        className="text-brand-soft hover:underline">+30 days</button>
                <button onClick={() => block(u.username, !u.blocked)}
                        disabled={busy === u.username}
                        className="text-accent-red hover:underline">
                  {u.blocked ? "unsuspend" : "suspend"}
                </button>
              </span>
            </div>
          </div>
        ))}
        {rows.length === 0 && (
          <p className="py-4 text-center text-xs text-text-dim">No accounts yet.</p>
        )}
      </div>
    </Section>
  );
}

function Contacts() {
  const { data } = useApi<any>("/api/admin/contacts", { refreshInterval: 60000 });
  const items = data?.items ?? [];
  async function done(email: string, at: number) {
    await apiSend(`/api/admin/contacts/x/handled`, "POST", { email, at });
    mutate("/api/admin/contacts");
  }
  return (
    <Section title="Contact messages" icon={<Mail size={14} />} count={items.length}>
      {items.length === 0 ? (
        <p className="py-4 text-center text-xs text-text-dim">Nothing new.</p>
      ) : (
        <div className="space-y-2">
          {items.map((m: any, i: number) => (
            <div key={i} className="rounded-lg border border-border-soft px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-text">{m.name || "(no name)"}</span>
                <span className="text-[11px] text-text-muted">{m.email}</span>
                <span className="ml-auto text-[10px] text-text-dim">{fmtDateTime(m.at)}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-[11px] text-text-muted">{m.message}</p>
              <button onClick={() => done(m.email, m.at)}
                      className="mt-1 text-[10px] text-brand-soft hover:underline">
                mark handled
              </button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

export default function AdminPage() {
  const { data, isLoading } = useApi<any>("/api/admin/overview",
                                          { refreshInterval: 30000 });

  if (isLoading && !data) {
    return <div className="grid h-64 place-items-center">
      <Loader2 size={18} className="animate-spin text-text-dim" />
    </div>;
  }
  const a = data?.accounts ?? {};
  const w = data?.waiting ?? {};

  return (
    <div className="space-y-5">
      <PageHeader title="Admin" subtitle="Accounts, money, and everything waiting on you" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Accounts" value={a.total ?? 0} icon={Users} tone="purple" />
        <StatCard label="Paying" value={a.active ?? 0} icon={Users} tone="green" />
        <StatCard label="On trial" value={a.trialing ?? 0} icon={Users} tone="blue" />
        {/* Amber when something is waiting, plain when nothing is — a stat
            card has no "gray" tone, and a red one for zero tickets would cry
            wolf. */}
        <StatCard label="Tickets open" value={w.tickets ?? 0} icon={LifeBuoy}
                  tone={w.tickets ? "amber" : "blue"} />
        <StatCard label="Revenue 30d" value={`$${data?.revenue?.usd_30d ?? 0}`}
                  icon={Receipt} tone="cyan" />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="space-y-5"><Tickets /><Contacts /></div>
        <div className="space-y-5"><Money /></div>
      </div>

      <Accounts />
    </div>
  );
}
