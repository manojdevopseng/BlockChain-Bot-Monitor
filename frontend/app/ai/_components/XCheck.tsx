"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Twitter } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { HistorySelect, SearchBox } from "@/components/SectionFilters";
import { FeedState, PAGE, useFeedEnabled, useKeywordRegex } from "./shared";
import { LaunchTable } from "./LaunchTable";

export function XCheck() {
  // Rows arrive over the WebSocket, one per token, as the feed finds them —
  // Shell revalidates this key on an `x_link` event. The interval is only a
  // safety net for a dropped socket, so it can be slow.
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState("");
  const [followers, setFollowers] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const minFollowers = useDebounced(followers);

  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("q", query);
  // Only when a number is actually typed — an empty box is no filter, not zero.
  if (/^\d+$/.test(minFollowers)) params.set("min_followers", minFollowers);
  if (date) params.set("date", date);

  const { data, mutate: refetch, isValidating } =
    useApi<any>(`/api/ai/xcheck?${params.toString()}`, { refreshInterval: 60000 });
  // A filter change puts us back at the first page — otherwise a narrow filter
  // would keep asking for a page size it can never fill.
  useEffect(() => setLimit(PAGE), [query, minFollowers, date]);
  const { data: datesData } = useApi<any>("/api/ai/xdates", { refreshInterval: 300000 });
  const rx = useKeywordRegex();
  const feedOn = useFeedEnabled();
  const items: any[] = data?.items ?? [];
  const emptyText = query || minFollowers ? "Nothing matches this filter"
    : date ? `No launches recorded on ${date}`
    : "Nothing recorded yet";

  return (
    <CollapsibleSection
      id="ai-xcheck"
      title="X Links — live check"
      icon={<Twitter size={14} />}
      count={data?.total ?? items.length}
      controls={<>
        <FeedState enabled={feedOn} />
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <Input
          value={followers}
          onChange={(e) => setFollowers(e.target.value.replace(/\D/g, ""))}
          placeholder="min followers"
          inputMode="numeric"
          className="h-8 w-32 text-xs"
        />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
        {/* A visible way to force a read. Polling covers the normal case, but a
            tab left open across a deploy runs the previous build's timers. */}
        <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isValidating}>
          <RefreshCw size={13} className={isValidating ? "animate-spin" : ""} /> Refresh
        </Button>
      </>}
    >
      {data?.error ? (
        <p className="text-xs text-accent-amber">{data.error}</p>
      ) : (
        <>
          <p className="mb-3 text-xs text-text-dim">
            New pump.fun launches whose X account is verified, pushed by
            PumpPortal as they happen. The link comes from the token's own
            metadata, so it arrives with the launch — a row is typically a second
            or two old. Any kind of tick counts; unverified accounts are not
            listed. The same name and ticker is listed at most five times an IST
            day, so a relaunch loop cannot fill the table.
            {data && (
              <> 
                <span className="text-text">{data.total}</span> stored,{" "}
                showing the newest <span className="text-text">{data.shown}</span>{" "}
                — <span className="text-text">{data.verified}</span> verified,{" "}
                <span className="text-text">{data.posts}</span> with post text.</>
            )}
          </p>
          <LaunchTable items={items} rx={rx} empty={emptyText}
            total={data?.total} onMore={() => setLimit((n) => n + PAGE)} />
        </>
      )}
    </CollapsibleSection>
  );
}
