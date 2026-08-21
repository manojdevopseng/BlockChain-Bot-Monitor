"use client";

import { useApi } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* Status, in the product\'s own words.
 *
 * Coarse on purpose: a public page that names workers and endpoints is a map of
 * what to attack. This says which parts are up and nothing about how they are
 * wired. */

const TONE: Record<string, string> = {
  operational: "text-accent-green border-accent-green/30 bg-accent-green/10",
  degraded: "text-accent-amber border-accent-amber/30 bg-accent-amber/10",
  down: "text-accent-red border-accent-red/30 bg-accent-red/10",
};

const SAID: Record<string, string> = {
  operational: "All systems operational",
  degraded: "Some parts are degraded",
  down: "Something is down",
};

function uptime(seconds: number): string {
  if (!seconds) return "—";
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
  return d ? `${d}d ${h}h` : `${h}h`;
}

export default function StatusPage() {
  const { data } = useApi<any>("/api/public/status", { refreshInterval: 30000 });
  const overall = data?.overall ?? "operational";

  return (
    <SiteChrome>
      <SiteHeading eyebrow="Status" title="Is it working?"
                   lead="Read live from the running service, every thirty seconds." />

      <div className={`rounded-xl border px-5 py-4 ${TONE[overall]}`}>
        <p className="text-sm font-semibold">{SAID[overall]}</p>
        <p className="mt-1 text-[11px] opacity-80">
          Running for {uptime(data?.uptime_seconds ?? 0)} since the last restart.
        </p>
      </div>

      <div className="mt-6 space-y-2">
        {(data?.components ?? []).map((c: any) => (
          <div key={c.name}
               className="flex items-center gap-3 rounded-lg border border-border-soft bg-bg-card/40 px-4 py-3">
            <span className={`h-2 w-2 shrink-0 rounded-full ${
              c.status === "operational" ? "bg-accent-green"
                : c.status === "degraded" ? "bg-accent-amber" : "bg-accent-red"}`} />
            <span className="text-xs text-text">{c.name}</span>
            <span className="ml-auto text-[11px] text-text-dim">{c.status}</span>
          </div>
        ))}
      </div>

      <p className="mt-6 text-[11px] leading-relaxed text-text-dim">
        A part reading degraded usually means an RPC provider is rate-limiting
        us and we are on the second endpoint; alerts keep working. Anything worse
        and it says down.
      </p>
    </SiteChrome>
  );
}
