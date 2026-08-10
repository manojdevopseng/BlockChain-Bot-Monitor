"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { mutate } from "swr";
import { useApi, apiSend } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* One list of @usernames: add, remove, and what the list does written on it.
 *
 * Shared because two panels have the same feature over different entries —
 * the X Monitor's lists live under /api/rbhx, the Launchpad Monitor's under
 * /api/launchpad, and `base` is the only difference between them. Both back
 * ends answer the same three routes and expire entries the same way. */

type Entry = { handle: string; note: string; added_at: number; expires_in_days: number };

export function HandleList({ base, kind, title, hint, icon }: {
  base: string;                 // "/api/rbhx" | "/api/launchpad"
  kind: "skip" | "watch";
  title: string;
  hint: string;
  icon: React.ReactNode;
}) {
  const path = `${base}/${kind}`;
  const { data } = useApi<any>(path);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const items: Entry[] = data?.items ?? [];

  async function add() {
    const handle = val.trim();
    if (!handle) return;
    setBusy(true); setErr("");
    try {
      await apiSend(path, "POST", { handle });
      setVal("");
      mutate(path);
    } catch (e: any) {
      setErr(e?.message || "could not add that username");
    } finally {
      setBusy(false);
    }
  }

  async function remove(handle: string) {
    await apiSend(`${path}/${handle}`, "DELETE");
    mutate(path);
  }

  return (
    <div className="rounded-lg border border-border-soft p-3">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-text">
        {icon} {title}
        <Badge variant="gray">{items.length}</Badge>
      </div>
      <p className="mb-2 text-[11px] text-text-dim">{hint}</p>
      <div className="mb-2 flex gap-1.5">
        <Input value={val} onChange={(e) => setVal(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && add()}
               placeholder="@username" className="h-7 text-xs" />
        <Button size="sm" onClick={add} disabled={busy}>Add</Button>
      </div>
      {err && <div className="mb-2 text-[11px] text-accent-red">{err}</div>}
      <div className="flex flex-wrap gap-1">
        {items.map((e) => (
          <span key={e.handle}
                title={`expires in ${e.expires_in_days} days unless removed sooner`}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-white/5 px-2 py-0.5 text-[11px] text-text-muted">
            @{e.handle}
            <span className="text-text-dim">{e.expires_in_days}d</span>
            <button onClick={() => remove(e.handle)} className="hover:text-accent-red">
              <X size={10} />
            </button>
          </span>
        ))}
        {items.length === 0 && <span className="text-[11px] text-text-dim">empty</span>}
      </div>
    </div>
  );
}
