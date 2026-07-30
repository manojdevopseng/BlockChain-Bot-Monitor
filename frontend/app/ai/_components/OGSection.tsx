"use client";

import { useEffect, useState } from "react";
import { Crown } from "lucide-react";
import { useApi } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { CollapsibleSection } from "@/components/CollapsibleSection";
import { HistorySelect, SearchBox } from "@/components/SectionFilters";
import { FeedState, PAGE, useFeedEnabled, useKeywordRegex } from "./shared";
import { LaunchTable } from "./LaunchTable";

// A name relaunched to the daily cap is somebody working at it, and the first
// of those launches is the one worth keeping — the rest are copies of it.
export function OGSection() {
  const [limit, setLimit] = useState(PAGE);
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const query = useDebounced(q);
  const rx = useKeywordRegex();
  const feedOn = useFeedEnabled();

  const params = new URLSearchParams({ limit: String(limit) });
  if (query) params.set("q", query);
  if (date) params.set("date", date);

  const { data } = useApi<any>(`/api/ai/og?${params.toString()}`, { refreshInterval: 60000 });
  useEffect(() => setLimit(PAGE), [query, date]);
  const { data: datesData } = useApi<any>("/api/ai/xdates?og=true", { refreshInterval: 300000 });
  const items: any[] = data?.items ?? [];

  return (
    <CollapsibleSection
      id="ai-og"
      title="OG Pump.Fun Tokens"
      icon={<Crown size={14} />}
      count={data?.total ?? items.length}
      controls={<>
        <FeedState enabled={feedOn} />
        <SearchBox value={q} onChange={setQ} placeholder="address / @username / word" />
        <HistorySelect value={date} onChange={setDate} dates={datesData?.dates ?? []} />
      </>}
    >
      <p className="mb-3 text-xs text-text-dim">
        The first launch of a name and ticker that came back five times inside
        five minutes, counting only launches that carry an X link. Five is not a
        coincidence — it is somebody working at it — and the original is the one
        that ran before the copies. Same feed and same switch as the live section
        below.
        {data && (
          <> <span className="text-text">{data.total}</span> stored, showing the
            newest <span className="text-text">{data.shown}</span>.</>
        )}
      </p>
      <LaunchTable
        items={items}
        rx={rx}
        total={data?.total}
        onMore={() => setLimit((n) => n + PAGE)}
        empty={query ? "Nothing matches this search"
          : date ? `No originals on ${date}`
          : "No name has hit five launches yet today"}
      />
    </CollapsibleSection>
  );
}
