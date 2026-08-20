"use client";

import { useState } from "react";
import { mutate } from "swr";
import { Loader2, Plus } from "lucide-react";
import { apiSend } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Add a premium caller group by whatever the user has to hand: the group's
   name, its @username, a t.me link, or the raw chat id. Resolution happens
   server-side through the userbot, which is the only thing that can see a
   private group — and it takes effect on the running server, no restart.

   Shared by the Forwarder page and the second dashboard: one add box, one set
   of rules, so a group added from either place behaves identically. */

export function AddPremiumGroup({ className = "mb-3" }: { className?: string }) {
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function add() {
    const value = val.trim();
    if (!value) return;
    setBusy(true);
    setMsg(null);
    try {
      const r: any = await apiSend("/api/forwarder/groups", "POST", { value });
      setVal("");
      // Same three details the chat-id finder gives back, so what was added is
      // identifiable without hunting for the row.
      const bits = [r.name, r.username ? `@${r.username}` : null, `-100${r.id}`]
        .filter(Boolean).join(" · ");
      setMsg({
        ok: true,
        text: r.name ? `Added ${bits} — live now`
                     : `Added -100${r.id} — live now. Telegram would not give a `
                       + `title; it will fill in when the group next posts.`,
      });
      mutate("/api/forwarder/sources");
      mutate("/api/forwarder/stats");
      mutate((k) => typeof k === "string" && k.startsWith("/api/calls"));
    } catch (e: any) {
      setMsg({ ok: false, text: String(e?.message || e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={className}>
      <div className="flex gap-2">
        <Input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          placeholder="Group name  ·  @username  ·  t.me/…  ·  -100123…"
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button variant="primary" size="sm" disabled={busy || !val.trim()} onClick={add}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
        </Button>
      </div>
      {msg && (
        <p className={`mt-1.5 text-[11px] ${msg.ok ? "text-accent-green" : "text-accent-red"}`}>
          {msg.text}
        </p>
      )}
    </div>
  );
}
