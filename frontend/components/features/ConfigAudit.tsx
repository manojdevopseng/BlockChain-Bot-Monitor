"use client";

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useApi } from "@/lib/api";

/* What is switched on and has nowhere to send what it produces.
 *
 * A blank destination is a skipped route by design — that is what lets this
 * run on a fresh box. The cost is silence: a feature you meant to use can be
 * off for weeks without saying a word, because the switch is on, the code
 * runs, and the send finds no chat id and returns.
 *
 * So the silence gets a place to be seen. Nothing here is fixed from this
 * panel: each row names the exact key, and the fields to set it are on the
 * same page. */

const TONE = {
  error: {
    icon: XCircle,
    box: "border-accent-red/40 bg-accent-red/10",
    text: "text-accent-red",
  },
  warn: {
    icon: AlertTriangle,
    box: "border-accent-amber/40 bg-accent-amber/10",
    text: "text-accent-amber",
  },
} as const;

export function ConfigAudit() {
  const { data } = useApi<any>("/api/system/config-audit", { refreshInterval: 60000 });
  const items: any[] = data?.items ?? [];

  if (!data) return null;

  if (items.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-accent-green/30
                      bg-accent-green/10 px-4 py-2.5 text-xs text-accent-green">
        <CheckCircle2 size={14} className="shrink-0" />
        Every switched-on feature has somewhere to send what it produces.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-text">Configuration</h3>
        <span className="text-[11px] text-text-dim">
          {data.errors > 0 && (
            <b className="text-accent-red">{data.errors} cannot work</b>
          )}
          {data.errors > 0 && data.warnings > 0 && " · "}
          {data.warnings > 0 && `${data.warnings} not as intended`}
        </span>
      </div>

      {/* Loudest first — an "error" is a feature that cannot work at all. */}
      {["error", "warn"].map((level) =>
        items.filter((i) => i.level === level).map((i, n) => {
          const t = TONE[level as keyof typeof TONE];
          const Icon = t.icon;
          return (
            <div key={`${level}-${n}`}
                 className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 ${t.box}`}>
              <Icon size={14} className={`mt-0.5 shrink-0 ${t.text}`} />
              <div className="min-w-0">
                <div className={`text-xs font-medium ${t.text}`}>
                  {i.feature}
                  <code className="ml-2 rounded bg-black/20 px-1.5 py-0.5 text-[10px]
                                   font-normal">{i.missing}</code>
                </div>
                <div className="mt-0.5 text-[11px] leading-snug text-text-muted">
                  {i.effect}
                </div>
              </div>
            </div>
          );
        }),
      )}
    </div>
  );
}
