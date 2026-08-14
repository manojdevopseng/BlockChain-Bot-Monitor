"use client";

import { useApi } from "@/lib/api";
import { SiteChrome, SiteHeading } from "@/components/site/SiteChrome";

/* What changed, newest first — read from the server so it ships with the change
 * it describes rather than lagging behind it. */

export default function ChangelogPage() {
  const { data } = useApi<any>("/api/public/changelog", { refreshInterval: 0 });
  const items: any[] = data?.items ?? [];

  return (
    <SiteChrome>
      <SiteHeading eyebrow="Changelog" title="What changed"
                   lead="Every release that touched something you can see." />
      <div className="space-y-4">
        {items.map((r: any, i: number) => (
          <div key={i} className="rounded-xl border border-border-soft bg-bg-card/40 p-5">
            <div className="flex flex-wrap items-baseline gap-3">
              <h2 className="text-sm font-semibold text-text">{r.title}</h2>
              <span className="text-[11px] text-text-dim">{r.date}</span>
            </div>
            <ul className="mt-2 space-y-1">
              {(r.items ?? []).map((line: string) => (
                <li key={line} className="text-xs leading-relaxed text-text-muted">• {line}</li>
              ))}
            </ul>
          </div>
        ))}
        {items.length === 0 && (
          <p className="text-xs text-text-dim">Nothing here yet.</p>
        )}
      </div>
    </SiteChrome>
  );
}
