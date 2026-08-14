"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Loader2, Send } from "lucide-react";
import { useApi, apiSend } from "@/lib/api";
import { useAccount } from "@/lib/account";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, Variant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fmtDateTime } from "@/lib/utils";

/* One support request: what was reported, what was attached, and the thread.
 *
 * The diagnostics block is shown to the person who reported it, not hidden from
 * them — "the RBH worker was running and its endpoint was connected" is often
 * the answer, and reading it beats waiting for somebody to type it out. */

const TONE: Record<string, Variant> = {
  open: "amber", in_progress: "blue", resolved: "green", closed: "gray",
};
const SAID: Record<string, string> = {
  open: "waiting", in_progress: "being looked at", resolved: "resolved",
  closed: "closed",
};

export default function TicketPage() {
  const id = String(useParams()?.id || "");
  const { isAdmin } = useAccount();
  const { data: t, isLoading, mutate } = useApi<any>(
    id ? `/api/support/tickets/${id}` : null, { refreshInterval: 20000 });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  if (isLoading || !t) {
    return <div className="h-48 animate-pulse rounded-xl border border-border-soft bg-bg-soft/40" />;
  }

  async function send(status?: string) {
    setBusy(true);
    try {
      const path = isAdmin
        ? `/api/support/queue/${id}/reply` : `/api/support/tickets/${id}/reply`;
      await apiSend(path, "POST", { text, ...(status ? { status } : {}) });
      setText("");
      await mutate();
    } finally { setBusy(false); }
  }

  const d = t.diagnostics ?? {};
  return (
    <div className="space-y-5">
      <PageHeader title={`Request ${t.id}`}
                  subtitle={(t.labels ?? []).join(" · ") || "Described in words"} />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Conversation</CardTitle>
            <Badge variant={TONE[t.status] ?? "gray"}>{SAID[t.status] ?? t.status}</Badge>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg border border-border-soft bg-bg-soft/40 p-3">
              <p className="text-[11px] text-text-dim">
                {fmtDateTime(t.created_at)} · {t.user_id}
              </p>
              <ul className="mt-1 space-y-0.5">
                {(t.labels ?? []).map((l: string) => (
                  <li key={l} className="text-xs text-text-muted">• {l}</li>
                ))}
              </ul>
              {t.message && <p className="mt-2 whitespace-pre-wrap text-xs text-text">{t.message}</p>}
              {t.token && <p className="mt-2 font-mono text-[11px] text-text-dim">token: {t.token}</p>}
              {t.order && <p className="font-mono text-[11px] text-text-dim">order: {t.order}</p>}
            </div>

            <div className="mt-3 space-y-2">
              {(t.thread ?? []).map((m: any, i: number) => (
                <div key={i}
                     className={`rounded-lg border px-3 py-2 ${
                       m.admin ? "border-brand/30 bg-brand/5" : "border-border-soft"}`}>
                  <p className="text-[11px] text-text-dim">
                    {m.admin ? "Support" : m.author} · {fmtDateTime(m.at)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-xs text-text">{m.text}</p>
                </div>
              ))}
            </div>

            {t.status !== "closed" && (
              <div className="mt-4">
                <textarea value={text} onChange={(e) => setText(e.target.value)}
                          rows={3} placeholder="Add anything that might help"
                          className="w-full rounded-lg border border-border bg-bg-soft px-3 py-2 text-xs text-text placeholder:text-text-dim" />
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" disabled={busy || !text.trim()}
                          onClick={() => send()}>
                    {busy ? <Loader2 size={13} className="animate-spin" />
                          : <><Send size={13} className="mr-1" /> Reply</>}
                  </Button>
                  {isAdmin && (
                    <>
                      <Button size="sm" variant="outline" disabled={busy}
                              onClick={() => send("resolved")}>Mark resolved</Button>
                      <Button size="sm" variant="outline" disabled={busy}
                              onClick={() => send("closed")}>Close</Button>
                    </>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>What we already know</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-[11px] text-text-dim">
              Attached automatically when you sent this — nobody had to type it.
            </p>
            <dl className="space-y-1.5 text-[11px]">
              <div className="flex justify-between gap-2">
                <dt className="text-text-dim">page</dt>
                <dd className="font-mono text-text-muted">{d.page || "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-text-dim">plan</dt>
                <dd className="text-text-muted">{d.plan} ({d.status})</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-text-dim">Telegram</dt>
                <dd className="text-text-muted">{d.telegram_linked ? "connected" : "not connected"}</dd>
              </div>
              {d.workers && (
                <div className="pt-1">
                  <dt className="text-text-dim">workers</dt>
                  <dd className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(d.workers).map(([k, v]: any) => (
                      <Badge key={k} variant={v ? "green" : "red"}>{k}</Badge>
                    ))}
                  </dd>
                </div>
              )}
              {d.rpc && (
                <div className="pt-1">
                  <dt className="text-text-dim">RPC</dt>
                  <dd className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(d.rpc).map(([k, v]: any) => (
                      <Badge key={k} variant={v ? "green" : "amber"}>{k}</Badge>
                    ))}
                  </dd>
                </div>
              )}
            </dl>
            <p className="mt-3 text-[11px] text-text-dim">
              <Link href="/support" className="text-brand-soft hover:underline">
                All your requests
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
