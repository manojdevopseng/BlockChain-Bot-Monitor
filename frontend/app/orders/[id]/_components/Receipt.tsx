"use client";

import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { useApi, apiDownload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/* The bill, on the page and in a file.
 *
 * Both are drawn from the same /receipt call the PDF is built from, so what
 * somebody reads here and what they download cannot say different things —
 * which is the usual way a receipt ends up disagreeing with itself.
 *
 * Shown for an unpaid order too. Its receipt says AWAITING PAYMENT on it, and
 * that is a perfectly good thing to send to whoever approves the spend. */
export function Receipt({ orderId }: { orderId: string }) {
  const { data } = useApi<any>(`/api/billing/orders/${orderId}/receipt`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function download() {
    setBusy(true);
    setError("");
    try {
      await apiDownload(`/api/billing/orders/${orderId}/receipt.pdf`,
                        `${orderId}.pdf`);
    } catch (e: any) {
      setError(e?.message || "Could not download it");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return <Card><CardContent className="pt-4">
      <div className="h-40 animate-pulse rounded-lg bg-bg-soft" />
    </CardContent></Card>;
  }

  const paid = data.settled;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText size={14} /> Receipt
        </CardTitle>
        <Button size="sm" variant="outline" onClick={download} disabled={busy}>
          <Download size={13} /> {busy ? "Preparing…" : "Download PDF"}
        </Button>
      </CardHeader>
      <CardContent>
        {/* Deliberately laid out like the PDF rather than like the rest of the
            app: somebody checking the download against the screen should not
            have to work out whether they are looking at the same document. */}
        <div className="rounded-lg border border-border-soft bg-bg-soft/40 p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-lg font-bold text-text">{data.seller?.name}</div>
              <div className="text-[11px] text-text-dim">{data.seller?.tagline}</div>
            </div>
            <div className="text-right">
              <div className="text-sm font-semibold uppercase tracking-wider text-text-muted">
                Receipt
              </div>
              <div className="font-mono text-[11px] text-text-dim">{data.number}</div>
            </div>
          </div>

          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <div>
              <Label>Billed to</Label>
              <div className="text-sm text-text">{data.buyer?.name}</div>
              {data.buyer?.email ? (
                <div className="text-xs text-text-dim">{data.buyer.email}</div>
              ) : null}
            </div>
            <div className="space-y-0.5 sm:text-right">
              <Row k="Issued" v={data.issued_on} />
              <Row k="Paid on" v={data.paid_on || "—"} />
              <Row k="Status" v={data.status} tone={paid ? "green" : "muted"} />
              <Row k="Order" v={data.order_id} mono />
            </div>
          </div>

          <div className="mt-5 border-t border-border-soft pt-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-sm font-semibold text-text">{data.item?.title}</div>
                <div className="text-xs text-text-dim">{data.item?.detail}</div>
                {data.item?.period ? (
                  <div className="mt-0.5 text-xs text-text-dim">
                    Covers {data.item.period}
                  </div>
                ) : null}
              </div>
              <div className="shrink-0 font-mono text-sm font-semibold text-text tabular-nums">
                {data.item?.price_usd}
              </div>
            </div>
          </div>

          <div className="mt-4 space-y-1 border-t border-border-soft pt-4">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text">Total</span>
              <span className="font-mono text-sm font-semibold text-text tabular-nums">
                {data.total_usd}
              </span>
            </div>
            {data.paid?.amount ? (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs text-text-dim">
                    Settled in {data.paid.symbol} on {data.paid.rail}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-text tabular-nums">
                    {data.paid.amount} {data.paid.symbol}
                  </span>
                </div>
                {data.paid.address ? (
                  <div className="break-all font-mono text-[10px] text-text-dim">
                    Paid to {data.paid.address}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>

          <div className="mt-5 space-y-1.5 border-t border-border-soft pt-4">
            {(data.notes ?? []).map((n: string) => (
              <p key={n} className="text-[11px] leading-relaxed text-text-dim">{n}</p>
            ))}
            {data.seller?.address || data.seller?.email || data.seller?.tax_id ? (
              <p className="pt-1 text-[11px] text-text-dim">
                {[data.seller.address, data.seller.email,
                  data.seller.tax_id ? `Tax ID ${data.seller.tax_id}` : ""]
                  .filter(Boolean).join(" · ")}
              </p>
            ) : null}
          </div>
        </div>

        {error ? (
          <p className="mt-2 text-xs text-accent-amber">{error}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-wider text-text-dim">
      {children}
    </div>
  );
}

function Row({ k, v, mono, tone }: {
  k: string; v: string; mono?: boolean; tone?: "green" | "muted";
}) {
  return (
    <div className="flex items-baseline justify-end gap-2 text-xs">
      <span className="text-text-dim">{k}</span>
      <span className={[
        mono ? "font-mono" : "",
        tone === "green" ? "font-semibold text-accent-green" : "text-text",
      ].join(" ")}>{v}</span>
    </div>
  );
}
