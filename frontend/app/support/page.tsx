"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CheckCircle2, LifeBuoy, Loader2, Send } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Variant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtDateTime } from "@/lib/utils";

/* Support — report a problem in one tap, or in your own words.
 *
 * The list is the things that actually go wrong with this product, so a report
 * arrives already saying which one. What nobody has to type — which page, which
 * plan, whether the worker behind that panel was even running — is attached by
 * the server. */

const STATUS_TONE: Record<string, Variant> = {
  open: "amber", in_progress: "blue", resolved: "green", closed: "gray",
};
const STATUS_SAID: Record<string, string> = {
  open: "waiting", in_progress: "being looked at", resolved: "resolved",
  closed: "closed",
};

// Which extra field a chosen problem makes worth asking for.
const NEEDS: Record<string, "address" | "order"> = {
  launch_missing: "address", rsi_mismatch: "address", mcap_wrong: "address",
  token_add_failed: "address", payment_not_activated: "order",
};

export default function SupportPage() {
  const path = usePathname();
  const { data: cat } = useApi<any>("/api/support/problems");
  const { data: mine, mutate } = useApi<any>("/api/support/tickets",
                                             { refreshInterval: 30000 });
  const [picked, setPicked] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [token, setToken] = useState("");
  const [order, setOrder] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState("");
  const [error, setError] = useState("");

  const problems: any[] = cat?.items ?? [];
  const groups: [string, any[]][] = [];
  for (const p of problems) {
    const last = groups[groups.length - 1];
    if (last && last[0] === p.group) last[1].push(p);
    else groups.push([p.group, [p]]);
  }
  const wants = new Set(picked.map((p) => NEEDS[p]).filter(Boolean));

  function toggle(id: string) {
    setPicked((cur) => cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  }

  async function submit() {
    setBusy(true); setError("");
    try {
      const got = await apiSend("/api/support/tickets", "POST", {
        problems: picked, message, token, order, page: path,
        client: {
          // Claims, not facts — useful for reproducing, never for deciding.
          agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
          screen: typeof window !== "undefined"
            ? `${window.innerWidth}x${window.innerHeight}` : "",
        },
      });
      setSent(got.id); setPicked([]); setMessage(""); setToken(""); setOrder("");
      await mutate();
    } catch (e: any) {
      setError(e?.message || "Could not send that");
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Support" subtitle="Tell us what went wrong — we will see the rest ourselves" />

      {sent && (
        <div className="flex items-center gap-2 rounded-xl border border-accent-green/30 bg-accent-green/10 px-4 py-3">
          <CheckCircle2 size={16} className="text-accent-green" />
          <p className="text-sm text-text">
            Sent — your request is <Link href={`/support/${sent}`}
              className="font-mono text-brand-soft hover:underline">{sent}</Link>.
            You will get an answer by email, and on Telegram if it is connected.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <LifeBuoy size={14} /> What went wrong?
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-xs text-text-dim">
              Tick anything that fits. We attach the rest — the page you were on,
              your plan, and whether the part behind it was running at the time.
            </p>

            <div className="space-y-4">
              {groups.map(([group, items]) => (
                <div key={group}>
                  <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                    {group}
                  </p>
                  <div className="space-y-1">
                    {items.map((p) => (
                      <label key={p.id}
                             className="flex cursor-pointer items-start gap-2 rounded-lg border border-border-soft px-3 py-2 hover:bg-bg-hover/40">
                        <input type="checkbox" checked={picked.includes(p.id)}
                               onChange={() => toggle(p.id)}
                               className="mt-0.5 accent-[var(--brand)]" />
                        <span className="text-xs text-text-muted">{p.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {wants.has("address") && (
              <div className="mt-4">
                <label className="mb-1 block text-xs text-text-muted">
                  Which token? (address)
                </label>
                <Input value={token} onChange={(e) => setToken(e.target.value)}
                       placeholder="0x… or a Solana mint" className="h-8 text-xs" />
              </div>
            )}
            {wants.has("order") && (
              <div className="mt-4">
                <label className="mb-1 block text-xs text-text-muted">
                  Which order? (ORD-…)
                </label>
                <Input value={order} onChange={(e) => setOrder(e.target.value)}
                       placeholder="ORD-XXXXXXXX" className="h-8 text-xs" />
              </div>
            )}

            <div className="mt-4">
              <label className="mb-1 block text-xs text-text-muted">
                Anything else? (optional — but the more the better)
              </label>
              <textarea value={message} onChange={(e) => setMessage(e.target.value)}
                        rows={4} placeholder="What you did, what you expected, what happened instead"
                        className="w-full rounded-lg border border-border bg-bg-soft px-3 py-2 text-xs text-text placeholder:text-text-dim" />
            </div>

            {error && <p className="mt-2 text-xs text-accent-red">{error}</p>}

            <Button variant="primary" className="mt-4"
                    onClick={submit}
                    disabled={busy || (picked.length === 0 && !message.trim())}>
              {busy ? <Loader2 size={14} className="animate-spin" />
                    : <><Send size={13} className="mr-1" /> Send request</>}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Your requests</CardTitle>
            <span className="text-[11px] text-text-dim">{mine?.items?.length ?? 0}</span>
          </CardHeader>
          <CardContent>
            {(mine?.items ?? []).length === 0 ? (
              <p className="py-6 text-center text-xs text-text-dim">
                Nothing yet — which is the idea.
              </p>
            ) : (
              <div className="space-y-2">
                {(mine?.items ?? []).map((t: any) => (
                  <Link key={t.id} href={`/support/${t.id}`}
                        className="block rounded-lg border border-border-soft px-3 py-2 hover:bg-bg-hover/40">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] text-text">{t.id}</span>
                      <Badge variant={STATUS_TONE[t.status] ?? "gray"}>
                        {STATUS_SAID[t.status] ?? t.status}
                      </Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] text-text-muted">
                      {(t.labels ?? []).join(" · ") || t.message}
                    </p>
                    <p className="mt-0.5 text-[10px] text-text-dim">
                      {fmtDateTime(t.created_at)}
                      {t.thread?.length ? ` · ${t.thread.length} reply` : ""}
                    </p>
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
