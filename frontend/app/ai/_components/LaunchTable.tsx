"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { STICKY_HEAD, TableScroll } from "@/components/TableScroll";
import { fmtDateTime, rowKey } from "@/lib/utils";
import { Age, Highlighted, PAGE, TokenCell } from "./shared";

// The launch table, shared by the live section and the OG one. Same nine
// columns, same row rendering — two copies would have drifted the moment one
// of them gained a column.
export function LaunchTable({ items, rx, empty, total, onMore }: {
  items: any[];
  rx: RegExp | null;
  empty: string;
  total?: number;
  onMore?: () => void;
}) {
  return (
    <>
        <TableScroll>
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className={`${STICKY_HEAD} border-b border-border`}>
                <th className="px-3 py-2.5 font-medium">Token</th>
                <th className="px-3 py-2.5 font-medium">Age</th>
                <th className="px-3 py-2.5 font-medium">Link type</th>
                <th className="px-3 py-2.5 font-medium">Account</th>
                <th className="px-3 py-2.5 font-medium">Verified</th>
                <th className="px-3 py-2.5 font-medium">Followers</th>
                <th className="px-3 py-2.5 font-medium">Post</th>
                <th className="px-3 py-2.5 font-medium">Text</th>
                <th className="px-3 py-2.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-text-dim">
                  {empty}
                </td></tr>
              ) : items.map((r: any, i: number) => (
                <tr key={rowKey(r, i)} className="border-b border-border-soft align-top hover:bg-bg-hover/40">
                  <TokenCell address={r.address} symbol={r.symbol} />
                  <td className="px-3 py-3 font-mono text-xs text-text-muted">
                    <Age ts={r.open_timestamp} />
                  </td>
                  <td className="px-3 py-3">
                    <Badge variant={r.kind === "tweet" ? "purple" : "blue"}>
                      {r.kind}
                    </Badge>
                  </td>
                  <td className="px-3 py-3">
                    {r.handle ? (
                      <a href={`https://x.com/${r.handle}`} target="_blank" rel="noopener noreferrer"
                         className="text-accent-blue hover:underline">@{r.handle}</a>
                    ) : <span className="font-mono text-xs text-text-dim">{r.link?.slice(0, 24)}</span>}
                    {!r.resolved && r.handle && (
                      <span className="ml-2 text-xs text-accent-amber">not found</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <Badge variant="green">{r.verified_type || "verified"}</Badge>
                  </td>
                  <td className="px-3 py-3 text-text-muted">
                    {r.followers ? Number(r.followers).toLocaleString() : "—"}
                  </td>
                  <td className="px-3 py-3">
                    {r.post_found
                      ? <span className="text-accent-green">{r.post_source}
                          {r.post_age_minutes != null && (
                            <span className="text-text-dim"> · {r.post_age_minutes}m</span>
                          )}
                        </span>
                      : <span className="text-text-dim">—</span>}
                  </td>
                  {/* The text is what the link says, so it is the link. Reading
                      it and opening it were two separate hunts across the row. */}
                  <td className="max-w-[300px] px-3 py-3 text-text-muted">
                    {r.link ? (
                      <a href={r.link} target="_blank" rel="noopener noreferrer"
                         title={r.link} className="hover:underline">
                        <Highlighted text={r.excerpt || ""} rx={rx} />
                      </a>
                    ) : <Highlighted text={r.excerpt || ""} rx={rx} />}
                  </td>
                  <td className="px-3 py-3">
                    <span className="font-mono text-xs text-text-muted">
                      {r.open_timestamp ? fmtDateTime(r.open_timestamp) : "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      {/* The count in the header is the whole filter; this is how the table
          catches up with it. */}
      {onMore && typeof total === "number" && items.length < total && (
        <div className="mt-3 flex items-center justify-center gap-3 text-xs">
          <span className="text-text-dim">
            showing {items.length.toLocaleString()} of {total.toLocaleString()}
          </span>
          <Button size="sm" variant="outline" onClick={onMore}>
            Load {Math.min(PAGE, total - items.length).toLocaleString()} more
          </Button>
        </div>
      )}
    </>
  );
}
